"""S-improvement-4 integration proofs — the signed, engine-routed Top-Management authorization of an
Improvement Initiative (clause 10.3, R46/R2) over HTTP against testcontainer Postgres.

A Completed initiative's manager *requests* a management authorization; it routes through the
generic multi-stage engine to the seeded "Top Management" role (mig 0053). When a Top-Management
member signs (``meaning=verify``), the sign-off closes the initiative, binding a ``signature_event``
to the new ``Closed`` stage event via the pre-generated-UUID seam. Authority is the role-resolved
candidate pool (no permission key gates the SIGN). The unsigned ``/transition`` close is untouched.

Like ``test_capa``, the authorization candidate pool resolves by Role MEMBERSHIP
(``users_with_roles``) — so an approver must be ASSIGNED the seeded "Top Management" role. The
integration suite shares one session DB across files (other files also assign Top-Management
members), so assertions are scoped to **this run's own** initiative / signature / stage-event ids
and never to absolute counts. The empty-pool → ``NEEDS_ATTENTION`` fail-closed path is NOT exercised
here (the shared org always has Top-Management members); it is covered generically by
``test_workflow_engine``'s under-quorum proof.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._signature_enums import SignatureMeaning, SignedObjectType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.improvement_initiative_stage_event import (
    ImprovementInitiativeStageEvent,
)
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.session import get_sessionmaker

from .test_improvement import _create, _grant, _subject, _transition
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_IMP_KEYS = ("improvement.read", "improvement.manage")


async def _assign_top_mgmt(subject: str) -> uuid.UUID:
    """Assign the seeded "Top Management" role to a user (the candidate pool is role-membership, not
    a SYSTEM permission override; the ``test_capa._assign_seeded_role`` pattern)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        role = (await s.execute(select(Role).where(Role.name == "Top Management"))).scalar_one()
        s.add(
            RoleAssignment(
                org_id=user.org_id,
                user_id=user.id,
                role_id=role.id,
                bound_scope={"level": "SYSTEM"},
            )
        )
        await s.commit()
        return user.id


async def _drive_to_completed(
    client: AsyncClient, headers: dict[str, str], **create_kwargs: object
) -> str:
    """Create an initiative and walk Open→InProgress→Completed; return its id."""
    initiative_id = str((await _create(client, headers, **create_kwargs))["id"])
    await _transition(client, headers, initiative_id, to_state="InProgress")
    await _transition(client, headers, initiative_id, to_state="Completed")
    return initiative_id


async def _request_auth(
    client: AsyncClient, headers: dict[str, str], initiative_id: str, **body: object
) -> Any:
    r = await client.post(
        f"/api/v1/improvement-initiatives/{initiative_id}/request-authorization",
        headers=headers,
        json=body or {},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _my_pending_task(client: AsyncClient, headers: dict[str, str], instance_id: str) -> str:
    """The caller's own PENDING task for an instance (self-scoped My-Tasks; one per candidate)."""
    r = await client.get(f"/api/v1/tasks?instance_id={instance_id}&state=PENDING", headers=headers)
    assert r.status_code == 200, r.text
    tasks = r.json()
    assert len(tasks) == 1, tasks
    return str(tasks[0]["id"])


# --- 1. Request → Top-Management signs → the initiative closes with a verify signature ---------


async def test_request_then_sign_closes_with_verify_signature(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The happy path: a manager requests authorization for a Completed initiative; a Top-Management
    member signs (``verify``); the initiative flips to Closed bound to a single
    ``signature_event(meaning=verify, signed_object_type=improvement_initiative_stage_event)`` whose
    ``signed_object_id`` is the Closed stage event (and the stage event's ``signed_event_id`` points
    back at the signature — the two mutually-referencing INSERTs). An INITIATIVE_AUTHORIZED audit is
    written, and GET /authorization reflects COMPLETED."""
    tm_subj = _subject("auth-tm")
    await _assign_top_mgmt(tm_subj)
    htm = _auth(token_factory, tm_subj)

    mgr_subj = _subject("auth-mgr")
    await _grant(mgr_subj, _IMP_KEYS)
    hm = _auth(token_factory, mgr_subj)

    initiative_id = await _drive_to_completed(app_client, hm, title="Sign me")

    # Before any request, GET /authorization is null (never 404 for a no-cycle initiative).
    pre = await app_client.get(
        f"/api/v1/improvement-initiatives/{initiative_id}/authorization", headers=hm
    )
    assert pre.status_code == 200, pre.text
    assert pre.json() is None

    req = await _request_auth(app_client, hm, initiative_id, comment="Please authorize closure")
    instance_id = str(req["instance_id"])
    assert req["current_state"] == "top_mgmt_authorization"  # the seeded stage materialized a task

    # The Top-Management member finds + signs their own task (verify).
    task_id = await _my_pending_task(app_client, htm, instance_id)
    decision = (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision",
            headers=htm,
            json={"outcome": "verify", "comment": "Benefit realized — scrap down 30%"},
        )
    ).json()
    assert decision["current_state"] == "COMPLETED", decision
    assert decision["initiative_stage"] == "Closed"
    sig_id = decision["signature_event_id"]
    assert sig_id is not None

    # The initiative is now Closed with closed_at set.
    detail = (
        await app_client.get(f"/api/v1/improvement-initiatives/{initiative_id}", headers=hm)
    ).json()
    assert detail["stage"] == "Closed"
    assert detail["closed_at"] is not None

    # The signed Closed stage event references the signature; the signature references the event.
    async with get_sessionmaker()() as s:
        closed_event = (
            await s.execute(
                select(ImprovementInitiativeStageEvent).where(
                    ImprovementInitiativeStageEvent.initiative_id == uuid.UUID(initiative_id),
                    ImprovementInitiativeStageEvent.to_state == "Closed",
                )
            )
        ).scalar_one()
        assert closed_event.from_state is not None
        assert closed_event.from_state.value == "Completed"
        assert str(closed_event.signed_event_id) == sig_id
        sig = (
            await s.execute(select(SignatureEvent).where(SignatureEvent.id == uuid.UUID(sig_id)))
        ).scalar_one()
        assert sig.meaning is SignatureMeaning.verify
        assert sig.signed_object_type is SignedObjectType.improvement_initiative_stage_event
        assert sig.signed_object_id == closed_event.id
        assert str(sig.signer_user_id) == str((await _ensure_user(s, tm_subj)).id)
        # Exactly ONE signature references this initiative's stage events.
        event_ids = list(
            (
                await s.execute(
                    select(ImprovementInitiativeStageEvent.id).where(
                        ImprovementInitiativeStageEvent.initiative_id == uuid.UUID(initiative_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        sig_count = (
            await s.execute(
                select(func.count())
                .select_from(SignatureEvent)
                .where(SignatureEvent.signed_object_id.in_(event_ids))
            )
        ).scalar_one()
        assert sig_count == 1
        # The leadership act is first-class in the audit trail.
        authorized = (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.object_id == uuid.UUID(initiative_id),
                    AuditEvent.event_type == EventType.INITIATIVE_AUTHORIZED,
                )
            )
        ).scalar_one()
        assert authorized.object_type == AuditObjectType.improvement_initiative
        assert authorized.scope_ref == detail["identifier"]

    # GET /authorization now reflects the COMPLETED cycle.
    after = (
        await app_client.get(
            f"/api/v1/improvement-initiatives/{initiative_id}/authorization", headers=hm
        )
    ).json()
    assert after["current_state"] == "COMPLETED"


# --- 2. Request guards ------------------------------------------------------------------------


async def test_request_requires_completed_initiative(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A request is a 409 ``initiative_not_authorizable`` unless the initiative is Completed (Open
    or InProgress are rejected)."""
    await _assign_top_mgmt(_subject("auth-tm2"))  # a pool exists; the guard is the stage gate
    mgr_subj = _subject("auth-mgr2")
    await _grant(mgr_subj, _IMP_KEYS)
    hm = _auth(token_factory, mgr_subj)

    open_id = str((await _create(app_client, hm, title="Still open"))["id"])
    r_open = await app_client.post(
        f"/api/v1/improvement-initiatives/{open_id}/request-authorization", headers=hm, json={}
    )
    assert r_open.status_code == 409, r_open.text
    assert r_open.json()["code"] == "initiative_not_authorizable"

    await _transition(app_client, hm, open_id, to_state="InProgress")
    r_inprog = await app_client.post(
        f"/api/v1/improvement-initiatives/{open_id}/request-authorization", headers=hm, json={}
    )
    assert r_inprog.status_code == 409, r_inprog.text
    assert r_inprog.json()["code"] == "initiative_not_authorizable"


async def test_request_twice_is_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """At most one active authorization per initiative: a second request while the first is still
    pending is a 409 ``authorization_in_progress``."""
    await _assign_top_mgmt(_subject("auth-tm3"))
    mgr_subj = _subject("auth-mgr3")
    await _grant(mgr_subj, _IMP_KEYS)
    hm = _auth(token_factory, mgr_subj)
    initiative_id = await _drive_to_completed(app_client, hm, title="Once only")
    await _request_auth(app_client, hm, initiative_id)
    again = await app_client.post(
        f"/api/v1/improvement-initiatives/{initiative_id}/request-authorization",
        headers=hm,
        json={},
    )
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "authorization_in_progress"


# --- 3. Reject leaves the initiative Completed + re-requestable; no signature ------------------


async def test_reject_leaves_completed_and_is_rerequestable(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A Top-Management reject does NOT close the initiative (it stays Completed), mints no
    signature, and a fresh authorization can be requested afterward (REJECTED is terminal for the
    instance, so it no longer blocks)."""
    tm_subj = _subject("auth-tm4")
    await _assign_top_mgmt(tm_subj)
    htm = _auth(token_factory, tm_subj)
    mgr_subj = _subject("auth-mgr4")
    await _grant(mgr_subj, _IMP_KEYS)
    hm = _auth(token_factory, mgr_subj)
    initiative_id = await _drive_to_completed(app_client, hm, title="Reject me")

    req = await _request_auth(app_client, hm, initiative_id)
    task_id = await _my_pending_task(app_client, htm, str(req["instance_id"]))
    rejected = (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision",
            headers=htm,
            json={"outcome": "reject", "comment": "Benefit not demonstrated"},
        )
    ).json()
    assert rejected["current_state"] == "REJECTED", rejected
    assert rejected.get("signature_event_id") is None

    # The initiative is untouched (still Completed) and no signed stage event exists.
    assert (
        await app_client.get(f"/api/v1/improvement-initiatives/{initiative_id}", headers=hm)
    ).json()["stage"] == "Completed"
    async with get_sessionmaker()() as s:
        signed_hooks = (
            await s.execute(
                select(func.count())
                .select_from(ImprovementInitiativeStageEvent)
                .where(
                    ImprovementInitiativeStageEvent.initiative_id == uuid.UUID(initiative_id),
                    ImprovementInitiativeStageEvent.signed_event_id.isnot(None),
                )
            )
        ).scalar_one()
        assert signed_hooks == 0

    # A fresh request is allowed now the first instance is terminal (REJECTED).
    again = await _request_auth(app_client, hm, initiative_id)
    assert again["current_state"] == "top_mgmt_authorization"


# --- 4. The SIGN is candidate-pool authority (a non-member 404-collapses) ----------------------


async def test_non_top_management_cannot_sign(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Authority is Top-Management role membership, not a permission key: a caller who is NOT a
    Top-Management member (here the requesting manager) cannot decide the authorization task — the
    sensitive-task gate 404-collapses (never reveals the task)."""
    await _assign_top_mgmt(_subject("auth-tm5"))
    mgr_subj = _subject("auth-mgr5")
    await _grant(mgr_subj, _IMP_KEYS)
    hm = _auth(token_factory, mgr_subj)
    initiative_id = await _drive_to_completed(app_client, hm, title="No side door")
    req = await _request_auth(app_client, hm, initiative_id)
    # The manager can see the materialized task id in the request response, but is NOT in its pool.
    task_id = str(req["tasks"][0]["id"])
    blocked = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hm, json={"outcome": "verify"}
    )
    assert blocked.status_code == 404, blocked.text


# --- 5. Idempotent replay ---------------------------------------------------------------------


async def test_sign_is_idempotent_on_replay(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An Idempotency-Key replay of the same completing sign returns the SAME signature_event_id
    and initiative_stage (the engine replay + the CAPA ``_enrich_completed_replay`` precedent) and
    writes no second signature."""
    tm_subj = _subject("auth-tm6")
    await _assign_top_mgmt(tm_subj)
    htm = _auth(token_factory, tm_subj)
    mgr_subj = _subject("auth-mgr6")
    await _grant(mgr_subj, _IMP_KEYS)
    hm = _auth(token_factory, mgr_subj)
    initiative_id = await _drive_to_completed(app_client, hm, title="Replay me")
    req = await _request_auth(app_client, hm, initiative_id)
    task_id = await _my_pending_task(app_client, htm, str(req["instance_id"]))

    key = uuid.uuid4().hex
    first = (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision",
            headers={**htm, "Idempotency-Key": key},
            json={"outcome": "verify", "comment": "ok"},
        )
    ).json()
    assert first["current_state"] == "COMPLETED"
    sig_id = first["signature_event_id"]
    assert sig_id is not None

    replay = (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision",
            headers={**htm, "Idempotency-Key": key},
            json={"outcome": "verify", "comment": "ok"},
        )
    ).json()
    assert replay["signature_event_id"] == sig_id
    assert replay["initiative_stage"] == "Closed"

    async with get_sessionmaker()() as s:
        event_ids = list(
            (
                await s.execute(
                    select(ImprovementInitiativeStageEvent.id).where(
                        ImprovementInitiativeStageEvent.initiative_id == uuid.UUID(initiative_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        sig_count = (
            await s.execute(
                select(func.count())
                .select_from(SignatureEvent)
                .where(SignatureEvent.signed_object_id.in_(event_ids))
            )
        ).scalar_one()
        assert sig_count == 1
