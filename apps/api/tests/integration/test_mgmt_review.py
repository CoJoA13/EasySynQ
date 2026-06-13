"""S-mr-1 integration (Phase 3): create → list → detail of a Management Review, plus the submit
freeze (Draft → InReview + a ``mgmt_review_minutes`` snapshot). Grants are SYSTEM-scope
PermissionOverrides on JIT users (the test_quality_objectives / test_objective_lifecycle harness).

The full submit → approve → release → 9.3-star-COVERED lifecycle (the headline) is at the foot."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_quality_objectives import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration

# mgmtReview.create to create, .read to list/detail, .record_outputs to author/submit.
_MR_KEYS = ("mgmtReview.create", "mgmtReview.read", "mgmtReview.record_outputs")

# The six sourced-read keys the compiler PDP-checks against the review OWNER (verbatim from the live
# endpoints; complaints ride record.read — there is no complaint.read key). With the union held, all
# six sourced rows compile available=True.
_SOURCE_KEYS = (
    "objective.read",
    "audit.read",
    "capa.read",
    "ncr.read",
    "record.read",
    "kpi.read",
    "report.compliance_checklist.read",
    "drift.read",
)

# The 12 canonical 9.3.2 input types (enum-declaration order). 6 sourced + 6 sourceless gap.
_SOURCED_TYPES = {
    "OBJECTIVES_STATUS",
    "PROCESS_PERFORMANCE",
    "NONCONFORMITIES_CAPA",
    "MONITORING_RESULTS",
    "AUDIT_RESULTS",
    "PRIOR_ACTIONS",  # sourced-but-gap until a 2nd review exists (v1)
}
_SOURCELESS_TYPES = {
    "CONTEXT_CHANGES",
    "CUSTOMER_SATISFACTION",
    "SUPPLIER_PERFORMANCE",
    "RESOURCE_ADEQUACY",
    "RISK_OPPORTUNITY_ACTIONS",
    "IMPROVEMENT_OPPORTUNITIES",
}


async def _create_review(client: AsyncClient, h: dict[str, str], title: str) -> str:
    r = await client.post(
        "/api/v1/management-reviews",
        headers=h,
        json={"title": title, "period_label": "2026 Annual"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_create_list_detail_management_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)

    rid = await _create_review(app_client, h, "Q2 2026 Review")

    # list (the register) contains the new review
    lst = (await app_client.get("/api/v1/management-reviews", headers=h)).json()
    assert any(row["id"] == rid for row in lst["data"]), lst

    # detail: Draft, mapped identity, the inputs/outputs collections present (empty pre-compile)
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    assert det["id"] == rid
    assert det["current_state"] == "Draft"
    assert det["period_label"] == "2026 Annual"
    assert det["close_state"] is None
    assert det["inputs"] == []
    assert det["outputs"] == []


async def test_create_requires_mgmt_review_create(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    reader = f"mr-rdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("mgmtReview.read",))
    r = await app_client.post(
        "/api/v1/management-reviews", headers=hr, json={"title": "No create key"}
    )
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "permission_denied"


async def test_add_output_action_requires_owner(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-out-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, h, "Outputs review")

    # an ACTION output with no owner is a 422 (it would spawn an ownerless MR_ACTION at release)
    bad = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=h,
        json={"output_type": "ACTION", "description": "Tighten supplier controls"},
    )
    assert bad.status_code == 422, bad.text

    # a DECISION output records cleanly
    ok = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=h,
        json={"output_type": "DECISION", "description": "QMS remains suitable and effective"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["output_type"] == "DECISION"

    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    assert len(det["outputs"]) == 1


async def test_submit_freezes_minutes_and_enters_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-sub-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, h, "Submittable review")

    # set the review meta the freeze reads into the minutes
    meta = await app_client.patch(
        f"/api/v1/management-reviews/{rid}",
        headers=h,
        json={"review_date": "2026-06-12", "attendees": [{"name": "Mara", "role": "QM"}]},
    )
    assert meta.status_code == 200, meta.text

    # author a decision so the frozen minutes carry an output
    await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=h,
        json={"output_type": "DECISION", "description": "Approve the objectives for 2026"},
    )

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["current_state"] == "InReview"

    # a Draft version exists with the frozen minutes in its metadata_snapshot
    async with get_sessionmaker()() as s:
        v = (
            await s.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(rid))
            )
        ).scalar_one()
        minutes = (v.metadata_snapshot or {}).get("mgmt_review_minutes")
        assert minutes is not None
        assert minutes["period_label"] == "2026 Annual"
        assert minutes["review_date"] == "2026-06-12"
        assert minutes["attendees"] == [{"name": "Mara", "role": "QM"}]
        descriptions = [o["description"] for o in minutes["outputs"]]
        assert "Approve the objectives for 2026" in descriptions


async def test_update_output_to_action_requires_owner(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-upd-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    owner_id = await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, h, "Update-output review")

    # author a DECISION output (no owner required)
    created = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=h,
        json={"output_type": "DECISION", "description": "QMS remains effective"},
    )
    assert created.status_code == 201, created.text
    output_id = created.json()["id"]

    # PATCH it to ACTION without an owner → 422 (an ACTION must keep an owner)
    bad = await app_client.patch(
        f"/api/v1/management-reviews/{rid}/outputs/{output_id}",
        headers=h,
        json={"output_type": "ACTION"},
    )
    assert bad.status_code == 422, bad.text

    # PATCH it to ACTION WITH an owner → 200
    ok = await app_client.patch(
        f"/api/v1/management-reviews/{rid}/outputs/{output_id}",
        headers=h,
        json={"output_type": "ACTION", "owner_user_id": str(owner_id)},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["output_type"] == "ACTION"
    assert ok.json()["owner_user_id"] == str(owner_id)


async def test_submit_twice_is_a_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-dbl-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, h, "Submit once")
    first = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert first.status_code == 200, first.text
    again = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert again.status_code == 409, again.text


async def test_compile_inputs_writes_all_twelve_rows(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A fully-granted owner compiles all 12 rows: the 6 sourced rows available=True with a summary,
    the 6 sourceless gap rows available=False with a reason (F4). PRIOR_ACTIONS is a sourced-but-gap
    row (no 2nd review yet)."""
    subject = f"mr-comp-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    # The creator IS the review owner (create_document sets owner_user_id=actor.id); grant the union
    # so every sourced read PDP-passes for the owner.
    await _grant(subject, _MR_KEYS + _SOURCE_KEYS)
    rid = await _create_review(app_client, h, "Fully-granted compile")

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert r.status_code == 200, r.text
    inputs = r.json()["inputs"]

    by_type = {ri["input_type"]: ri for ri in inputs}
    assert set(by_type) == _SOURCED_TYPES | _SOURCELESS_TYPES
    assert len(inputs) == 12

    # Every row's source_ref carries the envelope (available + generated_at).
    for ri in inputs:
        ref = ri["source_ref"]
        assert "available" in ref and "generated_at" in ref
        assert ref["available"] is ri["available"]

    # The five live sourced reads are available with a summary.
    for t in (
        "OBJECTIVES_STATUS",
        "PROCESS_PERFORMANCE",
        "NONCONFORMITIES_CAPA",
        "MONITORING_RESULTS",
        "AUDIT_RESULTS",
    ):
        assert by_type[t]["available"] is True, by_type[t]
        assert "summary" in by_type[t]["source_ref"], by_type[t]

    # The objectives scorecard summary has the EXACT by_rag key set.
    obj = by_type["OBJECTIVES_STATUS"]["source_ref"]["summary"]
    assert set(obj["by_rag"]) == {"green", "amber", "red", "unmeasured"}
    assert obj["on_target"] == obj["by_rag"]["green"]

    # PRIOR_ACTIONS + the six sourceless inputs are gap rows (available=False with a reason).
    for t in {"PRIOR_ACTIONS"} | _SOURCELESS_TYPES:
        assert by_type[t]["available"] is False, by_type[t]
        assert by_type[t]["source_ref"].get("reason"), by_type[t]


async def test_compile_inputs_owner_without_audit_read_yields_gap_row_not_403(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """F3: an owner LACKING audit.read yields an available=False AUDIT_RESULTS gap row — NOT a 403
    of the whole compile. The gate on the TRIGGER is the caller's record_outputs; per-source access
    is the owner's, fail-closed to a gap row."""
    subject = f"mr-noaud-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    # The union MINUS audit.read — every other sourced read stays available.
    keys = _MR_KEYS + tuple(k for k in _SOURCE_KEYS if k != "audit.read")
    await _grant(subject, keys)
    rid = await _create_review(app_client, h, "Owner missing audit.read")

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert r.status_code == 200, r.text  # NOT a 403 — the whole compile succeeds
    by_type = {ri["input_type"]: ri for ri in r.json()["inputs"]}

    aud = by_type["AUDIT_RESULTS"]
    assert aud["available"] is False, aud
    assert aud["source_ref"].get("reason") == "not available (insufficient access)", aud
    assert "summary" not in aud["source_ref"], aud

    # The other live sourced reads stay available (only the audit source gap-rowed).
    assert by_type["OBJECTIVES_STATUS"]["available"] is True
    assert by_type["AUDIT_RESULTS"]["available"] is False


async def test_recompile_replaces_the_working_input_set(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Re-compile (Draft) REPLACES the working review_input set (delete-then-insert) — the row count
    stays 12, never doubles."""
    subject = f"mr-recomp-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS + _SOURCE_KEYS)
    rid = await _create_review(app_client, h, "Re-compile review")

    first = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert first.status_code == 200, first.text
    assert len(first.json()["inputs"]) == 12

    second = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert second.status_code == 200, second.text
    assert len(second.json()["inputs"]) == 12  # replaced, not appended

    # The detail read also shows exactly 12 (no orphaned prior-compile rows).
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    assert len(det["inputs"]) == 12


async def test_compile_inputs_blocked_after_submit(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """compile-inputs is Draft-only — a 409 once the review leaves Draft (the snapshot is then the
    WORM authority)."""
    subject = f"mr-comp-late-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS + _SOURCE_KEYS)
    rid = await _create_review(app_client, h, "Compile-after-submit review")
    submitted = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert submitted.status_code == 200, submitted.text

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert r.status_code == 409, r.text


# --- Phase 5: outputs → work (spawn MR_ACTION at release + the decide leg) ----------------------


async def _drive_review_to_release(
    client: AsyncClient,
    token_factory: Callable[..., str],
    salt: str,
    *,
    action_owner_subject: str,
    action_owner_id: uuid.UUID,
) -> str:
    """Create a review, author an ACTION owned by ``action_owner_id``, submit → approve → release.
    The submitter owns/submits (holds the MR keys); a role-assigned approver clears the DOCUMENT
    approval task; a THIRD-party releaser holds document.release (SoD-2: author/approver ≠
    releaser). Returns the review id (now Effective)."""
    submitter, approver, releaser = f"mr-sm-{salt}", f"mr-ap-{salt}", f"mr-rl-{salt}"
    hs = _auth(token_factory, submitter)
    hap = _auth(token_factory, approver)
    hrl = _auth(token_factory, releaser)
    await _grant(submitter, _MR_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))

    rid = await _create_review(client, hs, f"Lifecycle review {salt}")
    # author an ACTION output owned by the action owner → spawns an MR_ACTION at release
    out = await client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=hs,
        json={
            "output_type": "ACTION",
            "description": "Tighten supplier controls",
            "owner_user_id": str(action_owner_id),
            "due_date": "2026-12-31",
        },
    )
    assert out.status_code == 201, out.text

    submitted = await client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=hs)
    assert submitted.status_code == 200, submitted.text
    task_id = await s5.task_for_doc(rid)
    dec = await client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await client.post(f"/api/v1/management-reviews/{rid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["current_state"] == "Effective"
    assert rel.json()["close_state"] == "ActionsTracked"
    return rid


async def test_release_spawns_mr_action_and_owner_can_complete_it(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The thesis of Phase 5: release of a review with an ACTION output spawns an MR_ACTION task for
    the owner (close_state → ActionsTracked), and the owner can decide it complete → DONE."""
    salt = uuid.uuid4().hex[:8]
    # the action OWNER is a distinct subject who holds only self-scoped task discovery (no MR keys)
    owner_subject = f"mr-own-{salt}"
    ho = _auth(token_factory, owner_subject)
    owner_id = await _grant(
        owner_subject, ()
    )  # JIT the app_user; no permission keys needed to decide

    rid = await _drive_review_to_release(
        app_client,
        token_factory,
        salt,
        action_owner_subject=owner_subject,
        action_owner_id=owner_id,
    )

    # the owner sees an MR_ACTION task in their self-scoped inbox (assignee OR candidate pool)
    tasks = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    mine = [t for t in tasks if t["assignee_user_id"] == str(owner_id)]
    assert len(mine) == 1, tasks
    action_task = mine[0]
    assert action_task["state"] == "PENDING"
    assert action_task["action_expected"] == "complete"
    assert action_task["candidate_pool"] == [str(owner_id)]
    # due_at = org-midnight of the action's due_date (2026-12-31), not now+hours
    assert action_task["due_at"] is not None
    assert action_task["due_at"].startswith("2026-12-31T00:00")

    # the output is stamped with its spawned task id (read with the submitter's MR keys)
    hs = _auth(token_factory, f"mr-sm-{salt}")
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=hs)).json()
    assert det["close_state"] == "ActionsTracked"
    action_out = next(o for o in det["outputs"] if o["output_type"] == "ACTION")
    assert action_out["spawned_task_id"] == action_task["id"]

    # the owner decides the action complete → DONE
    done = await app_client.post(
        f"/api/v1/tasks/{action_task['id']}/decision", headers=ho, json={"outcome": "complete"}
    )
    assert done.status_code == 200, done.text
    assert done.json()["outcome"] == "complete"

    refreshed = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    done_task = next(t for t in refreshed if t["id"] == action_task["id"])
    assert done_task["state"] == "DONE"


async def test_mr_action_decide_404_collapses_for_non_member(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A user who is neither the assignee nor in the candidate pool gets 404 (sensitive collapse),
    never a 403 that would leak the task's existence."""
    salt = uuid.uuid4().hex[:8]
    owner_subject = f"mr-own2-{salt}"
    owner_id = await _grant(owner_subject, ())

    await _drive_review_to_release(
        app_client,
        token_factory,
        salt,
        action_owner_subject=owner_subject,
        action_owner_id=owner_id,
    )
    ho = _auth(token_factory, owner_subject)
    tasks = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    action_task = next(t for t in tasks if t["assignee_user_id"] == str(owner_id))

    # a stranger (JIT app_user, no membership) is 404'd on the decide
    stranger = f"mr-str-{salt}"
    hx = _auth(token_factory, stranger)
    await _grant(stranger, ())  # JIT the row so it's a real, org-matched, non-member user
    r = await app_client.post(
        f"/api/v1/tasks/{action_task['id']}/decision", headers=hx, json={"outcome": "complete"}
    )
    assert r.status_code == 404, r.text


async def test_mr_action_decide_rejects_bad_outcome(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """MR_ACTION only accepts ``complete`` — any other outcome is a 422 (not a silent DONE)."""
    salt = uuid.uuid4().hex[:8]
    owner_subject = f"mr-own3-{salt}"
    owner_id = await _grant(owner_subject, ())
    await _drive_review_to_release(
        app_client,
        token_factory,
        salt,
        action_owner_subject=owner_subject,
        action_owner_id=owner_id,
    )
    ho = _auth(token_factory, owner_subject)
    tasks = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    action_task = next(t for t in tasks if t["assignee_user_id"] == str(owner_id))
    r = await app_client.post(
        f"/api/v1/tasks/{action_task['id']}/decision", headers=ho, json={"outcome": "approve"}
    )
    assert r.status_code == 422, r.text


# --- Phase 6: the close gate (spawned MR_ACTION tasks must be DONE) ------------------------------


async def test_close_blocked_while_action_open_then_closes_when_done(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The thesis of Phase 6: a released review with an open ACTION task cannot close (409
    ``review_close_blocked``); after the owner decides the action DONE, the close passes
    (``close_state == "Closed"``)."""
    salt = uuid.uuid4().hex[:8]
    owner_subject = f"mr-cl-own-{salt}"
    ho = _auth(token_factory, owner_subject)
    owner_id = await _grant(owner_subject, ())  # JIT the owner; no keys needed to decide

    rid = await _drive_review_to_release(
        app_client,
        token_factory,
        salt,
        action_owner_subject=owner_subject,
        action_owner_id=owner_id,
    )

    # the submitter holds the MR keys (mgmtReview.record_outputs gates /close)
    hs = _auth(token_factory, f"mr-sm-{salt}")

    # while the spawned MR_ACTION is PENDING, the close is blocked (409, fail-closed gate)
    blocked = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "review_close_blocked", blocked.text

    # the owner completes the action → DONE
    tasks = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    action_task = next(t for t in tasks if t["assignee_user_id"] == str(owner_id))
    done = await app_client.post(
        f"/api/v1/tasks/{action_task['id']}/decision", headers=ho, json={"outcome": "complete"}
    )
    assert done.status_code == 200, done.text

    # now the close passes — close_state flips to Closed
    closed = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs)
    assert closed.status_code == 200, closed.text
    assert closed.json()["close_state"] == "Closed", closed.text
    assert closed.json()["closed_at"] is not None, closed.text

    # the detail read reflects the closed state
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=hs)).json()
    assert det["close_state"] == "Closed"


async def test_close_requires_record_outputs_key(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The /close route is gated mgmtReview.record_outputs — a read-only caller gets 403."""
    salt = uuid.uuid4().hex[:8]
    owner_subject = f"mr-cl-key-{salt}"
    owner_id = await _grant(owner_subject, ())
    rid = await _drive_review_to_release(
        app_client,
        token_factory,
        salt,
        action_owner_subject=owner_subject,
        action_owner_id=owner_id,
    )
    reader = f"mr-cl-rdr-{salt}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("mgmtReview.read",))
    r = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hr)
    assert r.status_code == 403, r.text


async def test_decision_only_review_closes_immediately(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A review whose outputs are all DECISION/IMPROVEMENT (no ACTION) has an empty close gate — it
    closes immediately after release (nothing to track)."""
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"mr-d-sm-{salt}", f"mr-d-ap-{salt}", f"mr-d-rl-{salt}"
    hs = _auth(token_factory, submitter)
    hap = _auth(token_factory, approver)
    hrl = _auth(token_factory, releaser)
    await _grant(submitter, _MR_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))

    rid = await _create_review(app_client, hs, f"Decision-only review {salt}")
    out = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=hs,
        json={"output_type": "DECISION", "description": "QMS remains suitable and effective"},
    )
    assert out.status_code == 201, out.text

    submitted = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=hs)
    assert submitted.status_code == 200, submitted.text
    task_id = await s5.task_for_doc(rid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/management-reviews/{rid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["close_state"] == "ActionsTracked"

    # no ACTION outputs → the gate is empty → close passes immediately
    closed = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs)
    assert closed.status_code == 200, closed.text
    assert closed.json()["close_state"] == "Closed", closed.text


async def test_close_blocked_on_never_released_draft(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A never-released Draft review (``close_state is None``) cannot be closed — the precondition
    guard 409s ``review_not_open_to_close`` BEFORE the (empty) close gate would otherwise flip a
    still-Draft review to Closed (an incoherent terminal state). ``close_state`` stays null."""
    subject = f"mr-clstate-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)  # mgmtReview.record_outputs → the /close route is reachable
    rid = await _create_review(app_client, h, "Never-released review")

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "review_not_open_to_close", r.text

    # the review is untouched: still Draft, close_state still null (no flip, no closed_at)
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    assert det["current_state"] == "Draft"
    assert det["close_state"] is None


# --- Phase 8: the headline — a released Management Review flips the 9.3 ★ checklist node ----------


async def _clause_9_3_row(client: AsyncClient, h: dict[str, str]) -> dict:
    body = (await client.get("/api/v1/reports/compliance-checklist", headers=h)).json()
    return next(r for r in body["rows"] if r["number"] == "9.3")


async def test_released_review_flips_9_3_covered(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """THE thesis: a released Management Review (a kind=DOCUMENT subtype auto-mapped to clause 9.3)
    flips the 9.3 star compliance node PARTIAL->COVERED with ZERO checklist code — the S-obj-3
    mechanism (release sets ``current_effective_version_id``, the only thing the checklist counts).
    Delta-asserted: the ``-m integration`` suite shares one DB, so capture the count before."""
    salt = uuid.uuid4().hex[:8]
    # an org-wide checklist reader holding the SYSTEM report key (not the MR keys)
    reader = f"mr-chk-{salt}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("report.compliance_checklist.read",))

    before = await _clause_9_3_row(app_client, hr)
    eff0 = before["effective_count"]

    owner_subject = f"mr-cov-own-{salt}"
    owner_id = await _grant(owner_subject, ())
    await _drive_review_to_release(
        app_client,
        token_factory,
        salt,
        action_owner_subject=owner_subject,
        action_owner_id=owner_id,
    )

    after = await _clause_9_3_row(app_client, hr)
    assert after["effective_count"] == eff0 + 1, (eff0, after)
    assert after["status"] == "COVERED", after


# --- Codex review fixes: byte-path guard (#4) + concurrent-close race (#5) ----------------------


async def test_generic_byte_path_rejects_management_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Codex #4: an MR document must NOT be reachable through the generic /documents byte path —
    ``reject_objective_byte_path`` (which guards OBJ at checkout/checkin/start-revision/submit)
    now also rejects a ``ManagementReview``, so a generic check-in can't bypass the freeze + the
    release hook (an Effective review that cannot be closed)."""
    from easysynq_api.db.models.documented_information import DocumentedInformation
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.problems import ProblemException
    from easysynq_api.services.vault.service import reject_objective_byte_path

    salt = uuid.uuid4().hex[:8]
    subject = f"mr-bp-{salt}"
    hs = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, hs, f"Byte-path review {salt}")

    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(rid))
        assert doc is not None
        with pytest.raises(ProblemException) as ei:
            await reject_objective_byte_path(s, doc)
    assert ei.value.status == 422
    assert ei.value.errors is not None
    assert ei.value.errors[0]["code"] == "management_review_managed_via_reviews"


async def test_concurrent_close_serializes_to_one_winner(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Codex #5: two concurrent close requests on a released review must NOT both succeed — the
    ``FOR UPDATE`` re-check under lock serializes them to exactly one 200 (Closed) + one 409."""
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"mr-rc-sm-{salt}", f"mr-rc-ap-{salt}", f"mr-rc-rl-{salt}"
    hs = _auth(token_factory, submitter)
    hap = _auth(token_factory, approver)
    hrl = _auth(token_factory, releaser)
    await _grant(submitter, _MR_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))

    rid = await _create_review(app_client, hs, f"Race-close review {salt}")
    await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=hs,
        json={"output_type": "DECISION", "description": "QMS remains effective"},
    )
    await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=hs)
    task_id = await s5.task_for_doc(rid)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    rel = await app_client.post(f"/api/v1/management-reviews/{rid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text

    r1, r2 = await asyncio.gather(
        app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs),
        app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 409], (
        r1.status_code,
        r1.text,
        r2.status_code,
        r2.text,
    )
    winner = r1 if r1.status_code == 200 else r2
    assert winner.json()["close_state"] == "Closed"


async def test_two_actions_same_owner_both_decidable_then_close(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Regression (diff-critic MAJOR): a review with TWO ACTION outputs owned by the SAME user.
    The owner must complete BOTH MR_ACTION tasks (the second decision is 200, not a 409 from the
    engine's distinct-approver guard over a shared stage_key), then the review closes."""
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"mr2-sm-{salt}", f"mr2-ap-{salt}", f"mr2-rl-{salt}"
    hs = _auth(token_factory, submitter)
    hap = _auth(token_factory, approver)
    hrl = _auth(token_factory, releaser)
    await _grant(submitter, _MR_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))

    owner_subject = f"mr2-own-{salt}"
    ho = _auth(token_factory, owner_subject)
    owner_id = await _grant(owner_subject, ())

    rid = await _create_review(app_client, hs, f"Two-action review {salt}")
    for desc, due in (("Action one", "2026-11-30"), ("Action two", "2026-12-31")):
        out = await app_client.post(
            f"/api/v1/management-reviews/{rid}/outputs",
            headers=hs,
            json={
                "output_type": "ACTION",
                "description": desc,
                "owner_user_id": str(owner_id),
                "due_date": due,
            },
        )
        assert out.status_code == 201, out.text

    submitted = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=hs)
    assert submitted.status_code == 200, submitted.text
    task_id = await s5.task_for_doc(rid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/management-reviews/{rid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text

    # the owner holds TWO MR_ACTION tasks; completing BOTH must succeed (the 2nd is NOT a 409)
    tasks = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    mine = [t for t in tasks if t["assignee_user_id"] == str(owner_id)]
    assert len(mine) == 2, mine
    for t in mine:
        done = await app_client.post(
            f"/api/v1/tasks/{t['id']}/decision", headers=ho, json={"outcome": "complete"}
        )
        assert done.status_code == 200, done.text

    # both actions DONE → the review closes
    closed = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs)
    assert closed.status_code == 200, closed.text
    assert closed.json()["close_state"] == "Closed"
