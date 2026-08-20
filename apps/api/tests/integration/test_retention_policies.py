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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from easysynq_api.config import get_settings
from easysynq_api.db.models._retention_enums import DispositionAction, RetentionBasis
from easysynq_api.db.models.disposition_event import DispositionEvent
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.evidence_blob import EvidenceBlob
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.retention_policy import RetentionPolicy
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.records import sweep_due_records
from easysynq_api.services.records.repository import (
    SEALED_PACK_POLICY_NAME,
    ensure_sealed_pack_policy,
    sealed_pack_policy_id,
)

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
    # Task 4 makes both the event and its exact owner edge structural. Teardown therefore removes
    # the complete FK-RESTRICT chain through the existing owner DSN, never by widening app grants.
    owner_dsn = get_settings().database_url_sync
    assert owner_dsn is not None
    engine = create_async_engine(owner_dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            await s.execute(delete(DispositionEvent).where(DispositionEvent.record_id.in_(uids)))
            await s.execute(delete(EvidenceBlob).where(EvidenceBlob.record_id.in_(uids)))
            await s.execute(delete(Record).where(Record.id.in_(uids)))
            await s.execute(delete(DocumentedInformation).where(DocumentedInformation.id.in_(uids)))
            await s.commit()
    finally:
        await engine.dispose()


async def _delete_policy(policy_id: str) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(delete(RetentionPolicy).where(RetentionPolicy.id == uuid.UUID(policy_id)))
        await s.commit()


async def _backdate(record_id: str, *, days: int) -> None:
    when = datetime.date.today() - datetime.timedelta(days=days)
    owner_dsn = get_settings().database_url_sync
    assert owner_dsn is not None
    engine = create_async_engine(owner_dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            await s.execute(
                update(Record)
                .where(Record.id == uuid.UUID(record_id))
                .values(retention_basis_date=when)
            )
            await s.commit()
    finally:
        await engine.dispose()


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
    for name in ("System Default Retention", SEALED_PACK_POLICY_NAME):
        bad = await app_client.post(
            "/api/v1/retention-policies", headers=h, json=_policy_body(name=name)
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


async def test_patch_explicit_null_rejects_required_fields_and_clears_nullable_fields(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """PATCH omission is a no-op; explicit null is legal only for nullable policy columns."""
    subject = _subject("rp-null")
    await _grant(subject, _PERMS)
    h = _auth(token_factory, subject)
    created = await app_client.post(
        "/api/v1/retention-policies",
        headers=h,
        json=_policy_body(
            applies_to={"record_type": "SUPPLIER_EVAL"},
            worm_lock_period="P10Y",
        ),
    )
    assert created.status_code == 201, created.text
    original = created.json()
    pid = original["id"]
    try:
        for field in ("name", "basis", "duration", "disposition_action", "review_required"):
            rejected = await app_client.patch(
                f"/api/v1/retention-policies/{pid}", headers=h, json={field: None}
            )
            assert rejected.status_code == 422, (field, rejected.text)
            error = rejected.json()["errors"][0]
            assert error["field"] == field
            assert error["code"] == "not_nullable"

            unchanged = await app_client.get(f"/api/v1/retention-policies/{pid}", headers=h)
            assert unchanged.status_code == 200, unchanged.text
            assert unchanged.json()[field] == original[field]

        cleared = await app_client.patch(
            f"/api/v1/retention-policies/{pid}",
            headers=h,
            json={"applies_to": None, "worm_lock_period": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["applies_to"] is None
        assert cleared.json()["worm_lock_period"] is None
    finally:
        await _delete_policy(pid)


async def test_pinned_policy_period_is_immutable_but_unpinned_policy_remains_editable(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Task 4 freezes physical authority until Task 6 staged activation exists."""
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
        # Once pinned, neither reduction nor extension nor a WORM-only change may rewrite
        # the authority that was used to certify physical protection.
        for changes in (
            {"duration": "P3Y"},
            {"duration": "P20Y"},
            {"worm_lock_period": "P20Y"},
        ):
            refused = await app_client.patch(
                f"/api/v1/retention-policies/{pid}", headers=h, json=changes
            )
            assert refused.status_code == 409, refused.text
            assert refused.json()["code"] == "conflict"

        unchanged = await app_client.get(f"/api/v1/retention-policies/{pid}", headers=h)
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["duration"] == "P5Y"
    finally:
        await _delete_records(rids)
        await _delete_policy(pid)


@pytest.mark.parametrize(
    "overrides",
    (
        {"duration": "P999999999999999999999Y", "worm_lock_period": None},
        {"duration": "P1Y", "worm_lock_period": "P999999999999999999999D"},
    ),
    ids=("duration-overflow", "worm-period-overflow"),
)
async def test_policy_period_overflow_returns_bounded_validation_problem(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    overrides: dict[str, object],
) -> None:
    subject = _subject(f"rp-overflow-{uuid.uuid4().hex[:8]}")
    await _grant(subject, _PERMS)
    headers = _auth(token_factory, subject)
    response = await app_client.post(
        "/api/v1/retention-policies",
        headers=headers,
        json=_policy_body(**overrides),
    )
    created_id = response.json().get("id") if response.status_code == 201 else None
    try:
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "validation_error"
        assert response.json()["errors"][0]["code"] == "invalid_duration"
    finally:
        if created_id is not None:
            await _delete_policy(created_id)


async def test_disposed_record_pin_still_returns_bounded_policy_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The database backstop must not leak its trigger error through the API."""
    subject = _subject(f"rp-disposed-{uuid.uuid4().hex[:8]}")
    await _grant(subject, _PERMS)
    headers = _auth(token_factory, subject)
    policy_id = (
        await app_client.post(
            "/api/v1/retention-policies",
            headers=headers,
            json=_policy_body(duration="P1D"),
        )
    ).json()["id"]
    record_ids: list[str] = []
    try:
        captured = (
            await _capture(
                app_client,
                headers,
                record_type="SUPPLIER_EVAL",
                title="disposed policy pin",
                retention_policy_id=policy_id,
            )
        ).json()
        record_ids.append(captured["id"])
        await _backdate(captured["id"], days=30)
        async with get_sessionmaker()() as session:
            await sweep_due_records(session)
        async with get_sessionmaker()() as session:
            record = await session.get(Record, uuid.UUID(captured["id"]))
            assert record is not None
            assert record.disposition_state.value == "DISPOSED"

        refused = await app_client.patch(
            f"/api/v1/retention-policies/{policy_id}",
            headers=headers,
            json={"duration": "P2D"},
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "conflict"
    finally:
        await _delete_records(record_ids)
        await _delete_policy(policy_id)


async def test_system_managed_policies_protected(
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

    sealed_pack = next(p for p in listed if p["name"] == SEALED_PACK_POLICY_NAME)
    assert sealed_pack["duration"] == "PERMANENT"
    assert sealed_pack["disposition_action"] == "RETAIN_PERMANENT"
    sid = sealed_pack["id"]
    mutated = await app_client.patch(
        f"/api/v1/retention-policies/{sid}",
        headers=h,
        json={"duration": "P1D", "disposition_action": "DESTROY"},
    )
    assert mutated.status_code == 409
    assert mutated.json()["code"] == "system_policy_protected"
    sealed_archived = await app_client.post(f"/api/v1/retention-policies/{sid}/archive", headers=h)
    assert sealed_archived.status_code == 409
    assert sealed_archived.json()["code"] == "system_policy_protected"


async def test_lazy_pack_policy_ensure_preserves_a_user_name_collision(
    app_client: AsyncClient, dsns: dict[str, str]
) -> None:
    """An org created after 0077 can still arrive with a same-name row during a rolling upgrade."""
    del app_client  # fixture dependency ensures the shared database is migrated
    org_id = uuid.uuid4()
    legacy_id = uuid.uuid4()
    owner_engine = create_async_engine(dsns["owner"])
    try:
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as s:
            s.add(
                Organization(
                    id=org_id,
                    legal_name="M7 collision proof",
                    short_code=f"M7-{uuid.uuid4().hex[:8].upper()}",
                )
            )
            legacy = RetentionPolicy(
                id=legacy_id,
                org_id=org_id,
                name=SEALED_PACK_POLICY_NAME,
                applies_to={"record_type": "SUPPLIER_EVAL"},
                basis=RetentionBasis.CAPTURED_AT,
                duration="P3Y",
                disposition_action=DispositionAction.DESTROY,
                review_required=True,
                worm_lock_period="P5Y",
                is_active=True,
            )
            s.add(legacy)
            await s.flush()

            managed = await ensure_sealed_pack_policy(s, org_id)
            await s.commit()
            await s.refresh(legacy)
            assert managed.id == sealed_pack_policy_id(org_id)
            assert managed.id != legacy.id
            assert managed.name == SEALED_PACK_POLICY_NAME
            assert managed.duration == "PERMANENT"
            assert managed.disposition_action is DispositionAction.RETAIN_PERMANENT
            assert legacy.name.startswith(f"{SEALED_PACK_POLICY_NAME} (preserved user policy: ")
            assert legacy.applies_to == {"record_type": "SUPPLIER_EVAL"}
            assert legacy.duration == "P3Y"
            assert legacy.disposition_action is DispositionAction.DESTROY
            assert legacy.review_required is True
            assert legacy.worm_lock_period == "P5Y"

    finally:
        try:
            async with async_sessionmaker(owner_engine, expire_on_commit=False)() as s:
                await s.execute(delete(RetentionPolicy).where(RetentionPolicy.org_id == org_id))
                await s.execute(delete(Organization).where(Organization.id == org_id))
                await s.commit()
        finally:
            await owner_engine.dispose()


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
