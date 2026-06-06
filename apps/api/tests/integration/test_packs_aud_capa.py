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

from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.evidence_blob import EvidenceBlob
from easysynq_api.db.models.evidence_for_link import EvidenceForLink
from easysynq_api.db.models.evidence_pack import EvidencePack
from easysynq_api.db.models.pack_item import PackItem
from easysynq_api.db.models.record import Record
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.packs import build_and_cache_portfolio

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
