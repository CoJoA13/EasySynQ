"""S-dcr-4 integration proofs — DCR routing + approval via the declarative engine over HTTP.

The approval candidate pool resolves by Role MEMBERSHIP (``users_with_roles``), NOT by SYSTEM
permission overrides — so an approver must be ASSIGNED the seeded ``Process Owner`` / ``QMS
Owner`` role (the S-capa-2 gotcha). Per-approver signatures (doc 05 §5.4): a MAJOR DCR yields TWO
``signature_event(meaning=approval, signed_object_type=dcr)`` rows, a MINOR yields one.
Assertions are run-scoped to this run's DCR / instance.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._dcr_enums import DcrState
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.dcr_stage_event import DcrStageEvent
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.models.workflow import Task
from easysynq_api.db.session import get_sessionmaker

from .test_capa import _assign_seeded_role, _my_pending_task
from .test_dcr import _auth, _grant, _subject

pytestmark = pytest.mark.integration

_ROUTE_PERMS = (
    "changeRequest.create",
    "changeRequest.read",
    "changeRequest.assess",
    "changeRequest.route",
)


async def _open_assessed_dcr(client: AsyncClient, h: dict[str, str], significance: str) -> str:
    """Create a CREATE DCR at the given significance and assess it → return its id (Assessed)."""
    r = await client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "CREATE",
            "change_significance": significance,
            "reason_class": "process_improvement",
            "reason_text": f"approval-flow {significance}",
        },
    )
    assert r.status_code == 201, r.text
    dcr_id = r.json()["id"]
    a = await client.post(f"/api/v1/dcrs/{dcr_id}/assess", headers=h)
    assert a.status_code == 200, a.text
    return dcr_id


async def _approval_sig_count(dcr_id: str) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(SignatureEvent)
                .where(
                    SignatureEvent.signed_object_id == uuid.UUID(dcr_id),
                    SignatureEvent.signed_object_type == "dcr",
                )
            )
        ).scalar_one()


async def test_dcr_subject_task_carries_dcr_identity(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-optimize-1: a DCR-subject approval task surfaces in /tasks with the DCR's identity — the
    coalesce(...,Dcr.identifier)/coalesce(...,Dcr.reason_text) branch (no documented_information).
    Proves the list resolves a NON-document subject and agrees with the detail."""
    req = _subject("dcr-subj-req")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    qm = _subject("dcr-subj-qm")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MINOR")
    dcr = (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hr)).json()
    routed = await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)
    iid = routed.json()["approval_instance"]["id"]
    task_id = await _my_pending_task(app_client, hq, iid)

    listing = (await app_client.get("/api/v1/tasks?assignee=me&state=PENDING", headers=hq)).json()
    row = next(t for t in listing if t["id"] == task_id)
    assert row["subject_type"] == "DCR"
    assert row["subject_id"] == dcr_id
    assert row["subject_identifier"] == dcr["identifier"]  # DCR-{YYYY}-{SEQ}
    assert row["subject_title"] == dcr["reason_text"]  # short reason_text, untruncated

    detail = (await app_client.get(f"/api/v1/tasks/{task_id}", headers=hq)).json()
    assert detail["subject_identifier"] == row["subject_identifier"]
    assert detail["subject_title"] == row["subject_title"]


async def test_minor_dcr_single_qms_approval(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    req = _subject("dcr-route-minor")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    qm = _subject("dcr-qms-minor")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MINOR")
    routed = await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)
    assert routed.status_code == 200, routed.text
    body = routed.json()
    assert body["state"] == "InApproval"
    iid = body["approval_instance"]["id"]
    assert body["approval_instance"]["current_state"] == "minor_qms"

    task_id = await _my_pending_task(app_client, hq, iid)
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hq, json={"outcome": "approve"}
    )
    assert dr.status_code == 200, dr.text
    decision = dr.json()
    assert decision["current_state"] == "COMPLETED"
    assert decision["dcr_state"] == "Approved"
    assert decision["signature_event_id"]
    assert (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hr)).json()[
        "state"
    ] == "Approved"
    assert await _approval_sig_count(dcr_id) == 1


async def test_route_stage_events_have_strict_causal_order(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The atomic Assessed→Routed→InApproval route writes two events in one transaction. Their
    timestamps must be strictly increasing so the public trail cannot render the pair backwards."""
    req = _subject("dcr-route-order")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    qm = _subject("dcr-route-order-qms")
    await _assign_seeded_role(qm, "QMS Owner")

    dcr_id = await _open_assessed_dcr(app_client, hr, "MINOR")
    routed = await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)
    assert routed.status_code == 200, routed.text

    detail = (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hr)).json()
    events = detail["stage_events"]
    assert [(event["from_state"], event["to_state"]) for event in events[-2:]] == [
        ("Assessed", "Routed"),
        ("Routed", "InApproval"),
    ]
    routed_at, in_approval_at = (
        datetime.datetime.fromisoformat(event["occurred_at"]) for event in events[-2:]
    )
    assert routed_at < in_approval_at


async def test_equal_timestamp_stage_events_follow_transition_chain(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Legacy equal-timestamp route pairs follow from→to adjacency, not heap or UUID order. Insert
    the later event physically first and give it the smaller UUID so both accidental orderings are
    opposite the causal trail."""
    req = _subject("dcr-route-tie")
    actor_id = await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    dcr_id = await _open_assessed_dcr(app_client, hr, "MINOR")

    in_approval_id, routed_id = sorted((uuid.uuid4(), uuid.uuid4()))
    tied_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
    async with get_sessionmaker()() as s:
        actor = await s.get(AppUser, actor_id)
        assert actor is not None
        # Flush the later transition first so an occurred_at-only query returns the wrong order.
        s.add(
            DcrStageEvent(
                id=in_approval_id,
                org_id=actor.org_id,
                dcr_id=uuid.UUID(dcr_id),
                from_state=DcrState.Routed,
                to_state=DcrState.InApproval,
                actor_id=actor.id,
                occurred_at=tied_at,
            )
        )
        await s.flush()
        s.add(
            DcrStageEvent(
                id=routed_id,
                org_id=actor.org_id,
                dcr_id=uuid.UUID(dcr_id),
                from_state=DcrState.Assessed,
                to_state=DcrState.Routed,
                actor_id=actor.id,
                occurred_at=tied_at,
            )
        )
        await s.commit()

    events = (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hr)).json()["stage_events"]
    tied = [event for event in events if event["id"] in {str(routed_id), str(in_approval_id)}]
    assert [event["id"] for event in tied] == [str(routed_id), str(in_approval_id)]
    assert [event["to_state"] for event in tied] == ["Routed", "InApproval"]


async def test_approval_decision_idempotent_replay(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # Re-sending the same Idempotency-Key after a completing approve replays byte-identically — the
    # response (incl. signature_event_id, re-derived from signature_event) matches; no extra sig.
    req = _subject("dcr-replay")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    qm = _subject("dcr-qms-replay")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MINOR")
    iid = (await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)).json()[
        "approval_instance"
    ]["id"]
    task_id = await _my_pending_task(app_client, hq, iid)
    key = uuid.uuid4().hex
    hk = {**hq, "Idempotency-Key": key}
    first = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hk, json={"outcome": "approve"}
    )
    assert first.status_code == 200, first.text
    second = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hk, json={"outcome": "approve"}
    )
    assert second.status_code == 200, second.text
    assert second.json()["dcr_state"] == first.json()["dcr_state"] == "Approved"
    assert second.json()["signature_event_id"] == first.json()["signature_event_id"]
    assert second.json()["signature_event_id"] is not None
    assert await _approval_sig_count(dcr_id) == 1  # the replay wrote NO second signature


async def test_major_dcr_two_stage_two_signatures(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    req = _subject("dcr-route-major")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    proc = _subject("dcr-proc")
    await _assign_seeded_role(proc, "Process Owner")
    hp = _auth(token_factory, proc)
    qm = _subject("dcr-qms-major")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MAJOR")
    iid = (await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)).json()[
        "approval_instance"
    ]["id"]

    # Stage 1: Process Owner — advances but does NOT complete (state stays InApproval).
    t1 = await _my_pending_task(app_client, hp, iid)
    d1 = await app_client.post(
        f"/api/v1/tasks/{t1}/decision", headers=hp, json={"outcome": "approve"}
    )
    assert d1.status_code == 200, d1.text
    assert d1.json()["dcr_state"] == "InApproval"
    assert d1.json()["current_state"] != "COMPLETED"

    # Stage 2: QMS Owner — completes → Approved.
    t2 = await _my_pending_task(app_client, hq, iid)
    d2 = await app_client.post(
        f"/api/v1/tasks/{t2}/decision", headers=hq, json={"outcome": "approve"}
    )
    assert d2.status_code == 200, d2.text
    assert d2.json()["current_state"] == "COMPLETED"
    assert d2.json()["dcr_state"] == "Approved"
    # doc 05 §5.4: EACH approval signs → MAJOR = two signature_events.
    assert await _approval_sig_count(dcr_id) == 2


async def test_second_tier_task_inherits_the_definition_default_sla(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[Batch 9] A stage-advance must thread the definition's ``default_sla`` (migration 0043 sets
    120h for dcr_approval), so the SECOND-tier QMS-Owner task gets a real ``due_at``. Without it the
    task was created with due_at=NULL → no due-soon reminder, never OVERDUE, never escalated, and
    the DCR sat InApproval forever while the org believed the R29/R55 SLA machinery covered it.
    Mutation-distinguishing: this asserts due_at IS NOT NULL, which fails on the old `None` arg."""
    req = _subject("dcr-sla-major")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    proc = _subject("dcr-sla-proc")
    await _assign_seeded_role(proc, "Process Owner")
    hp = _auth(token_factory, proc)
    qm = _subject("dcr-sla-qms")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MAJOR")
    iid = (await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)).json()[
        "approval_instance"
    ]["id"]

    # Stage 1 (Process Owner) already carries a due date from the same default_sla at instantiate.
    t1 = await _my_pending_task(app_client, hp, iid)
    async with get_sessionmaker()() as s:
        due1 = (await s.execute(select(Task.due_at).where(Task.id == uuid.UUID(t1)))).scalar_one()
    assert due1 is not None, "stage-1 task should carry the definition default_sla due date"

    # Advance to stage 2 — the task the ADVANCE materializes must also carry a due date.
    assert (
        await app_client.post(
            f"/api/v1/tasks/{t1}/decision", headers=hp, json={"outcome": "approve"}
        )
    ).status_code == 200
    t2 = await _my_pending_task(app_client, hq, iid)
    async with get_sessionmaker()() as s:
        due2 = (await s.execute(select(Task.due_at).where(Task.id == uuid.UUID(t2)))).scalar_one()
    assert due2 is not None, "second-tier task lost the definition default_sla (due_at is NULL)"


async def test_dcr_decision_rejects_an_unsupported_outcome(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[Batch 9] A DCR approval accepts ONLY approve / reject / changes_requested. ``verify`` is a
    legal TaskOutcomeKind the engine's ANY quorum treats as POSITIVE, so without the allow-list it
    completed the instance while matching neither DCR branch — leaving the DCR stuck InApproval with
    no live approval instance (permanently bricked). It must 422 and change nothing."""
    req = _subject("dcr-badoutcome")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    qm = _subject("dcr-badoutcome-qms")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MINOR")
    iid = (await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)).json()[
        "approval_instance"
    ]["id"]
    task_id = await _my_pending_task(app_client, hq, iid)

    bad = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hq, json={"outcome": "verify"}
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["errors"][0]["code"] == "unsupported_outcome"

    # The DCR is untouched and still decidable — the task remains open for a real decision.
    assert (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hr)).json()["state"] == (
        "InApproval"
    )
    assert await _approval_sig_count(dcr_id) == 0
    good = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hq, json={"outcome": "approve"}
    )
    assert good.status_code == 200, good.text
    assert good.json()["dcr_state"] == "Approved"


async def test_changes_requested_loops_to_open(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    req = _subject("dcr-cr")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    qm = _subject("dcr-qms-cr")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MINOR")
    iid = (await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)).json()[
        "approval_instance"
    ]["id"]
    task_id = await _my_pending_task(app_client, hq, iid)
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=hq,
        json={"outcome": "changes_requested", "comment": "tighten the scope"},
    )
    assert dr.status_code == 200, dr.text
    assert dr.json()["dcr_state"] == "Open"
    # No approval signature on a changes-requested loop.
    assert await _approval_sig_count(dcr_id) == 0
    # Re-route opens a FRESH instance (the prior one is terminal).
    re = await app_client.post(f"/api/v1/dcrs/{dcr_id}/assess", headers=hr)
    assert re.status_code == 200, re.text
    again = await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)
    assert again.status_code == 200, again.text
    assert again.json()["approval_instance"]["id"] != iid


async def test_reject_goes_to_rejected(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    req = _subject("dcr-rej")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    qm = _subject("dcr-qms-rej")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MINOR")
    iid = (await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)).json()[
        "approval_instance"
    ]["id"]
    task_id = await _my_pending_task(app_client, hq, iid)
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hq, json={"outcome": "reject"}
    )
    assert dr.status_code == 200, dr.text
    assert dr.json()["dcr_state"] == "Rejected"
    assert await _approval_sig_count(dcr_id) == 0


async def test_cross_stage_distinct_approver_guard(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # One user holding BOTH the Process-Owner and QMS-Owner roles cannot clear both MAJOR tiers —
    # the cross-stage distinct-approver guard 409s their second decision (the deterministic check;
    # an "empty pool → 409" assertion is unreliable in the shared session DB other tests populate).
    req = _subject("dcr-distinct")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    dual = _subject("dcr-dual")
    await _assign_seeded_role(dual, "Process Owner")
    await _assign_seeded_role(dual, "QMS Owner")
    hd = _auth(token_factory, dual)

    dcr_id = await _open_assessed_dcr(app_client, hr, "MAJOR")
    iid = (await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)).json()[
        "approval_instance"
    ]["id"]
    t1 = await _my_pending_task(app_client, hd, iid)
    d1 = await app_client.post(
        f"/api/v1/tasks/{t1}/decision", headers=hd, json={"outcome": "approve"}
    )
    assert d1.status_code == 200, d1.text
    assert d1.json()["dcr_state"] == "InApproval"
    # The same user also holds QMS Owner → their stage-2 task exists, but the cross-stage guard
    # 409s.
    t2 = await _my_pending_task(app_client, hd, iid)
    d2 = await app_client.post(
        f"/api/v1/tasks/{t2}/decision", headers=hd, json={"outcome": "approve"}
    )
    assert d2.status_code == 409, d2.text


async def test_major_dcr_unstaffable_second_stage_409s_and_stays_retryable(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Audit C6: with the QMS-Owner pool EMPTY at stage-1 approval, the engine used to advance to
    terminal NEEDS_ATTENTION while the DCR stayed InApproval with no exit — a permanent wedge.
    Now the approve rolls back with 409 dcr_no_approvers, NOTHING is recorded (no
    signature, the stage-1 task stays PENDING), and the same approve succeeds once the role is
    staffed. QMS-Owner assignments from other tests are snapshotted/restored around the window."""
    from easysynq_api.db.models.role import Role, RoleAssignment

    req = _subject("dcr-wedge-req")
    await _grant(req, _ROUTE_PERMS)
    hr = _auth(token_factory, req)
    proc = _subject("dcr-wedge-proc")
    await _assign_seeded_role(proc, "Process Owner")
    hp = _auth(token_factory, proc)

    async with get_sessionmaker()() as s:
        proc_user = (
            await s.execute(select(AppUser).where(AppUser.keycloak_subject == proc))
        ).scalar_one()
        org_id = proc_user.org_id
        qms_role_id = (
            await s.execute(select(Role.id).where(Role.org_id == org_id, Role.name == "QMS Owner"))
        ).scalar_one()
        saved = [
            (row.org_id, row.user_id, row.role_id)
            for row in (
                await s.execute(select(RoleAssignment).where(RoleAssignment.role_id == qms_role_id))
            ).scalars()
        ]
        for row in (
            await s.execute(select(RoleAssignment).where(RoleAssignment.role_id == qms_role_id))
        ).scalars():
            await s.delete(row)
        await s.commit()

    try:
        dcr_id = await _open_assessed_dcr(app_client, hr, "MAJOR")
        iid = (await app_client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=hr)).json()[
            "approval_instance"
        ]["id"]
        t1 = await _my_pending_task(app_client, hp, iid)
        d1 = await app_client.post(
            f"/api/v1/tasks/{t1}/decision", headers=hp, json={"outcome": "approve"}
        )
        assert d1.status_code == 409, d1.text
        assert d1.json()["code"] == "dcr_no_approvers"

        # Nothing was recorded: no approval signature, the DCR still InApproval, and the SAME
        # stage-1 task is still PENDING (the retry affordance).
        assert await _approval_sig_count(dcr_id) == 0
        detail = (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hr)).json()
        assert detail["state"] == "InApproval"
        assert await _my_pending_task(app_client, hp, iid) == t1
    finally:
        async with get_sessionmaker()() as s:
            for org, user, role in saved:
                s.add(RoleAssignment(org_id=org, user_id=user, role_id=role))
            await s.commit()

    # Staff the second tier, then the SAME approve succeeds and the flow completes normally.
    qm = _subject("dcr-wedge-qms")
    await _assign_seeded_role(qm, "QMS Owner")
    hq = _auth(token_factory, qm)
    d1b = await app_client.post(
        f"/api/v1/tasks/{t1}/decision", headers=hp, json={"outcome": "approve"}
    )
    assert d1b.status_code == 200, d1b.text
    assert d1b.json()["dcr_state"] == "InApproval"
    t2 = await _my_pending_task(app_client, hq, iid)
    d2 = await app_client.post(
        f"/api/v1/tasks/{t2}/decision", headers=hq, json={"outcome": "approve"}
    )
    assert d2.status_code == 200, d2.text
    assert d2.json()["dcr_state"] == "Approved"
    assert await _approval_sig_count(dcr_id) == 2
