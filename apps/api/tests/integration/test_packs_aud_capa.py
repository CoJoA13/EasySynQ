"""S-aud-capa-pack integration proofs — Evidence-Pack FINDING/CAPA scope + the synthesized dossier.

The headline is ``test_capa_scope_pack_proves_closed_effectively``: an audit NC's auto-CAPA is
driven to Closed via the REAL path (containment → RCA → approved plan → implement → verify → close,
with distinct implementer/verifier), then a CAPA-scope pack bundles a sealed dossier whose stage
trail + e-signatures + effectiveness evidence let an auditor "prove this NC was closed effectively"
(doc 06 §7.1). The dossier carries NO PII (only ``{user_id, display_name}``); the finding/CAPA
SUBJECT is a dossier subject, never a phantom pack_item record.

Isolation follows the family pattern: UUID-salted subjects, assertions scoped to this run's own ids,
and teardown that touches ONLY pack-tier rows + the evidence records this test created — NEVER the
capa_stage / capa / audit_finding / audit ancestry (``easysynq_app`` has DELETE revoked on
capa_stage and capa_stage→capa is RESTRICT; those rows stay, harmless under per-subject isolation).
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import Callable

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from easysynq_api.db.models._pack_enums import PackStatus
from easysynq_api.db.models._retention_enums import DispositionAction
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.evidence_blob import EvidenceBlob
from easysynq_api.db.models.evidence_for_link import EvidenceForLink
from easysynq_api.db.models.evidence_pack import EvidencePack
from easysynq_api.db.models.pack_item import PackItem
from easysynq_api.db.models.pack_share_link import PackShareLink
from easysynq_api.db.models.record import Record
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.packs import build_and_cache_portfolio
from easysynq_api.services.packs import repository as packs_repo
from easysynq_api.services.records import disposition

from ._owner_db import owner_delete_disposition_events
from .test_audits import _new_audit, _walk
from .test_capa import _ACTION_PLAN, _assign_seeded_role, _latest_stage_id, _my_pending_task
from .test_packs import _seal
from .test_records import _capture, _grant, _subject, _upload_evidence
from .test_vault import _auth

pytestmark = pytest.mark.integration

_AUDIT_KEYS = ("audit.read", "audit.plan", "audit.create", "audit.conduct", "audit.close")
_PACK_KEYS = ("report.evidence_pack.generate", "report.export", "record.read", "record.create")


async def _evidence_record(client: AsyncClient, h: dict[str, str], title: str) -> str:
    """Upload a real WORM-sealed evidence blob + capture it as an EVIDENCE record; return its id."""
    sha = await _upload_evidence(client, h, f"{title}-{uuid.uuid4().hex}".encode())
    r = await _capture(
        client,
        h,
        record_type="EVIDENCE",
        title=title,
        evidence=[{"sha256": sha, "content_type": "application/pdf"}],
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _link(client: AsyncClient, h: dict[str, str], rid: str, ttype: str, tid: str) -> None:
    r = await client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=h,
        json={"target_type": ttype, "target_id": tid},
    )
    assert r.status_code == 201, r.text


async def _download_zip(client: AsyncClient, h: dict[str, str], pack_id: uuid.UUID) -> bytes:
    dl = await client.get(f"/api/v1/evidence-packs/{pack_id}/download", headers=h)
    assert dl.status_code == 200, dl.text
    async with httpx.AsyncClient(timeout=30) as raw:
        fetched = await raw.get(dl.json()["download_url"])
        assert fetched.status_code == 200
    return fetched.content


async def _teardown(record_ids: list[str], pack_id: uuid.UUID | None) -> None:
    """Drop ONLY pack-tier rows + the evidence records this test created. The capa/audit ancestry is
    left in place (DELETE is revoked on capa_stage for the app role; RESTRICT blocks a capa del)."""
    async with get_sessionmaker()() as s:
        recs = [uuid.UUID(r) for r in record_ids]
        portfolio_sha: str | None = None
        if pack_id is not None:
            pack = await s.get(EvidencePack, pack_id)
            if pack is not None:
                if pack.pack_record_id is not None:
                    recs.append(pack.pack_record_id)
                portfolio_sha = pack.portfolio_blob_sha256
            # share-links → evidence_pack is RESTRICT, so drop the links before the pack.
            await s.execute(delete(PackShareLink).where(PackShareLink.pack_id == pack_id))
            await s.execute(delete(PackItem).where(PackItem.pack_id == pack_id))
            await s.execute(delete(EvidencePack).where(EvidencePack.id == pack_id))
            if portfolio_sha is not None:
                await s.execute(delete(Blob).where(Blob.sha256 == portfolio_sha))
        if recs:
            await s.execute(delete(EvidenceForLink).where(EvidenceForLink.record_id.in_(recs)))
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
        await s.commit()


async def test_finding_scope_pack_bundles_dossier(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("fpack")
    keys = (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS)
    await _grant(subject, keys)
    h = _auth(token_factory, subject)

    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    f = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Major", "clause_ref": "8.4"},
        )
    ).json()
    finding_id, auto_capa_id = f["id"], f["auto_capa_id"]

    ev_id = await _evidence_record(app_client, h, "finding evidence")
    await _link(app_client, h, ev_id, "finding", finding_id)

    pack_uuid: uuid.UUID | None = None
    try:
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": "NC pack", "scope_kind": "FINDING", "finding_ids": [finding_id]},
        )
        assert created.status_code == 201, created.text
        pack = created.json()
        pack_uuid = uuid.UUID(pack["id"])
        assert pack["gap_summary"]["applicable"] is False  # no clause-coverage gap for findings
        rec_items = {i["record_id"] for i in pack["items"] if i["record_id"]}
        assert ev_id in rec_items  # the linked evidence record is a pack_item
        assert finding_id not in rec_items  # the SUBJECT finding is a dossier subject, not a record

        await _seal(pack_uuid)
        sealed = (await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}", headers=h)).json()
        assert sealed["status"] == "SEALED"
        assert sealed["content_hash"].startswith("sha256:")

        with zipfile.ZipFile(io.BytesIO(await _download_zip(app_client, h, pack_uuid))) as zf:
            names = set(zf.namelist())
            assert any(n.startswith(f"records/{ev_id}/") for n in names)  # the evidence file
            dossier_names = [n for n in names if n.startswith("findings/")]
            assert len(dossier_names) == 1, names
            d = json.loads(zf.read(dossier_names[0]))
            assert d["kind"] == "finding" and d["id"] == finding_id
            assert d["finding_type"] == "NC" and d["severity"] == "Major"
            assert d["clause_ref"] == "8.4"
            assert d["linked_capa"]["id"] == auto_capa_id
            assert d["audit"]["id"] == audit_id
            assert any(e["record_id"] == ev_id for e in d["evidence_records"])
            assert set(d["captured_by"].keys()) == {"user_id", "display_name"}

            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["scope_kind"] == "FINDING"
            assert manifest["dossier"]["digest"].startswith("sha256:")
            assert dossier_names[0] in {fm["path"] for fm in manifest["dossier"]["files"]}
            assert any(sub["id"] == finding_id for sub in manifest["dossier_subjects"])
            assert json.loads(zf.read("gap_report.json"))["applicable"] is False
            # No PII: the dossier emits only {user_id, display_name} — never an email or a
            # keycloak_subject field (the structural project_user boundary).
            blob = zf.read(dossier_names[0]).decode()
            assert '"email"' not in blob and '"keycloak_subject"' not in blob
    finally:
        await _teardown([ev_id], pack_uuid)


async def test_sealed_finding_pack_refuses_serving_after_subject_destroyed(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Ti85C (R27/R28): a FINDING/CAPA pack's SUBJECT is a *dossier* subject, NOT a pack_item — but
    it IS a record (``audit_finding``/``capa`` are shared-PK RECORD subtypes), so it can be
    DESTROYed. When the subject is destroyed after sealing, the cached ZIP still carries its
    narrative, so every serve path must fail-closed on the SUBJECT tombstone too (not only on
    destroyed evidence MEMBERS). The linked evidence stays INTACT here, so only the subject-destroy
    can trip the gate. Mutation-verify: without the subject branch in ``pack_has_destroyed_member``
    the paths keep serving the destroyed finding narrative (the member count is 0, the intact
    evidence never trips it)."""
    subject = _subject("subjdestroy")
    keys = (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS)
    await _grant(subject, keys)
    h = _auth(token_factory, subject)

    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    finding_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Major", "clause_ref": "8.4"},
        )
    ).json()["id"]
    ev_id = await _evidence_record(app_client, h, "intact evidence")
    await _link(app_client, h, ev_id, "finding", finding_id)

    pack_uuid: uuid.UUID | None = None
    try:
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": "NC pack", "scope_kind": "FINDING", "finding_ids": [finding_id]},
        )
        assert created.status_code == 201, created.text
        pack_uuid = uuid.UUID(created.json()["id"])
        await _seal(pack_uuid)

        token = (
            await app_client.post(
                f"/api/v1/evidence-packs/{pack_uuid}/share",
                headers=h,
                json={"recipient": "auditor"},
            )
        ).json()["token"]
        # BEFORE the subject destroy both the public landing and the authenticated download serve.
        assert (
            await app_client.get("/api/v1/evidence-packs/shared", params={"t": token})
        ).status_code == 200
        assert (
            await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}/download", headers=h)
        ).status_code == 200

        # DESTROY the SUBJECT finding (its shared-PK record), leaving the linked evidence intact.
        async with get_sessionmaker()() as s:
            finding_rec = await s.get(Record, uuid.UUID(finding_id))
            assert finding_rec is not None
            disposition._write_tombstone(
                s, finding_rec, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
            )
            await s.commit()

        # AFTER: the public paths fail-closed (403); the authenticated download 409s; minting a new
        # share is refused before it commits a dead token.
        assert (
            await app_client.get("/api/v1/evidence-packs/shared", params={"t": token})
        ).status_code == 403
        pub = await app_client.get(
            "/api/v1/evidence-packs/shared/download", params={"t": token, "format": "zip"}
        )
        assert pub.status_code == 403, pub.text
        auth = await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}/download", headers=h)
        assert auth.status_code == 409, auth.text
        assert auth.json()["code"] == "pack_evidence_destroyed"
        mint = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share", headers=h, json={"recipient": "auditor2"}
        )
        assert mint.status_code == 409, mint.text
        assert mint.json()["code"] == "pack_evidence_destroyed"
    finally:
        await owner_delete_disposition_events([uuid.UUID(finding_id)])
        await _teardown([ev_id], pack_uuid)


async def test_capa_pack_create_requires_origin_finding_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """New-1 (R28): a CAPA pack whose CAPA has an ORIGIN finding must ALSO re-authorize finding.read
    — the CAPA dossier embeds the origin finding's type/severity/summary/identifier (content the
    CAPA API does NOT expose), so a caller with capa.read but WITHOUT finding.read is REFUSED (403)
    at create. Mutation-verify: without the origin gate the capa.read-only caller gets 201."""
    owner = _subject("origin-owner")
    await _grant(owner, (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS))
    ho = _auth(token_factory, owner)
    audit_id = await _new_audit(app_client, ho)
    await _walk(app_client, ho, audit_id, "plan", "conduct")
    capa_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=ho,
            json={"finding_type": "NC", "severity": "Major", "clause_ref": "8.4"},
        )
    ).json()["auto_capa_id"]

    # Attacker: capa.read (the CAPA subject, granted SYSTEM so the process scope passes) + the pack
    # keys, but NOT finding.read (the embedded origin).
    attacker = _subject("origin-attacker")
    await _grant(attacker, ("capa.read", "report.evidence_pack.generate", "report.export"))
    ha = _auth(token_factory, attacker)
    refused = await app_client.post(
        "/api/v1/evidence-packs",
        headers=ha,
        json={"title": "C", "scope_kind": "CAPA", "capa_ids": [capa_id]},
    )
    assert refused.status_code == 403, refused.text

    # The owner (WITH finding.read for the origin) still creates the pack — the gate keeps the path.
    ok = await app_client.post(
        "/api/v1/evidence-packs",
        headers=ho,
        json={"title": "C-ok", "scope_kind": "CAPA", "capa_ids": [capa_id]},
    )
    assert ok.status_code == 201, ok.text
    await _teardown([], uuid.UUID(ok.json()["id"]))


async def test_sealed_pack_serve_guard_covers_embedded_records(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """New-2 / New-3 + the FINDING→auto_capa mirror + the CAPA-subject branch: the serve guard
    (``pack_has_destroyed_member``) fails-closed when ANY dossier-embedded / derived record is
    destroyed — not only an INCLUDED pack_item member. The CAPA dossier embeds its origin finding,
    the finding dossier embeds its linked auto-CAPA, and every sealed pack carries its own
    registered EVIDENCE record (pack_record) — none are pack_item rows. Repo-level matrix: each pack
    is False at seal and True once its OWN embedded record gets a DESTROY tombstone
    (mutation-distinguishing — dropping each branch flips its assertion). The three DRAFT packs are
    checked at the repo level (the download endpoint 409s on unsealed BEFORE the destroyed-member
    check); pk_record is sealed, so its endpoint 409 proves the (scope-agnostic) serve wiring."""
    owner = _subject("embedded")
    await _grant(owner, (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS))
    h = _auth(token_factory, owner)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")

    async def _nc() -> tuple[str, str]:
        f = (
            await app_client.post(
                f"/api/v1/audits/{audit_id}/findings",
                headers=h,
                json={"finding_type": "NC", "severity": "Major"},
            )
        ).json()
        return f["id"], f["auto_capa_id"]

    async def _create(scope: str, key: str, sid: str) -> uuid.UUID:
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": scope, "scope_kind": scope, key: [sid]},
        )
        assert created.status_code == 201, created.text
        return uuid.UUID(created.json()["id"])

    async def _guard(pid: uuid.UUID) -> bool:
        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pid)
            assert pack is not None
            return await packs_repo.pack_has_destroyed_member(s, pack)

    async def _destroy(rid: uuid.UUID) -> None:
        async with get_sessionmaker()() as s:
            rec = await s.get(Record, rid)
            assert rec is not None
            disposition._write_tombstone(
                s, rec, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
            )
            await s.commit()

    f1, c1 = await _nc()
    _f2, c2 = await _nc()
    f3, c3 = await _nc()
    f4, _c4 = await _nc()
    # The subject / origin / linked-CAPA branches read scope_selector + cross-refs (populated at
    # create), so those three packs are asserted at the repo level while DRAFT; only New-3 needs a
    # pack_record, so pk_record is sealed — with an evidence member, so no dossier-only seal is
    # assumed. The existing subject test covers the sealed download path end-to-end.
    pk_origin = await _create("CAPA", "capa_ids", c1)  # → destroy c1's ORIGIN finding f1  (New-2)
    pk_subject = await _create("CAPA", "capa_ids", c2)  # → destroy the CAPA SUBJECT c2
    pk_mirror = await _create("FINDING", "finding_ids", f3)  # → destroy f3's LINKED capa (mirror)
    ev4 = await _evidence_record(app_client, h, "pk-record evidence")
    await _link(app_client, h, ev4, "finding", f4)
    pk_record = await _create("FINDING", "finding_ids", f4)  # → destroy the PACK_RECORD   (New-3)
    await _seal(pk_record)
    packs = [pk_origin, pk_subject, pk_mirror, pk_record]
    pr4: uuid.UUID | None = None
    try:
        for pid in packs:
            assert await _guard(pid) is False, pid

        # New-2: a CAPA pack's origin finding destroyed (subject CAPA c1 intact).
        await _destroy(uuid.UUID(f1))
        assert await _guard(pk_origin) is True

        # CAPA-subject branch: the CAPA subject itself destroyed (origin finding f2 intact).
        await _destroy(uuid.UUID(c2))
        assert await _guard(pk_subject) is True

        # Mirror: a FINDING pack's linked auto-CAPA destroyed (subject finding f3 intact).
        await _destroy(uuid.UUID(c3))
        assert await _guard(pk_mirror) is True

        # New-3: the pack's own registered EVIDENCE record (the sealed ZIP-as-record) destroyed.
        async with get_sessionmaker()() as s:
            p4 = await s.get(EvidencePack, pk_record)
            assert p4 is not None and p4.pack_record_id is not None
            pr4 = p4.pack_record_id
        await _destroy(pr4)
        assert await _guard(pk_record) is True
        dl4 = await app_client.get(f"/api/v1/evidence-packs/{pk_record}/download", headers=h)
        assert dl4.status_code == 409, dl4.text
        assert dl4.json()["code"] == "pack_evidence_destroyed"
    finally:
        tombstoned = [uuid.UUID(f1), uuid.UUID(c2), uuid.UUID(c3)]
        if pr4 is not None:
            tombstoned.append(pr4)
        await owner_delete_disposition_events(tombstoned)
        for pid in (pk_origin, pk_subject, pk_mirror):
            await _teardown([], pid)
        await _teardown([ev4], pk_record)


async def test_finding_pack_create_requires_linked_capa_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """New-4 (R28): a FINDING pack whose finding has a LINKED auto-CAPA must ALSO re-authorize
    capa.read — the finding dossier embeds the linked CAPA's identifier + close_state (data GET
    /findings/{id} does NOT expose), so a caller with finding.read but WITHOUT capa.read is REFUSED
    (403) at create (the symmetric partner of the CAPA-pack origin-finding gate). Mutation-verify:
    without the linked-CAPA gate the finding.read-only caller gets 201."""
    owner = _subject("linked-owner")
    await _grant(owner, (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS))
    ho = _auth(token_factory, owner)
    audit_id = await _new_audit(app_client, ho)
    await _walk(app_client, ho, audit_id, "plan", "conduct")
    finding_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=ho,
            json={"finding_type": "NC", "severity": "Major"},
        )
    ).json()["id"]

    # Attacker: finding.read (the subject, granted SYSTEM) + the pack keys, but NOT capa.read.
    attacker = _subject("linked-attacker")
    await _grant(attacker, ("finding.read", "report.evidence_pack.generate", "report.export"))
    ha = _auth(token_factory, attacker)
    refused = await app_client.post(
        "/api/v1/evidence-packs",
        headers=ha,
        json={"title": "F", "scope_kind": "FINDING", "finding_ids": [finding_id]},
    )
    assert refused.status_code == 403, refused.text

    ok = await app_client.post(
        "/api/v1/evidence-packs",
        headers=ho,
        json={"title": "F-ok", "scope_kind": "FINDING", "finding_ids": [finding_id]},
    )
    assert ok.status_code == 201, ok.text
    await _teardown([], uuid.UUID(ok.json()["id"]))


async def test_finding_pack_serve_guard_covers_source_audit(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """New-5: a FINDING pack's dossier embeds its SOURCE AUDIT's id + identifier, and ``audit`` is a
    shared-PK record — so a destroyed source audit must fail-close the serve guard (its narrative is
    baked into the sealed ZIP). Mutation-verify: without the audit branch in the embedded-id set the
    guard stays False (the finding + its CAPA are intact, so only the audit-destroy can trip it)."""
    owner = _subject("audit-serve")
    await _grant(owner, (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS))
    h = _auth(token_factory, owner)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    finding_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Major"},
        )
    ).json()["id"]
    created = await app_client.post(
        "/api/v1/evidence-packs",
        headers=h,
        json={"title": "F", "scope_kind": "FINDING", "finding_ids": [finding_id]},
    )
    assert created.status_code == 201, created.text
    pid = uuid.UUID(created.json()["id"])

    async def _guard() -> bool:
        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pid)
            assert pack is not None
            return await packs_repo.pack_has_destroyed_member(s, pack)

    try:
        assert await _guard() is False
        # Destroy the SOURCE AUDIT (a shared-PK record); the finding + its CAPA stay intact.
        async with get_sessionmaker()() as s:
            audit_rec = await s.get(Record, uuid.UUID(audit_id))
            assert audit_rec is not None
            disposition._write_tombstone(
                s, audit_rec, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
            )
            await s.commit()
        assert await _guard() is True
    finally:
        await owner_delete_disposition_events([uuid.UUID(audit_id)])
        await _teardown([], pid)


async def test_finding_capa_pack_create_requires_subject_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Batch 6 (R28): creating a FINDING/CAPA pack must re-authorize the SUBJECT's read at its own
    scope — a holder of report.evidence_pack.generate who can't finding.read/capa.read the subject
    is REFUSED (403), never handed the subject dossier the worker would serialize. The evidence
    candidates are already R28-filtered, but the subject is excluded from that candidate set.
    Mutation-verify: without the create-time subject gate the create returns 201."""
    # Owner (full keys) raises an audit finding (which auto-spawns a CAPA).
    owner = _subject("subjread-owner")
    await _grant(owner, (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS))
    ho = _auth(token_factory, owner)
    audit_id = await _new_audit(app_client, ho)
    await _walk(app_client, ho, audit_id, "plan", "conduct")
    f = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=ho,
            json={"finding_type": "NC", "severity": "Major", "clause_ref": "8.4"},
        )
    ).json()
    finding_id, capa_id = f["id"], f["auto_capa_id"]

    # Attacker holds the pack-generate authority but NOT finding.read / capa.read.
    attacker = _subject("subjread-attacker")
    await _grant(attacker, ("report.evidence_pack.generate", "report.export"))
    ha = _auth(token_factory, attacker)
    fp = await app_client.post(
        "/api/v1/evidence-packs",
        headers=ha,
        json={"title": "F", "scope_kind": "FINDING", "finding_ids": [finding_id]},
    )
    assert fp.status_code == 403, fp.text
    cp = await app_client.post(
        "/api/v1/evidence-packs",
        headers=ha,
        json={"title": "C", "scope_kind": "CAPA", "capa_ids": [capa_id]},
    )
    assert cp.status_code == 403, cp.text

    # The owner (WITH subject read) still creates the pack — the gate keeps the happy path.
    op = await app_client.post(
        "/api/v1/evidence-packs",
        headers=ho,
        json={"title": "F-ok", "scope_kind": "FINDING", "finding_ids": [finding_id]},
    )
    assert op.status_code == 201, op.text
    await _teardown([], uuid.UUID(op.json()["id"]))


async def test_generate_pack_rechecks_subject_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Batch 6 (finding-2 hardening, IijIP): the FINDING/CAPA subject read is re-authorized at
    GENERATE (request-aware), not just at create — so a generator who cannot read the subject can't
    seal the pack (a revoked grant, or a different generator, between create and seal). The worker
    build has no caller, so the last request-time gate is generate. Mutation-verify: without the
    generate re-check the seal is enqueued (202)."""
    owner = _subject("regen-owner")
    await _grant(owner, (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS))
    ho = _auth(token_factory, owner)
    audit_id = await _new_audit(app_client, ho)
    await _walk(app_client, ho, audit_id, "plan", "conduct")
    finding_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=ho,
            json={"finding_type": "NC", "severity": "Major", "clause_ref": "8.4"},
        )
    ).json()["id"]
    pack_uuid: uuid.UUID | None = None
    try:
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=ho,
            json={"title": "F", "scope_kind": "FINDING", "finding_ids": [finding_id]},
        )
        assert created.status_code == 201, created.text
        pack_uuid = uuid.UUID(created.json()["id"])
        # A generator holding report.evidence_pack.generate but NOT finding.read cannot seal it.
        gen_only = _subject("regen-genonly")
        await _grant(gen_only, ("report.evidence_pack.generate",))
        hg = _auth(token_factory, gen_only)
        gen = await app_client.post(f"/api/v1/evidence-packs/{pack_uuid}/generate", headers=hg)
        assert gen.status_code == 403, gen.text
        # The ALLOW direction is NOT asserted through the endpoint here: a 202 fires the real
        # build_evidence_pack.delay(), which the integration env has no Celery result backend for
        # (the other seals drive build() directly via _seal). It's covered transitively — the shared
        # _authorize_pack_subjects allow is proven by the create-time owner→201 test, and this
        # generate-time wiring reaching it is proven by the 403 above.
    finally:
        await _teardown([], pack_uuid)


async def test_capa_scope_pack_proves_closed_effectively(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The family headline: drive an NC's auto-CAPA to Closed via the real path with REAL evidence
    records on the Implement + Verify stages, then a CAPA pack's dossier proves the closure."""
    drv = _subject("cpack-drv")
    await _grant(
        drv,
        (
            *_AUDIT_KEYS,
            "finding.create",
            "finding.read",
            "capa.read",
            "capa.update",
            "capa.record_rca",
            "capa.plan_action",
            "capa.capture_effectiveness",
            *_PACK_KEYS,
        ),
    )
    h = _auth(token_factory, drv)
    qm = _subject("cpack-qm")
    await _assign_seeded_role(qm, "QMS Owner")  # the action-plan approval candidate pool (by role)
    hqm = _auth(token_factory, qm)
    ver = _subject("cpack-ver")
    await _grant(ver, ("capa.read", "capa.verify", "capa.close", "record.create", "record.read"))
    hver = _auth(token_factory, ver)

    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    capa_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Minor"},
        )
    ).json()["auto_capa_id"]

    await app_client.post(
        f"/api/v1/capas/{capa_id}/containment", headers=h, json={"content_block": {"c": "x"}}
    )
    await app_client.post(
        f"/api/v1/capas/{capa_id}/root-cause",
        headers=h,
        json={"content_block": {"root_cause": "rc"}},
    )
    iid = (
        await app_client.post(
            f"/api/v1/capas/{capa_id}/action-plan", headers=h, json={"content_block": _ACTION_PLAN}
        )
    ).json()["approval_instance"]["id"]
    task_id = await _my_pending_task(app_client, hqm, iid)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hqm, json={"outcome": "approve"}
    )
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=h, json={"content_block": {"done": "x"}}
    )
    impl_stage = await _latest_stage_id(app_client, h, capa_id, "Implement")
    impl_ev = await _evidence_record(app_client, h, "impl evidence")
    await _link(app_client, h, impl_ev, "capa_stage", impl_stage)

    await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hver,
        json={"decision": "effective", "content_block": {"c": "x"}},
    )
    ver_stage = await _latest_stage_id(app_client, hver, capa_id, "Verify")
    eff_ev = await _evidence_record(app_client, hver, "effectiveness evidence")
    await _link(app_client, hver, eff_ev, "capa_stage", ver_stage)
    closed = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hver)
    assert closed.status_code == 200 and closed.json()["close_state"] == "Closed", closed.text

    pack_uuid: uuid.UUID | None = None
    try:
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": "CAPA closure pack", "scope_kind": "CAPA", "capa_ids": [capa_id]},
        )
        assert created.status_code == 201, created.text
        pack_uuid = uuid.UUID(created.json()["id"])
        rec_items = {i["record_id"] for i in created.json()["items"] if i["record_id"]}
        assert impl_ev in rec_items and eff_ev in rec_items  # both stage-evidence records resolved
        assert capa_id not in rec_items  # the CAPA SUBJECT is a dossier subject, not a record

        await _seal(pack_uuid)
        sealed = (await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}", headers=h)).json()
        assert sealed["status"] == "SEALED" and sealed["content_hash"].startswith("sha256:")

        # Stage 2: the PDF portfolio must build for a CAPA pack without crashing (its cover carries
        # the v2 verify scheme + the N/A gap; the dossier itself stays in the ZIP variant).
        async with get_sessionmaker()() as s:
            await build_and_cache_portfolio(s, pack_uuid)
        async with get_sessionmaker()() as s:
            built = await s.get(EvidencePack, pack_uuid)
            assert built is not None and built.portfolio_blob_sha256 is not None

        with zipfile.ZipFile(io.BytesIO(await _download_zip(app_client, h, pack_uuid))) as zf:
            names = set(zf.namelist())
            assert any(n.startswith(f"records/{impl_ev}/") for n in names)
            assert any(n.startswith(f"records/{eff_ev}/") for n in names)
            dossier_names = [n for n in names if n.startswith("capas/")]
            assert len(dossier_names) == 1, names
            d = json.loads(zf.read(dossier_names[0]))
            assert d["kind"] == "capa" and d["id"] == capa_id
            assert d["close_state"] == "Closed"
            assert d["origin_finding"] is not None  # the audit NC

            stage_types = [s["stage"] for s in d["stages"]]
            assert "RootCause" in stage_types and "ActionPlan" in stage_types
            verify = [s for s in d["stages"] if s["stage"] == "Verify"][-1]
            assert "effective" in json.dumps(verify["content_block"])
            assert verify["signature"]["meaning"] == "verify"  # the REAL signature_event
            assert set(verify["signature"]["signer"].keys()) == {"user_id", "display_name"}
            assert verify["signature"]["content_digest"].startswith("sha256:")
            assert any(e["record_id"] == eff_ev for e in verify["evidence_records"])
            action_plan = [s for s in d["stages"] if s["stage"] == "ActionPlan"][-1]
            assert action_plan["signature"]["meaning"] == "approval"

            # No PII: only {user_id, display_name} — no email / keycloak_subject field.
            blob = zf.read(dossier_names[0]).decode()
            assert '"email"' not in blob and '"keycloak_subject"' not in blob
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["scope_kind"] == "CAPA"
            assert manifest["dossier"]["digest"].startswith("sha256:")
    finally:
        await _teardown([impl_ev, eff_ev], pack_uuid)


async def test_finding_capa_scope_validation(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("vpack")
    await _grant(subject, ("report.evidence_pack.generate", "report.export"))
    h = _auth(token_factory, subject)

    # Unknown finding / capa id → 422; an empty selector → 422.
    bad_finding = await app_client.post(
        "/api/v1/evidence-packs",
        headers=h,
        json={"title": "bad", "scope_kind": "FINDING", "finding_ids": [str(uuid.uuid4())]},
    )
    assert bad_finding.status_code == 422, bad_finding.text
    bad_capa = await app_client.post(
        "/api/v1/evidence-packs",
        headers=h,
        json={"title": "bad", "scope_kind": "CAPA", "capa_ids": [str(uuid.uuid4())]},
    )
    assert bad_capa.status_code == 422, bad_capa.text
    empty = await app_client.post(
        "/api/v1/evidence-packs",
        headers=h,
        json={"title": "bad", "scope_kind": "FINDING", "finding_ids": []},
    )
    assert empty.status_code == 422, empty.text


async def test_build_refuses_destroyed_finding_subject_before_seal(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Round-5 P1 (Codex): a FINDING/CAPA subject is a shared-PK record and can be DESTROYed. If the
    subject carries a DESTROY tombstone BEFORE the seal, the worker build must REFUSE — never copy a
    legally-erased narrative forward into a new sealed RETAIN_PERMANENT pack (the serve guard would
    only withhold it afterwards; this stops the born-dead copy from ever sealing). The pack flips to
    FAILED with no pack_record and no zip. Mutation-verify: without the build-time subject-tombstone
    check the pack SEALS with the destroyed finding's narrative baked into the ZIP."""
    subject = _subject("subjbuild")
    keys = (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS)
    await _grant(subject, keys)
    h = _auth(token_factory, subject)

    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    finding_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Major", "clause_ref": "8.4"},
        )
    ).json()["id"]

    pack_uuid: uuid.UUID | None = None
    try:
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": "NC pack", "scope_kind": "FINDING", "finding_ids": [finding_id]},
        )
        assert created.status_code == 201, created.text
        pack_uuid = uuid.UUID(created.json()["id"])

        # DESTROY the SUBJECT finding (its shared-PK record) BEFORE the seal.
        async with get_sessionmaker()() as s:
            finding_rec = await s.get(Record, uuid.UUID(finding_id))
            assert finding_rec is not None
            disposition._write_tombstone(
                s, finding_rec, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
            )
            await s.commit()

        await _seal(pack_uuid)  # drives build() → the subject-tombstone check → _fail (no raise)

        async with get_sessionmaker()() as s:
            built = await s.get(EvidencePack, pack_uuid)
            assert built is not None
            assert built.status is PackStatus.FAILED, built.status
            assert built.pack_record_id is None  # nothing sealed → no ZIP-as-record registered
            assert built.zip_blob_sha256 is None
            assert "destroyed" in (built.error or "")
    finally:
        await owner_delete_disposition_events([uuid.UUID(finding_id)])
        await _teardown([], pack_uuid)


async def test_dossier_omits_r28_excluded_evidence_identifier(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Round-5 P1 (Codex): the dossier's per-subject evidence list is filtered to the build's
    R28-INCLUDED record set. ``evidence_records_for_targets`` reloads every linked record with NO
    permission / period / absence filter, so without the filter the shareable dossier would leak an
    identifier for a record the pack itself excluded. Two evidence records are linked to a finding;
    one is DESTROYed before the seal (→ EXCLUDED_ABSENCE, one of the three R28 exclusion reasons all
    funnelling through the same INCLUDED set the filter keys on). The sealed dossier's
    evidence_records must carry ONLY the intact record's id, never the excluded one's identifier.
    Mutation-verify: without the filter the dossier lists BOTH identifiers."""
    subject = _subject("dossierfilter")
    keys = (*_AUDIT_KEYS, "finding.create", "finding.read", "capa.read", *_PACK_KEYS)
    await _grant(subject, keys)
    h = _auth(token_factory, subject)

    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    finding_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Major", "clause_ref": "8.4"},
        )
    ).json()["id"]

    ev_keep = await _evidence_record(app_client, h, "kept evidence")
    ev_drop = await _evidence_record(app_client, h, "excluded evidence")
    await _link(app_client, h, ev_keep, "finding", finding_id)
    await _link(app_client, h, ev_drop, "finding", finding_id)

    pack_uuid: uuid.UUID | None = None
    try:
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": "NC pack", "scope_kind": "FINDING", "finding_ids": [finding_id]},
        )
        assert created.status_code == 201, created.text
        pack_uuid = uuid.UUID(created.json()["id"])

        # DESTROY ev_drop before sealing → the build re-classifies it EXCLUDED_ABSENCE, so it is not
        # in the INCLUDED set the dossier filters to. The finding SUBJECT + ev_keep stay intact, so
        # the build seals and the serve guard does not trip — only the excluded evidence is dropped.
        async with get_sessionmaker()() as s:
            drop_rec = await s.get(Record, uuid.UUID(ev_drop))
            assert drop_rec is not None
            disposition._write_tombstone(
                s, drop_rec, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
            )
            await s.commit()

        await _seal(pack_uuid)
        sealed = (await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}", headers=h)).json()
        assert sealed["status"] == "SEALED", sealed

        with zipfile.ZipFile(io.BytesIO(await _download_zip(app_client, h, pack_uuid))) as zf:
            dossier_names = [n for n in zf.namelist() if n.startswith("findings/")]
            assert len(dossier_names) == 1
            d = json.loads(zf.read(dossier_names[0]))
            ev_ids = {e["record_id"] for e in d["evidence_records"]}
            assert ev_keep in ev_ids  # the INCLUDED evidence is serialized
            assert ev_drop not in ev_ids  # the R28-excluded evidence identifier is filtered out
    finally:
        await owner_delete_disposition_events([uuid.UUID(ev_drop)])
        await _teardown([ev_keep, ev_drop], pack_uuid)
