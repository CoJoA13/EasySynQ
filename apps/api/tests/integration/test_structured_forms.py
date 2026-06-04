"""S-rec-3 integration proofs — Mode-B structured-form capture, over HTTP against testcontainer
Postgres + MinIO + Redis.

A Form/Template (document_type FRM) is authored with a ``field_schema``, checked in (its WORM source
blob IS the canonical-serialized schema), then driven Effective through the normal document
lifecycle. Mode-B capture (POST /records against the template) validates the submitted
``form_field_values`` against the schema PINNED in the resolved version's snapshot and pins
``source_version_id``. Covers: the happy path, server-side validation failures, the Effective-only
default + the pre-release toggle (incl. the v0.1/v0.2 pin regression), the in-service edit guard,
correction-validates-against-the-pinned-edition, the best-effort structured-PDF rendition, and the
WORM-destroy rendition purge (the blob-row-iff-bytes invariant).

Records authoring rides SYSTEM ``record.*`` overrides; subjects are unique per test so the shared
session DB stays isolated; assertions are scoped to a test's own ids."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.record import Record
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.records import build_structured_pdf
from easysynq_api.services.vault import storage

from . import s5_helpers as s5
from .test_records import _grant, _subject
from .test_vault import _auth, _map_clause

pytestmark = pytest.mark.integration

_CAL_SCHEMA = {
    "fields": [
        {"key": "operator", "label": "Operator", "type": "string", "required": True, "max": 80},
        {"key": "reading", "type": "number", "min": 0, "max": 100},
        {"key": "result", "type": "enum", "required": True, "enum": ["pass", "adjusted", "fail"]},
    ]
}


async def _set_schema(client: AsyncClient, h: dict[str, str], did: str, schema: dict) -> None:
    r = await client.put(
        f"/api/v1/documents/{did}/form-schema", headers=h, json={"field_schema": schema}
    )
    assert r.status_code == 200, r.text


async def _checkin_schema(client: AsyncClient, h: dict[str, str], did: str) -> None:
    r = await client.post(
        f"/api/v1/documents/{did}/form-schema:checkin",
        headers=h,
        json={"change_reason": "schema", "change_significance": "MAJOR"},
    )
    assert r.status_code == 201, r.text


async def _approve_and_release(
    client: AsyncClient, did: str, h_approver: dict[str, str], h_releaser: dict[str, str]
) -> None:
    sr = await client.post(f"/api/v1/documents/{did}/submit-review", headers=h_approver)
    assert sr.status_code == 200, sr.text
    task_id = await s5.task_for_doc(did)
    dec = await client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=h_approver, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await client.post(f"/api/v1/documents/{did}/release", headers=h_releaser, json={})
    assert rel.status_code == 200, rel.text


async def _new_form_template(client: AsyncClient, h_author: dict[str, str], schema: dict) -> str:
    """Create an FRM document + set its working schema (Draft, not yet checked in)."""
    frm = await s5.type_id("FRM")
    r = await client.post(
        "/api/v1/documents",
        headers=h_author,
        json={"title": "Calibration Form", "document_type_id": frm, "area_code": "QA"},
    )
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    await _set_schema(client, h_author, did, schema)
    return did


async def _drive_template_effective(
    client: AsyncClient,
    h_author: dict[str, str],
    h_approver: dict[str, str],
    schema: dict,
) -> str:
    """Author → set schema → form-schema:checkin → map clause → submit → approve → release."""
    did = await _new_form_template(client, h_author, schema)
    await _checkin_schema(client, h_author, did)
    await _map_clause(client, h_author, did)
    await _approve_and_release(client, did, h_approver, h_approver)
    return did


async def _authors(token_factory: Callable[..., str]) -> tuple[dict[str, str], dict[str, str]]:
    """An author + an approver/releaser (distinct, for SoD), both holding the lifecycle perms; the
    approver also releases (allow_approver_release on)."""
    a, b = _subject("frm-a"), _subject("frm-b")
    await s5.grant_lifecycle(a)
    await s5.grant_lifecycle(b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    return _auth(token_factory, a), _auth(token_factory, b)


# --- the Mode-B capture contract ---------------------------------------------------------


async def test_mode_b_happy_path(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    ha, hb = await _authors(token_factory)
    did = await _drive_template_effective(app_client, ha, hb, _CAL_SCHEMA)

    # The render-the-form read returns the pinned schema + the resolved version.
    eff = await app_client.get(f"/api/v1/documents/{did}/effective-form-schema", headers=ha)
    assert eff.status_code == 200, eff.text
    effective_version_id = eff.json()["source_version_id"]
    assert eff.json()["field_schema"] == _CAL_SCHEMA

    cap_subj = _subject("cap")
    await _grant(cap_subj, ("record.read", "record.create"))
    hc = _auth(token_factory, cap_subj)
    cap = await app_client.post(
        "/api/v1/records",
        headers=hc,
        json={
            "record_type": "FILLED_FORM",
            "title": "Calibration of gauge 7",
            "source_document_id": did,
            "form_field_values": {"operator": "Mara", "reading": 42.5, "result": "pass"},
        },
    )
    assert cap.status_code == 201, cap.text
    body = cap.json()
    # The server resolved + pinned the template's Effective version (the caller supplied none).
    assert body["source_version_id"] == effective_version_id
    assert body["source_document_id"] == did
    assert body["form_field_values"]["result"] == "pass"


async def test_mode_b_validation_failure_is_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    ha, hb = await _authors(token_factory)
    did = await _drive_template_effective(app_client, ha, hb, _CAL_SCHEMA)
    cap_subj = _subject("cap")
    await _grant(cap_subj, ("record.read", "record.create"))
    hc = _auth(token_factory, cap_subj)
    bad = await app_client.post(
        "/api/v1/records",
        headers=hc,
        json={
            "record_type": "FILLED_FORM",
            "title": "Bad",
            "source_document_id": did,
            # operator missing (required); reading out of range; result not in enum.
            "form_field_values": {"reading": 999, "result": "maybe", "ghost": 1},
        },
    )
    assert bad.status_code == 422, bad.text
    fields = {e["field"] for e in bad.json()["errors"]}
    assert {"operator", "reading", "result", "ghost"} <= fields


async def test_capture_blocked_when_template_not_effective(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    ha, _hb = await _authors(token_factory)
    # A checked-in but NOT-released (Draft) form template; toggle OFF (the default).
    did = await _new_form_template(app_client, ha, _CAL_SCHEMA)
    await _checkin_schema(app_client, ha, did)
    cap_subj = _subject("cap")
    await _grant(cap_subj, ("record.read", "record.create"))
    hc = _auth(token_factory, cap_subj)
    cap = await app_client.post(
        "/api/v1/records",
        headers=hc,
        json={
            "record_type": "FILLED_FORM",
            "title": "Premature",
            "source_document_id": did,
            "form_field_values": {"operator": "x", "result": "pass"},
        },
    )
    assert cap.status_code == 422, cap.text
    assert any(e["code"] == "template_not_effective" for e in cap.json()["errors"])


async def test_put_form_schema_blocked_on_effective_template(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    ha, hb = await _authors(token_factory)
    did = await _drive_template_effective(app_client, ha, hb, _CAL_SCHEMA)
    # ha holds document.manage_metadata at SYSTEM (no lifecycle predicate) — the SERVICE still 409s.
    r = await app_client.put(
        f"/api/v1/documents/{did}/form-schema",
        headers=ha,
        json={"field_schema": _CAL_SCHEMA},
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "not_editable"


# --- the pre-release-capture toggle (PATCH /admin/config) --------------------------------


async def test_pre_release_toggle_allows_draft_capture_and_pins_it(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    ha, _hb = await _authors(token_factory)
    did = await _new_form_template(app_client, ha, _CAL_SCHEMA)
    await _checkin_schema(app_client, ha, did)  # Draft v0.1 (its schema pinned in the snapshot)

    # Flip the org toggle ON via the admin endpoint (config.update).
    admin = _subject("cfg-admin")
    await _grant(admin, ("config.update",))
    ha_admin = _auth(token_factory, admin)
    patched = await app_client.patch(
        "/api/v1/admin/config", headers=ha_admin, json={"capture_pre_release_templates": True}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["capture_pre_release_templates"] is True

    cap_subj = _subject("cap")
    await _grant(cap_subj, ("record.read", "record.create"))
    hc = _auth(token_factory, cap_subj)
    cap = await app_client.post(
        "/api/v1/records",
        headers=hc,
        json={
            "record_type": "FILLED_FORM",
            "title": "Pre-release fill",
            "source_document_id": did,
            "form_field_values": {"operator": "x", "result": "pass"},
        },
    )
    assert cap.status_code == 201, cap.text
    pinned_v1 = cap.json()["source_version_id"]

    # Re-edit the working schema + check in v0.2; the earlier record's pinned version is unchanged.
    await _set_schema(app_client, ha, did, {"fields": [{"key": "note", "type": "string"}]})
    await _checkin_schema(app_client, ha, did)
    eff = await app_client.get(f"/api/v1/documents/{did}/effective-form-schema", headers=ha)
    assert eff.json()["source_version_id"] != pinned_v1  # the resolver now picks v0.2

    # Turn it OFF again — a fresh capture against the still-non-Effective template is blocked.
    await app_client.patch(
        "/api/v1/admin/config", headers=ha_admin, json={"capture_pre_release_templates": False}
    )
    blocked = await app_client.post(
        "/api/v1/records",
        headers=hc,
        json={
            "record_type": "FILLED_FORM",
            "title": "Blocked now",
            "source_document_id": did,
            "form_field_values": {"note": "hi"},
        },
    )
    assert blocked.status_code == 422, blocked.text


async def test_config_setter_requires_config_update(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # A caller WITHOUT config.update (a content-tier grant) is denied; config.update succeeds.
    no_cfg = _subject("nocfg")
    await _grant(no_cfg, ("record.read",))
    denied = await app_client.patch(
        "/api/v1/admin/config",
        headers=_auth(token_factory, no_cfg),
        json={"capture_pre_release_templates": True},
    )
    assert denied.status_code == 403, denied.text

    admin = _subject("cfg-admin2")
    await _grant(admin, ("config.update",))
    ok = await app_client.patch(
        "/api/v1/admin/config",
        headers=_auth(token_factory, admin),
        json={"capture_pre_release_templates": False},
    )
    assert ok.status_code == 200, ok.text


# --- correction validates against the PINNED edition (records keep showing v2.0) ----------


async def test_correction_validates_against_pinned_edition(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    ha, hb = await _authors(token_factory)
    did = await _drive_template_effective(app_client, ha, hb, _CAL_SCHEMA)
    cap_subj = _subject("cap")
    await _grant(cap_subj, ("record.read", "record.create"))
    hc = _auth(token_factory, cap_subj)
    cap = await app_client.post(
        "/api/v1/records",
        headers=hc,
        json={
            "record_type": "FILLED_FORM",
            "title": "v2 record",
            "source_document_id": did,
            "form_field_values": {"operator": "Mara", "result": "pass"},
        },
    )
    assert cap.status_code == 201, cap.text
    rec_id = cap.json()["id"]
    pinned_v2 = cap.json()["source_version_id"]

    # Revise the template to v3.0 with a BREAKING schema change (operator → inspector, required).
    rev_schema = {
        "fields": [
            {"key": "inspector", "type": "string", "required": True},
            {"key": "result", "type": "enum", "required": True, "enum": ["pass", "fail"]},
        ]
    }
    sr = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    assert sr.status_code == 200, sr.text
    await _set_schema(app_client, ha, did, rev_schema)
    await _checkin_schema(app_client, ha, did)
    await _approve_and_release(app_client, did, hb, hb)

    # Correcting the v2.0 record validates against v2.0's schema (operator), NOT the new v3.0 one —
    # the same {operator, result} body that was valid at capture still validates.
    corr = await app_client.post(
        f"/api/v1/records/{rec_id}/correction",
        headers=hc,
        json={
            "record_type": "FILLED_FORM",
            "title": "v2 record (corrected)",
            "form_field_values": {"operator": "Mara", "result": "fail"},
        },
    )
    assert corr.status_code == 201, corr.text
    assert corr.json()["source_version_id"] == pinned_v2  # still pinned to the original edition
    assert corr.json()["correction_of"] == rec_id


# --- the structured-PDF rendition (best-effort Stage 2) + its destroy purge ---------------


async def _capture_mode_b(app_client: AsyncClient, hc: dict[str, str], did: str) -> str:
    cap = await app_client.post(
        "/api/v1/records",
        headers=hc,
        json={
            "record_type": "FILLED_FORM",
            "title": "Rendition record",
            "source_document_id": did,
            "form_field_values": {"operator": "Ken", "reading": 7, "result": "adjusted"},
        },
    )
    assert cap.status_code == 201, cap.text
    return cap.json()["id"]


async def test_structured_pdf_rendition_builds_and_downloads(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    ha, hb = await _authors(token_factory)
    did = await _drive_template_effective(app_client, ha, hb, _CAL_SCHEMA)
    cap_subj = _subject("cap")
    await _grant(cap_subj, ("record.read", "record.create"))
    hc = _auth(token_factory, cap_subj)
    rec_id = await _capture_mode_b(app_client, hc, did)

    # Pending before the best-effort build runs.
    pending = await app_client.get(f"/api/v1/records/{rec_id}/rendition", headers=hc)
    assert pending.status_code == 409, pending.text

    # Run the Stage-2 build directly (the worker path; .delay is fire-and-forget in tests).
    async with get_sessionmaker()() as s:
        await build_structured_pdf(s, uuid.UUID(rec_id))

    got = await app_client.get(f"/api/v1/records/{rec_id}/rendition", headers=hc)
    assert got.status_code == 200, got.text
    assert got.json()["content_type"] == "application/pdf"

    # Idempotent: a second build is a no-op (the pointer is already set).
    async with get_sessionmaker()() as s:
        await build_structured_pdf(s, uuid.UUID(rec_id))


async def test_destroy_purges_structured_pdf_blob(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    ha, hb = await _authors(token_factory)
    did = await _drive_template_effective(app_client, ha, hb, _CAL_SCHEMA)
    # Two distinct disposers for the R27 dual-control destroy.
    req_subj, app_subj = _subject("disp-req"), _subject("disp-app")
    await _grant(req_subj, ("record.read", "record.create", "record.dispose"))
    await _grant(app_subj, ("record.read", "record.dispose"))
    hreq, happ = _auth(token_factory, req_subj), _auth(token_factory, app_subj)
    rec_id = await _capture_mode_b(app_client, hreq, did)

    async with get_sessionmaker()() as s:
        await build_structured_pdf(s, uuid.UUID(rec_id))
    async with get_sessionmaker()() as s:
        rec = await s.get(Record, uuid.UUID(rec_id))
        assert rec is not None
        pdf_sha = rec.structured_pdf_blob_sha256
    assert pdf_sha is not None
    # The rendition object + its blob row exist pre-destroy.
    assert (await storage.head(pdf_sha, bucket=storage.get_settings().s3_bucket_renditions)).exists

    # Dual-control destroy (GOVERNANCE bypass — a Mode-B record has no evidence blob, so only the
    # rendition is purged): request → approve by a distinct actor.
    rq = await app_client.post(
        f"/api/v1/records/{rec_id}/worm-destroy-requests",
        headers=hreq,
        json={"legal_basis": "court order #42"},
    )
    assert rq.status_code == 201, rq.text
    req_id = rq.json()["id"]
    ap = await app_client.post(
        f"/api/v1/records/{rec_id}/worm-destroy-requests/{req_id}/approve",
        headers=happ,
        json={},
    )
    assert ap.status_code == 200, ap.text

    # The pointer is nulled, the rendition blob row is gone, and the bytes are purged — so the
    # backup manifest (which copies EVERY blob row) cannot hit NoSuchKey on a dead rendition.
    async with get_sessionmaker()() as s:
        rec = await s.get(Record, uuid.UUID(rec_id))
        assert rec is not None
        assert rec.structured_pdf_blob_sha256 is None
        assert (
            await s.execute(select(Blob).where(Blob.sha256 == pdf_sha))
        ).scalar_one_or_none() is None
    assert not (
        await storage.head(pdf_sha, bucket=storage.get_settings().s3_bucket_renditions)
    ).exists
