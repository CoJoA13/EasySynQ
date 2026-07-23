"""S-pack-1 integration proofs — evidence-pack preview + immutable build/seal, over HTTP + the
worker ``build`` driven directly (the disposition-test contract: never via Celery/Beat on shared).

Isolation: every pack is **PROCESS-scoped to a per-test Process** + its own evidence-for links, so
resolution can only ever match this test's records (the shared DB accumulates rows). The R28 matrix
is the load-bearing proof — a denied record surfaces as EXCLUDED_PERMISSION, a tombstoned record
as EXCLUDED_ABSENCE, a form-only (evidence-less) record stays INCLUDED — none silently dropped.
Teardown is in ``finally`` in FK-RESTRICT order (pack → record children → records → blobs → authz).
"""

from __future__ import annotations

import datetime
import io
import uuid
import zipfile
from collections.abc import Callable

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models._pack_enums import PackStatus
from easysynq_api.db.models._retention_enums import DispositionAction
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.disposition_event import DispositionEvent
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.evidence_blob import EvidenceBlob
from easysynq_api.db.models.evidence_for_link import EvidenceForLink
from easysynq_api.db.models.evidence_pack import EvidencePack
from easysynq_api.db.models.pack_item import PackItem
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.process import Process
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.retention_policy import RetentionPolicy
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.packs import build
from easysynq_api.services.packs import repository as packs_repo

from ._owner_db import owner_delete_disposition_events
from .test_records import _capture, _grant, _subject, _upload_evidence
from .test_vault import _auth

pytestmark = pytest.mark.integration

_PACK_PERMS = ("report.evidence_pack.generate", "report.export", "record.read", "record.create")


async def _make_process(user_id: uuid.UUID, name: str) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, user_id)
        assert user is not None
        proc = Process(org_id=user.org_id, name=name, pdca_phase=PdcaPhase.DO, created_by=user.id)
        s.add(proc)
        await s.commit()
        return proc.id


async def _link_process(
    client: AsyncClient, h: dict[str, str], record_id: str, process_id: uuid.UUID
) -> None:
    r = await client.post(
        f"/api/v1/records/{record_id}/evidence-links",
        headers=h,
        json={"target_type": "process", "target_id": str(process_id)},
    )
    assert r.status_code == 201, r.text


async def _deny_record_read(record_id: str, user_id: uuid.UUID) -> uuid.UUID:
    """An ARTIFACT-scope DENY of ``record.read`` on one record (deny-wins over the SYSTEM ALLOW) —
    proves the R28 EXCLUSION classifier denies a *folderless* record by its artifact id."""
    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, user_id)
        assert user is not None
        perm = (
            await s.execute(select(Permission).where(Permission.key == "record.read"))
        ).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel.ARTIFACT, selector={"artifact_id": str(record_id)}
        )
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.DENY,
                scope_id=scope.id,
            )
        )
        await s.commit()
        return scope.id


async def _destroy_tombstone(record_id: str, user_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, user_id)
        assert user is not None
        s.add(
            DispositionEvent(
                org_id=user.org_id,
                record_id=uuid.UUID(record_id),
                action=DispositionAction.DESTROY,
                tombstone=True,
            )
        )
        await s.commit()


async def _seal(pack_id: uuid.UUID) -> None:
    """Drive the worker build directly (BUILDING → build()), not via Celery/Beat."""
    async with get_sessionmaker()() as s:
        pack = await packs_repo.get_pack(s, pack_id, for_update=True)
        assert pack is not None
        pack.status = PackStatus.BUILDING
        pack.build_started_at = datetime.datetime.now(datetime.UTC)
        await s.commit()
    async with get_sessionmaker()() as s:
        await build(s, pack_id)


async def _teardown(
    *,
    record_ids: list[str],
    pack_id: uuid.UUID | None,
    scope_id: uuid.UUID | None,
    process_id: uuid.UUID,
) -> None:
    async with get_sessionmaker()() as s:
        recs = [uuid.UUID(r) for r in record_ids]
        if pack_id is not None:
            pack = await s.get(EvidencePack, pack_id)
            if pack is not None and pack.pack_record_id is not None:
                recs.append(pack.pack_record_id)
            await s.execute(delete(PackItem).where(PackItem.pack_id == pack_id))
            await s.execute(delete(EvidencePack).where(EvidencePack.id == pack_id))
        if recs:
            await s.execute(delete(EvidenceForLink).where(EvidenceForLink.record_id.in_(recs)))
            # disposition_event is append-only for the app role (0072) → delete it as the OWNER.
            await owner_delete_disposition_events(recs)
            shas = list(
                (
                    await s.execute(
                        select(EvidenceBlob.blob_sha256).where(EvidenceBlob.record_id.in_(recs))
                    )
                )
                .scalars()
                .all()
            )
            await s.execute(delete(EvidenceBlob).where(EvidenceBlob.record_id.in_(recs)))
            await s.execute(delete(Record).where(Record.id.in_(recs)))
            await s.execute(delete(DocumentedInformation).where(DocumentedInformation.id.in_(recs)))
            if shas:
                await s.execute(delete(Blob).where(Blob.sha256.in_(shas)))
        if scope_id is not None:
            await s.execute(
                delete(PermissionOverride).where(PermissionOverride.scope_id == scope_id)
            )
            await s.execute(delete(Scope).where(Scope.id == scope_id))
        await s.execute(delete(Process).where(Process.id == process_id))
        await s.commit()


async def test_pack_build_seal_r28_matrix_and_download(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("pack")
    user_id = await _grant(subject, _PACK_PERMS)
    h = _auth(token_factory, subject)
    process_id = await _make_process(user_id, f"Proc-{uuid.uuid4().hex[:8]}")

    # INCLUDED (evidence) + INCLUDED (form-only) + EXCLUDED_PERMISSION + EXCLUDED_ABSENCE.
    content = f"evidence-{uuid.uuid4().hex}".encode()
    sha = await _upload_evidence(app_client, h, content)
    r_inc = (
        await _capture(
            app_client,
            h,
            record_type="EVIDENCE",
            title="included",
            evidence=[{"sha256": sha, "content_type": "application/pdf"}],
        )
    ).json()["id"]
    r_form = (
        await _capture(
            app_client,
            h,
            record_type="COMPETENCE",
            title="form only",
            form_field_values={"score": 9},
        )
    ).json()["id"]
    r_perm = (await _capture(app_client, h, record_type="EVIDENCE", title="denied")).json()["id"]
    r_absent = (await _capture(app_client, h, record_type="EVIDENCE", title="destroyed")).json()[
        "id"
    ]
    record_ids = [r_inc, r_form, r_perm, r_absent]

    scope_id: uuid.UUID | None = None
    pack_uuid: uuid.UUID | None = None
    try:
        for rid in record_ids:
            await _link_process(app_client, h, rid, process_id)
        scope_id = await _deny_record_read(r_perm, user_id)
        await _destroy_tombstone(r_absent, user_id)

        # Preview: 4 candidates, classified.
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={
                "title": "Q1 process pack",
                "scope_kind": "PROCESS",
                "process_ids": [str(process_id)],
            },
        )
        assert created.status_code == 201, created.text
        pack = created.json()
        pack_uuid = uuid.UUID(pack["id"])
        assert pack["status"] == "DRAFT"
        statuses = {i["record_id"]: i["inclusion_status"] for i in pack["items"] if i["record_id"]}
        assert statuses[r_inc] == "INCLUDED"
        assert statuses[r_form] == "INCLUDED"
        assert statuses[r_perm] == "EXCLUDED_PERMISSION"
        assert statuses[r_absent] == "EXCLUDED_ABSENCE"

        # Build/seal, then poll.
        await _seal(pack_uuid)
        got = await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}", headers=h)
        assert got.status_code == 200, got.text
        sealed = got.json()
        assert sealed["status"] == "SEALED"
        assert sealed["content_hash"].startswith("sha256:")
        assert sealed["pack_record_id"] is not None
        assert sealed["item_count"] == 2  # 2 included records, no pinned versions (ad-hoc EVIDENCE)
        # R28: the gap report (clause coverage) is DISTINCT from the exclusion report.
        assert sealed["exclusion_summary"]["permission_count"] == 1
        assert sealed["exclusion_summary"]["absence_count"] == 1
        assert r_perm in sealed["exclusion_summary"]["permission"]
        assert r_absent in sealed["exclusion_summary"]["absence"]
        assert "clauses" in sealed["gap_summary"] and "permission" not in sealed["gap_summary"]

        # The pack is itself a RETAIN_PERMANENT EVIDENCE Record.
        async with get_sessionmaker()() as s:
            pack_rec = await s.get(Record, uuid.UUID(sealed["pack_record_id"]))
            assert pack_rec is not None and pack_rec.record_type.value == "EVIDENCE"
            policy = await s.get(RetentionPolicy, pack_rec.retention_policy_id)
            assert (
                policy is not None
                and policy.disposition_action == DispositionAction.RETAIN_PERMANENT
            )

        # Download the sealed ZIP and verify its contents.
        dl = await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}/download", headers=h)
        assert dl.status_code == 200, dl.text
        async with httpx.AsyncClient(timeout=30) as raw:
            fetched = await raw.get(dl.json()["download_url"])
            assert fetched.status_code == 200
        with zipfile.ZipFile(io.BytesIO(fetched.content)) as zf:
            names = set(zf.namelist())
            assert {
                "cover.txt",
                "manifest.json",
                "gap_report.json",
                "exclusion_report.json",
            } <= names
            assert any(
                n.startswith(f"records/{r_inc}/") for n in names
            )  # the included evidence file
            assert not any(n.startswith(f"records/{r_perm}/") for n in names)  # excluded → no bytes
    finally:
        await _teardown(
            record_ids=record_ids, pack_id=pack_uuid, scope_id=scope_id, process_id=process_id
        )


async def test_process_pack_includes_source_less_correction(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """CX-2: a source-LESS correction of a process-linked record is a PROCESS pack candidate (it
    inherits the original's process via ``correction_of``), so the PROCESS evidence pack includes it
    exactly as ``/records`` shows it — the candidate query walks the correction chain."""
    subject = _subject("pack-cx2")
    user_id = await _grant(subject, _PACK_PERMS)
    h = _auth(token_factory, subject)
    process_id = await _make_process(user_id, f"Proc-{uuid.uuid4().hex[:8]}")
    record_ids: list[str] = []
    pack_uuid: uuid.UUID | None = None
    try:
        sha = await _upload_evidence(app_client, h, f"ev-{uuid.uuid4().hex}".encode())
        orig = await _capture(
            app_client,
            h,
            record_type="EVIDENCE",
            title="orig",
            evidence=[{"sha256": sha, "content_type": "application/pdf"}],
        )
        assert orig.status_code == 201, orig.text
        original = orig.json()["id"]
        await _link_process(app_client, h, original, process_id)  # leg A → the process
        # A source-less correction copies no evidence link → it inherits the process only via the
        # correction chain (not its own binding).
        csha = await _upload_evidence(app_client, h, f"corr-{uuid.uuid4().hex}".encode())
        corr = await app_client.post(
            f"/api/v1/records/{original}/correction",
            headers=h,
            json={
                "record_type": "EVIDENCE",
                "title": "corrected",
                "evidence": [{"sha256": csha, "content_type": "application/pdf"}],
            },
        )
        assert corr.status_code == 201, corr.text
        successor = corr.json()["id"]
        record_ids = [original, successor]

        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": "cx2 pack", "scope_kind": "PROCESS", "process_ids": [str(process_id)]},
        )
        assert created.status_code == 201, created.text
        pack = created.json()
        pack_uuid = uuid.UUID(pack["id"])
        statuses = {i["record_id"]: i["inclusion_status"] for i in pack["items"] if i["record_id"]}
        assert statuses.get(original) == "INCLUDED"
        # The source-less correction is a candidate AND included (omitted entirely without CX-2).
        assert statuses.get(successor) == "INCLUDED", f"correction omitted from pack: {statuses}"
    finally:
        # Break the correction RESTRICT cycle (correction_of <-> superseded_by_correction) so the
        # bulk delete in _teardown can drop both rows.
        if record_ids:
            async with get_sessionmaker()() as s:
                for rid in record_ids:
                    rec = await s.get(Record, uuid.UUID(rid))
                    if rec is not None:
                        rec.correction_of = None
                        rec.superseded_by_correction = None
                await s.commit()
        await _teardown(
            record_ids=record_ids, pack_id=pack_uuid, scope_id=None, process_id=process_id
        )


async def test_pack_download_409_until_sealed_and_generate_guards(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("pack")
    user_id = await _grant(subject, _PACK_PERMS)
    h = _auth(token_factory, subject)
    process_id = await _make_process(user_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    try:
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": "empty pack", "scope_kind": "PROCESS", "process_ids": [str(process_id)]},
        )
        assert created.status_code == 201, created.text
        pack_uuid = uuid.UUID(created.json()["id"])

        # Download is 409 while DRAFT (not yet sealed).
        early = await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}/download", headers=h)
        assert early.status_code == 409, early.text

        # Seal, then a second generate is 409 (SEALED is terminal).
        await _seal(pack_uuid)
        regen = await app_client.post(f"/api/v1/evidence-packs/{pack_uuid}/generate", headers=h)
        assert regen.status_code == 409, regen.text

        # Now download works.
        ok = await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}/download", headers=h)
        assert ok.status_code == 200, ok.text
    finally:
        await _teardown(record_ids=[], pack_id=pack_uuid, scope_id=None, process_id=process_id)


async def test_pack_validation_unknown_process_404(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("pack")
    await _grant(subject, _PACK_PERMS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/evidence-packs",
        headers=h,
        json={"title": "bad", "scope_kind": "PROCESS", "process_ids": [str(uuid.uuid4())]},
    )
    assert r.status_code == 422, r.text


async def test_pack_authz_403_without_generate(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("pack")
    await _grant(subject, ("report.export",))  # export but NOT generate
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/evidence-packs",
        headers=h,
        json={"title": "denied", "scope_kind": "CLAUSE", "clause_ids": [str(uuid.uuid4())]},
    )
    assert r.status_code == 403, r.text
