"""S-rec-4 integration proofs — /retention-policies CRUD + soft-archive, over HTTP against
testcontainer Postgres + MinIO + Redis.

Shared-DB isolation: each test creates its OWN policy (unique name; ``applies_to`` only on a record
type no other test captures — SUPPLIER_EVAL) and cleans up its records + policy in the FK-RESTRICT
order. Retention-policy management rides SYSTEM ``retention.read``/``retention.manage`` overrides
(R38; authz is proven in S2)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, update

from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.evidence_blob import EvidenceBlob
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.retention_policy import RetentionPolicy
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.records import sweep_due_records

from ._owner_db import owner_delete_disposition_events
from .test_records import _capture, _grant, _subject
from .test_vault import _auth

pytestmark = pytest.mark.integration

_PERMS = ("retention.read", "retention.manage", "record.read", "record.create", "record.dispose")


def _policy_body(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": f"P-{uuid.uuid4().hex[:10]}",
        "duration": "P10Y",
        "disposition_action": "ARCHIVE_COLD",
        "review_required": False,
    }
    body.update(over)
    return body


async def _delete_records(ids: list[str]) -> None:
    if not ids:
        return
    uids = [uuid.UUID(i) for i in ids]
    # disposition_event is append-only for the app role (0072 REVOKE UPDATE,DELETE) → delete the
    # FK-RESTRICT tombstone as the OWNER first; the rest of the chain stays on the app session.
    await owner_delete_disposition_events(uids)
    async with get_sessionmaker()() as s:
        await s.execute(delete(EvidenceBlob).where(EvidenceBlob.record_id.in_(uids)))
        await s.execute(delete(Record).where(Record.id.in_(uids)))
        await s.execute(delete(DocumentedInformation).where(DocumentedInformation.id.in_(uids)))
        await s.commit()


async def _delete_policy(policy_id: str) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(delete(RetentionPolicy).where(RetentionPolicy.id == uuid.UUID(policy_id)))
        await s.commit()


async def _backdate(record_id: str, *, days: int) -> None:
    when = datetime.date.today() - datetime.timedelta(days=days)
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Record)
            .where(Record.id == uuid.UUID(record_id))
            .values(retention_basis_date=when)
        )
        await s.commit()


# --- CRUD ---------------------------------------------------------------------------------


async def test_create_list_get_roundtrip(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rp")
    await _grant(subject, _PERMS)
    h = _auth(token_factory, subject)
    created = await app_client.post(
        "/api/v1/retention-policies", headers=h, json=_policy_body(duration="P7Y")
    )
    assert created.status_code == 201, created.text
    pol = created.json()
    pid = pol["id"]
    try:
        assert pol["active"] is True and pol["archived_at"] is None
        assert pol["duration"] == "P7Y"
        got = await app_client.get(f"/api/v1/retention-policies/{pid}", headers=h)
        assert got.status_code == 200 and got.json()["id"] == pid
        listed = (await app_client.get("/api/v1/retention-policies", headers=h)).json()
        assert any(p["id"] == pid for p in listed)
    finally:
        await _delete_policy(pid)


async def test_create_reserved_name_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rp")
    await _grant(subject, _PERMS)
    h = _auth(token_factory, subject)
    bad = await app_client.post(
        "/api/v1/retention-policies", headers=h, json=_policy_body(name="System Default Retention")
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["code"] == "reserved_name"


async def test_create_name_collision_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rp")
    await _grant(subject, _PERMS)
    h = _auth(token_factory, subject)
    name = f"Coll-{uuid.uuid4().hex[:8]}"
    first = await app_client.post(
        "/api/v1/retention-policies", headers=h, json=_policy_body(name=name)
    )
    assert first.status_code == 201
    pid = first.json()["id"]
    try:
        dup = await app_client.post(
            "/api/v1/retention-policies", headers=h, json=_policy_body(name=name)
        )
        assert dup.status_code == 409
        assert dup.json()["code"] == "name_taken"
    finally:
        await _delete_policy(pid)


async def test_extend_forward_guard(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """No pinned records: any edit allowed; with a pinned record: reductions 422, extensions 200."""
    subject = _subject("rp")
    await _grant(subject, _PERMS)
    h = _auth(token_factory, subject)
    pid = (
        await app_client.post(
            "/api/v1/retention-policies", headers=h, json=_policy_body(duration="P10Y")
        )
    ).json()["id"]
    rids: list[str] = []
    try:
        # Unused policy: a reduction is allowed.
        r = await app_client.patch(
            f"/api/v1/retention-policies/{pid}", headers=h, json={"duration": "P5Y"}
        )
        assert r.status_code == 200, r.text
        # Pin a record to it.
        rid = (
            await _capture(
                app_client, h, record_type="SUPPLIER_EVAL", title="se", retention_policy_id=pid
            )
        ).json()["id"]
        rids.append(rid)
        # Now a reduction is blocked, but an extension is allowed.
        reduced = await app_client.patch(
            f"/api/v1/retention-policies/{pid}", headers=h, json={"duration": "P3Y"}
        )
        assert reduced.status_code == 422
        assert reduced.json()["errors"][0]["code"] == "retention_reduction_blocked"
        extended = await app_client.patch(
            f"/api/v1/retention-policies/{pid}", headers=h, json={"duration": "P20Y"}
        )
        assert extended.status_code == 200, extended.text
        assert extended.json()["duration"] == "P20Y"
    finally:
        await _delete_records(rids)
        await _delete_policy(pid)


async def test_system_default_protected(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rp")
    await _grant(subject, _PERMS)
    h = _auth(token_factory, subject)
    listed = (
        await app_client.get("/api/v1/retention-policies?include_archived=true", headers=h)
    ).json()
    default = next(p for p in listed if p["name"] == "System Default Retention")
    did = default["id"]
    archived = await app_client.post(f"/api/v1/retention-policies/{did}/archive", headers=h)
    assert archived.status_code == 409
    assert archived.json()["code"] == "system_default_protected"
    renamed = await app_client.patch(
        f"/api/v1/retention-policies/{did}", headers=h, json={"name": "Renamed Default"}
    )
    assert renamed.status_code == 409
    assert renamed.json()["code"] == "system_default_protected"


async def test_archive_hides_from_resolution_but_pinned_records_still_swept(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Archiving a policy hides it from new-capture auto-attach (a new SUPPLIER_EVAL falls back to
    the System Default), but a record pinned BEFORE the archive is still swept under it."""
    subject = _subject("rp")
    await _grant(subject, _PERMS)
    h = _auth(token_factory, subject)
    pid = (
        await app_client.post(
            "/api/v1/retention-policies",
            headers=h,
            json=_policy_body(applies_to={"record_type": "SUPPLIER_EVAL"}, duration="P1D"),
        )
    ).json()["id"]
    rids: list[str] = []
    try:
        # Auto-attach: a SUPPLIER_EVAL capture (no pin) resolves to this policy.
        r1 = (await _capture(app_client, h, record_type="SUPPLIER_EVAL", title="a")).json()
        rids.append(r1["id"])
        assert r1["retention_policy_id"] == pid

        archived = await app_client.post(f"/api/v1/retention-policies/{pid}/archive", headers=h)
        assert archived.status_code == 200, archived.text
        assert archived.json()["active"] is False

        # A new SUPPLIER_EVAL no longer auto-attaches the archived policy → System Default fallback.
        r2 = (await _capture(app_client, h, record_type="SUPPLIER_EVAL", title="b")).json()
        rids.append(r2["id"])
        assert r2["retention_policy_id"] != pid

        # The pre-archive pinned record is still swept (due_active_records joins by id, not active).
        await _backdate(r1["id"], days=30)
        async with get_sessionmaker()() as s:
            summary = await sweep_due_records(s)
        assert summary["disposed"] >= 1
        async with get_sessionmaker()() as s:
            rec = await s.get(Record, uuid.UUID(r1["id"]))
            assert rec is not None and rec.disposition_state.value == "DISPOSED"
    finally:
        await _delete_records(rids)
        await _delete_policy(pid)


async def test_pin_archived_policy_at_capture_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rp")
    await _grant(subject, _PERMS)
    h = _auth(token_factory, subject)
    pid = (
        await app_client.post("/api/v1/retention-policies", headers=h, json=_policy_body())
    ).json()["id"]
    try:
        await app_client.post(f"/api/v1/retention-policies/{pid}/archive", headers=h)
        capture = await _capture(
            app_client, h, record_type="SUPPLIER_EVAL", title="x", retention_policy_id=pid
        )
        assert capture.status_code == 422
        assert capture.json()["errors"][0]["code"] == "retention_policy_archived"
    finally:
        await _delete_policy(pid)
