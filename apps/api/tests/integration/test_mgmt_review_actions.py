"""S-mr-3 integration: MR ACTION output → CAPA / DCR spawns + the close-gate decouple."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._capa_enums import CapaSource
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.session import get_sessionmaker

from .test_mgmt_review import _auth, _drive_review_to_release, _grant

pytestmark = pytest.mark.integration


async def _action_output_id(client: AsyncClient, h: dict[str, str], rid: str) -> str:
    det = (await client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    return next(o["id"] for o in det["outputs"] if o["output_type"] == "ACTION")


async def test_raise_capa_from_action_output(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("capa.create", "capa.read"))
    oid = await _action_output_id(app_client, hs, rid)

    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-capa",
        headers=hs,
        json={"severity": "Major"},
    )
    assert r.status_code == 201, r.text
    capa_id = r.json()["spawned_capa_id"]
    assert capa_id is not None

    async with get_sessionmaker()() as s:
        capa = (await s.execute(select(Capa).where(Capa.id == uuid.UUID(capa_id)))).scalar_one()
        assert capa.source is CapaSource.review_output
        assert capa.severity.value == "Major"
        # NO signature on a recording act (R43)
        sigs = (
            (
                await s.execute(
                    select(SignatureEvent).where(SignatureEvent.signed_object_id == capa.id)
                )
            )
            .scalars()
            .all()
        )
        assert sigs == []
        # the MR-side audit fired
        ev = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == EventType.MGMT_REVIEW_CAPA_SPAWNED,
                        AuditEvent.object_type == AuditObjectType.document,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any(e.after.get("capa_id") == capa_id for e in ev)

    # one-shot latch: a second spawn 409s
    again = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-capa",
        headers=hs,
        json={"severity": "Minor"},
    )
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "capa_already_spawned"


async def test_raise_capa_404_on_unknown_output(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An unknown output id under a real review → 404 (the not-found guard, ordered first)."""
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("capa.create",))
    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{uuid.uuid4()}/raise-capa",
        headers=hs,
        json={"severity": "Major"},
    )
    assert r.status_code == 404, r.text


async def test_spawned_capa_does_not_block_close(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """F3: a spawned CAPA (still open) does NOT block MR close — the MR_ACTION task DONE is the sole
    close signal."""
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    ho = _auth(token_factory, owner_sub)
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("capa.create", "capa.read"))
    oid = await _action_output_id(app_client, hs, rid)
    sp = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-capa",
        headers=hs,
        json={"severity": "Major"},
    )
    assert sp.status_code == 201, sp.text
    tasks = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    action_task = next(t for t in tasks if t["assignee_user_id"] == str(owner_id))
    done = await app_client.post(
        f"/api/v1/tasks/{action_task['id']}/decision", headers=ho, json={"outcome": "complete"}
    )
    assert done.status_code == 200, done.text
    closed = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs)
    assert closed.status_code == 200, closed.text
    assert closed.json()["close_state"] == "Closed"


async def test_raise_dcr_from_action_output_links_mgmt_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("changeRequest.create", "changeRequest.read"))
    oid = await _action_output_id(app_client, hs, rid)

    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-dcr",
        headers=hs,
        json={
            "change_type": "CREATE",
            "change_significance": "MINOR",
            "reason_text": "Draft a supplier-evaluation SOP per the review decision",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source_link_type"] == "mgmt_review"
    assert body["source_link_id"] == oid
    assert body["reason_class"] == "mgmt_review"


async def test_raise_dcr_idempotency_key_replays(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("changeRequest.create", "changeRequest.read"))
    oid = await _action_output_id(app_client, hs, rid)
    key = uuid.uuid4().hex
    payload = {
        "change_type": "CREATE",
        "change_significance": "MINOR",
        "reason_text": "idempotent draft",
    }
    first = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-dcr",
        headers={**hs, "Idempotency-Key": key},
        json=payload,
    )
    assert first.status_code == 201, first.text
    replay = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-dcr",
        headers={**hs, "Idempotency-Key": key},
        json=payload,
    )
    assert replay.status_code == 200, replay.text  # 200 == replay, not a new DCR
    assert replay.json()["id"] == first.json()["id"]
    other = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-dcr",
        headers={**hs, "Idempotency-Key": uuid.uuid4().hex},
        json=payload,
    )
    assert other.status_code == 201, other.text
    assert other.json()["id"] != first.json()["id"]
