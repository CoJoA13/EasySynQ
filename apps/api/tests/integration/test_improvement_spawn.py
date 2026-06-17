"""S-improvement-2 integration proofs — the two Improvement-Initiative spawn endpoints (ISO 10.3,
R46): ``POST /findings/{id}/raise-initiative`` (OFI/OBSERVATION origin) and
``POST /management-reviews/{id}/outputs/{oid}/raise-initiative`` (MR-output origin).

Both are 1:N + Idempotency-Key recording acts: they compose ``create_initiative(_commit=False)`` and
mint NO signature (R43). The spawn gates on ``improvement.manage`` (NOT capa.*/finding.* — R46). The
MR-output spawn leaves ``review_output.spawned_initiative_id`` reserved-null (R46). Assertions are
scoped to **this run's own** initiative ids — the integration suite shares one session DB across
files, so absolute counts are never asserted (the test_improvement / test_mgmt_review_actions rule).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.review_output import ReviewOutput
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_audits import _FINDING_KEYS
from .test_improvement import _event_count, _grant, _grant_process, _seed_process, _subject
from .test_mgmt_review import _MR_KEYS, _auth, _create_review, _drive_review_to_release
from .test_mgmt_review import _grant as _mr_grant

pytestmark = pytest.mark.integration


# --- finding-origin helpers -------------------------------------------------------------------


async def _new_audit_with_process(client: AsyncClient, h: dict[str, str], process_id: str) -> str:
    """Programme → plan (auditee_process = ``process_id``) → audit → walk to InProgress so findings
    can be logged. The auditee process is what a finding-spawned initiative inherits (R28)."""
    program_id = (
        await client.post("/api/v1/audit-programs", headers=h, json={"title": "P"})
    ).json()["id"]
    plan_id = (
        await client.post(
            f"/api/v1/audit-programs/{program_id}/plans",
            headers=h,
            json={"auditee_process_id": process_id},
        )
    ).json()["id"]
    audit_id = (await client.post("/api/v1/audits", headers=h, json={"plan_id": plan_id})).json()[
        "id"
    ]
    for action in ("plan", "conduct"):
        r = await client.post(f"/api/v1/audits/{audit_id}/{action}", headers=h)
        assert r.status_code == 200, f"{action}: {r.text}"
    return audit_id


async def _new_finding(
    client: AsyncClient, h: dict[str, str], audit_id: str, finding_type: str, **extra: object
) -> dict[str, object]:
    r = await client.post(
        f"/api/v1/audits/{audit_id}/findings",
        headers=h,
        json={"finding_type": finding_type, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- finding-origin tests ---------------------------------------------------------------------


async def test_raise_initiative_from_ofi_finding(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("imp-ofi")
    proc_id = await _seed_process(subject)
    await _grant(subject, (*_FINDING_KEYS, "improvement.read", "improvement.manage"))
    h = _auth(token_factory, subject)
    audit_id = await _new_audit_with_process(app_client, h, proc_id)
    finding = await _new_finding(app_client, h, audit_id, "OFI", clause_ref="10.3")

    r = await app_client.post(
        f"/api/v1/findings/{finding['id']}/raise-initiative",
        headers=h,
        json={"title": "Streamline supplier onboarding", "target_outcome": "lead time -30%"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source"] == "OFI"
    assert body["source_link_id"] == finding["id"]
    assert body["process_id"] == proc_id  # inherits the audited process (R28)
    assert body["stage"] == "Open"
    assert body["identifier"].startswith("IMP-")

    # NO signature on a recording act (R43); the INITIATIVE_RAISED audit fired for this id.
    async with get_sessionmaker()() as s:
        sigs = (
            (
                await s.execute(
                    select(SignatureEvent).where(
                        SignatureEvent.signed_object_id == uuid.UUID(body["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sigs == []
    assert await _event_count(body["id"], EventType.INITIATIVE_RAISED) == 1


async def test_raise_initiative_from_observation_finding(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An OBSERVATION finding is also improvable (eligibility set {OBSERVATION, OFI}); both map to
    ``source=OFI`` (the finding's real type is recoverable via the link)."""
    subject = _subject("imp-obs")
    proc_id = await _seed_process(subject)
    await _grant(subject, (*_FINDING_KEYS, "improvement.manage"))
    h = _auth(token_factory, subject)
    audit_id = await _new_audit_with_process(app_client, h, proc_id)
    finding = await _new_finding(app_client, h, audit_id, "OBSERVATION")

    r = await app_client.post(
        f"/api/v1/findings/{finding['id']}/raise-initiative",
        headers=h,
        json={"title": "Tidy the supplier index"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "OFI"
    assert r.json()["source_link_id"] == finding["id"]


async def test_raise_initiative_from_nc_finding_is_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An NC is corrective-action work (it carries its mandatory CAPA), NOT an improvement → 422."""
    subject = _subject("imp-nc")
    proc_id = await _seed_process(subject)
    await _grant(subject, (*_FINDING_KEYS, "improvement.manage"))
    h = _auth(token_factory, subject)
    audit_id = await _new_audit_with_process(app_client, h, proc_id)
    finding = await _new_finding(app_client, h, audit_id, "NC", severity="Major")

    r = await app_client.post(
        f"/api/v1/findings/{finding['id']}/raise-initiative",
        headers=h,
        json={"title": "x"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "finding_not_improvable"


async def test_raise_initiative_unknown_finding_is_404(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A SYSTEM improvement.manage holder reaches the service (the scope resolver SYSTEM-falls-back
    on an unknown id) → the service raises the real 404, not the gate."""
    subject = _subject("imp-404")
    await _grant(subject, ("improvement.manage",))
    h = _auth(token_factory, subject)
    r = await app_client.post(
        f"/api/v1/findings/{uuid.uuid4()}/raise-initiative", headers=h, json={"title": "x"}
    )
    assert r.status_code == 404, r.text


async def test_raise_initiative_from_finding_requires_manage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Affordance: a caller who can create findings but lacks ``improvement.manage`` is denied the
    spawn (deny-by-default; an auditor raises the OFI, a Process Owner turns it into one)."""
    subject = _subject("imp-noperm")
    proc_id = await _seed_process(subject)
    await _grant(subject, _FINDING_KEYS)  # no improvement.manage
    h = _auth(token_factory, subject)
    audit_id = await _new_audit_with_process(app_client, h, proc_id)
    finding = await _new_finding(app_client, h, audit_id, "OFI")

    r = await app_client.post(
        f"/api/v1/findings/{finding['id']}/raise-initiative", headers=h, json={"title": "x"}
    )
    assert r.status_code == 403, r.text


async def test_raise_initiative_from_finding_idempotency(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An Idempotency-Key replays the SAME initiative (200, original body wins); a new key spawns a
    fresh one (201, 1:N per R46)."""
    subject = _subject("imp-idem")
    proc_id = await _seed_process(subject)
    await _grant(subject, (*_FINDING_KEYS, "improvement.manage"))
    h = _auth(token_factory, subject)
    audit_id = await _new_audit_with_process(app_client, h, proc_id)
    finding = await _new_finding(app_client, h, audit_id, "OFI")
    url = f"/api/v1/findings/{finding['id']}/raise-initiative"
    key = uuid.uuid4().hex

    first = await app_client.post(url, headers={**h, "Idempotency-Key": key}, json={"title": "A"})
    assert first.status_code == 201, first.text
    replay = await app_client.post(url, headers={**h, "Idempotency-Key": key}, json={"title": "B"})
    assert replay.status_code == 200, replay.text  # 200 == replay, not a new initiative
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["title"] == "A"  # the original wins; the retry body is ignored
    other = await app_client.post(
        url, headers={**h, "Idempotency-Key": uuid.uuid4().hex}, json={"title": "C"}
    )
    assert other.status_code == 201, other.text
    assert other.json()["id"] != first.json()["id"]


async def test_raise_initiative_from_superseded_finding_is_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A corrected (superseded) finding is no longer the live finding → 409 finding_superseded; its
    live successor IS improvable (Codex P2)."""
    subject = _subject("imp-sup")
    proc_id = await _seed_process(subject)
    await _grant(subject, (*_FINDING_KEYS, "improvement.manage"))
    h = _auth(token_factory, subject)
    audit_id = await _new_audit_with_process(app_client, h, proc_id)
    finding = await _new_finding(app_client, h, audit_id, "OBSERVATION")
    # correct it (OBS → OFI): captures a superseding successor, marking the original superseded
    corr = await app_client.post(
        f"/api/v1/findings/{finding['id']}/correction",
        headers=h,
        json={"finding_type": "OFI", "reason": "reclassified as an improvement"},
    )
    assert corr.status_code == 201, corr.text

    blocked = await app_client.post(
        f"/api/v1/findings/{finding['id']}/raise-initiative", headers=h, json={"title": "stale"}
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "finding_superseded"

    # the live successor is improvable
    ok = await app_client.post(
        f"/api/v1/findings/{corr.json()['id']}/raise-initiative", headers=h, json={"title": "live"}
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["source"] == "OFI"


# --- MR-output-origin helpers -----------------------------------------------------------------


async def _action_output_id(client: AsyncClient, h: dict[str, str], rid: str) -> str:
    det = (await client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    return next(o["id"] for o in det["outputs"] if o["output_type"] == "ACTION")


async def _drive_review_with_outputs(
    client: AsyncClient,
    token_factory: Callable[..., str],
    salt: str,
    outputs: list[dict[str, object]],
) -> str:
    """Author ``outputs`` on a fresh review, submit → approve → release (the
    ``_drive_review_to_release`` shape, but with caller-supplied output kinds so an IMPROVEMENT
    output can be released-and-tracking). Returns the released review id."""
    submitter, approver, releaser = f"mr-sm-{salt}", f"mr-ap-{salt}", f"mr-rl-{salt}"
    hs = _auth(token_factory, submitter)
    hap = _auth(token_factory, approver)
    hrl = _auth(token_factory, releaser)
    await _mr_grant(submitter, _MR_KEYS)
    await s5.grant_role(approver, "Approver")
    await _mr_grant(releaser, ("document.release", "document.read", "document.read_draft"))

    rid = await _create_review(client, hs, f"Improvement review {salt}")
    for o in outputs:
        r = await client.post(f"/api/v1/management-reviews/{rid}/outputs", headers=hs, json=o)
        assert r.status_code == 201, r.text
    sub = await client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=hs)
    assert sub.status_code == 200, sub.text
    task_id = await s5.task_for_doc(rid)
    dec = await client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await client.post(f"/api/v1/management-reviews/{rid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["close_state"] == "ActionsTracked"
    return rid


# --- MR-output-origin tests -------------------------------------------------------------------


async def test_raise_initiative_from_action_output(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _mr_grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _mr_grant(f"mr-sm-{salt}", ("improvement.read", "improvement.manage"))
    oid = await _action_output_id(app_client, hs, rid)

    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-initiative",
        headers=hs,
        json={"title": "Improve action follow-through"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source"] == "review"
    assert body["source_link_id"] == oid
    assert body["stage"] == "Open"

    async with get_sessionmaker()() as s:
        # the MR-side spawn audit fired against the MR document
        ev = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == EventType.MGMT_REVIEW_INITIATIVE_SPAWNED,
                        AuditEvent.object_type == AuditObjectType.document,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any(e.after.get("initiative_id") == body["id"] for e in ev)
        # NO signature (R43)
        sigs = (
            (
                await s.execute(
                    select(SignatureEvent).where(
                        SignatureEvent.signed_object_id == uuid.UUID(body["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sigs == []
        # the reciprocal latch stays reserved-null (R46 — un-reserving is a future owner call)
        ro = (
            await s.execute(select(ReviewOutput).where(ReviewOutput.id == uuid.UUID(oid)))
        ).scalar_one()
        assert ro.spawned_initiative_id is None


async def test_raise_initiative_from_improvement_output(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The reserved IMPROVEMENT output type is eligible (the owner-confirmed {ACTION, IMPROVEMENT}
    set) → 201."""
    salt = uuid.uuid4().hex[:8]
    rid = await _drive_review_with_outputs(
        app_client,
        token_factory,
        salt,
        [{"output_type": "IMPROVEMENT", "description": "Pilot a kaizen board"}],
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _mr_grant(f"mr-sm-{salt}", ("improvement.manage",))
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=hs)).json()
    oid = next(o["id"] for o in det["outputs"] if o["output_type"] == "IMPROVEMENT")

    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-initiative",
        headers=hs,
        json={"title": "Kaizen rollout"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "review"
    assert r.json()["source_link_id"] == oid


async def test_raise_initiative_from_decision_output_is_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A DECISION output is not improvable → 422 (eligibility is checked before the state gate, so a
    Draft review is enough to prove it)."""
    salt = uuid.uuid4().hex[:8]
    sub = f"mr-sm-{salt}"
    hs = _auth(token_factory, sub)
    await _mr_grant(sub, (*_MR_KEYS, "improvement.manage"))
    rid = await _create_review(app_client, hs, f"Decision review {salt}")
    out = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=hs,
        json={"output_type": "DECISION", "description": "Approve the budget"},
    )
    assert out.status_code == 201, out.text

    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{out.json()['id']}/raise-initiative",
        headers=hs,
        json={"title": "x"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "output_not_improvable"


async def test_raise_initiative_mr_review_not_tracking_is_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A spawn against an eligible output of a never-released (Draft) review → 409 (the close-state
    gate; replay runs first but there is no key here)."""
    salt = uuid.uuid4().hex[:8]
    sub = f"mr-sm-{salt}"
    hs = _auth(token_factory, sub)
    await _mr_grant(sub, (*_MR_KEYS, "improvement.manage"))
    rid = await _create_review(app_client, hs, f"Untracked review {salt}")
    out = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=hs,
        json={"output_type": "IMPROVEMENT", "description": "An idea"},
    )
    assert out.status_code == 201, out.text

    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{out.json()['id']}/raise-initiative",
        headers=hs,
        json={"title": "x"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "review_not_tracking"


async def test_raise_initiative_mr_404_on_unknown_output(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _mr_grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _mr_grant(f"mr-sm-{salt}", ("improvement.manage",))
    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{uuid.uuid4()}/raise-initiative",
        headers=hs,
        json={"title": "x"},
    )
    assert r.status_code == 404, r.text


async def test_raise_initiative_mr_idempotency_replays(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _mr_grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _mr_grant(f"mr-sm-{salt}", ("improvement.manage",))
    oid = await _action_output_id(app_client, hs, rid)
    url = f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-initiative"
    key = uuid.uuid4().hex

    first = await app_client.post(url, headers={**hs, "Idempotency-Key": key}, json={"title": "A"})
    assert first.status_code == 201, first.text
    replay = await app_client.post(url, headers={**hs, "Idempotency-Key": key}, json={"title": "B"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    other = await app_client.post(
        url, headers={**hs, "Idempotency-Key": uuid.uuid4().hex}, json={"title": "C"}
    )
    assert other.status_code == 201, other.text
    assert other.json()["id"] != first.json()["id"]


async def test_raise_initiative_mr_idempotency_replays_after_close(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The idempotency lookup runs BEFORE the close-state gate (the spawn_dcr Codex-P2 lesson): a
    retry with the same key after the review is Closed replays the initiative (200), not 409."""
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _mr_grant(owner_sub, ())
    ho = _auth(token_factory, owner_sub)
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")  # holds the MR keys (so it can close) + improvement
    await _mr_grant(f"mr-sm-{salt}", ("improvement.manage",))
    oid = await _action_output_id(app_client, hs, rid)
    url = f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-initiative"
    key = uuid.uuid4().hex

    first = await app_client.post(url, headers={**hs, "Idempotency-Key": key}, json={"title": "A"})
    assert first.status_code == 201, first.text

    # close the review: complete the lone MR_ACTION task, then close as the MR-keys holder
    tasks = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    action_task = next(t for t in tasks if t["assignee_user_id"] == str(owner_id))
    done = await app_client.post(
        f"/api/v1/tasks/{action_task['id']}/decision", headers=ho, json={"outcome": "complete"}
    )
    assert done.status_code == 200, done.text
    closed = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs)
    assert closed.status_code == 200, closed.text
    assert closed.json()["close_state"] == "Closed"

    # retry the SAME key AFTER close → replays (200, same initiative), NOT 409 review_not_tracking
    replay = await app_client.post(url, headers={**hs, "Idempotency-Key": key}, json={"title": "B"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]


async def test_raise_initiative_mr_replay_rejects_foreign_process_scope(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Codex P2: an idempotent replay re-authorizes the STORED initiative scope. A caller granted
    only on process B cannot read an A-scoped initiative via a known key — gate-1 (their own process
    B) passes, but gate-2 against the stored scope (A) denies → 403."""
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _mr_grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    # creator: PROCESS-A-scoped improvement.manage; raises an A-scoped initiative from the output
    creator = f"mr-sm-{salt}"
    hs = _auth(token_factory, creator)
    proc_a = await _seed_process(creator)
    await _grant_process(creator, "improvement.manage", proc_a)
    oid = await _action_output_id(app_client, hs, rid)
    url = f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-initiative"
    key = uuid.uuid4().hex

    first = await app_client.post(
        url, headers={**hs, "Idempotency-Key": key}, json={"title": "A", "process_id": proc_a}
    )
    assert first.status_code == 201, first.text
    assert first.json()["process_id"] == proc_a

    # a caller scoped only to process B replays the same (output, key) with process_id=B → 403
    other = _subject("imp-otherproc")
    proc_b = await _seed_process(other)
    await _grant_process(other, "improvement.manage", proc_b)
    ho = _auth(token_factory, other)
    replay = await app_client.post(
        url, headers={**ho, "Idempotency-Key": key}, json={"title": "B", "process_id": proc_b}
    )
    assert replay.status_code == 403, replay.text
