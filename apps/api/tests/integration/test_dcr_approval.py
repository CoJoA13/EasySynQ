"""S-dcr-4 integration proofs — DCR routing + approval via the declarative engine over HTTP.

The approval candidate pool resolves by Role MEMBERSHIP (``users_with_roles``), NOT by SYSTEM
permission overrides — so an approver must be ASSIGNED the seeded ``Process Owner`` / ``QMS
Owner`` role (the S-capa-2 gotcha). Per-approver signatures (doc 05 §5.4): a MAJOR DCR yields TWO
``signature_event(meaning=approval, signed_object_type=dcr)`` rows, a MINOR yields one.
Assertions are run-scoped to this run's DCR / instance.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models.signature_event import SignatureEvent
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
