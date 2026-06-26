"""S-drift-1 integration proofs — periodic re-review write paths.

Note: the GET serializer fields (review_period_months / next_review_due / review_state) land in
the read-surface task (Task 7 of this PR). These tests assert the final API shape; they will be
green once that task lands. Direct DB reads are used as a fallback where the GET body doesn't
yet carry the field, but the test assertions are written for the final API shape.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.duedate import resolve_calendar, snap_to_working_day
from easysynq_api.services.vault.review import REVIEW_PERIOD_DEFAULT_MONTHS, _org_tz, add_months

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _map_clause, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-pr-author-{salt}", b=f"kc-pr-approver-{salt}")


async def _release_doc(
    app_client: AsyncClient,
    ha: dict[str, str],
    hb: dict[str, str],
    type_id: str,
    content: bytes,
) -> tuple[str, dict]:
    """Create → checkout → upload unique bytes → checkin MAJOR → map clause →
    submit-review → approve → release. Returns (doc_id, GET body after release)."""
    doc = await _create(app_client, ha, type_id)
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, content)
    ci = await _checkin(
        app_client, ha, did, sha, change_reason="initial", change_significance="MAJOR"
    )
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text
    task_id = await s5.task_for_doc(did)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    # SoD-2: the editor may never release their own edit — the APPROVER releases (the
    # set_approver_release(org, True) flag each caller sets; the test_lifecycle pattern).
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text
    body = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()
    return did, body


async def test_create_defaults_review_period_to_24(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A freshly created document gets review_period_months=24 by default; next_review_due
    and review_state are None until first release."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")

    doc = await _create(app_client, ha, type_id)
    did = doc["id"]
    body = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()

    assert body.get("review_period_months") == REVIEW_PERIOD_DEFAULT_MONTHS  # 24
    assert body.get("next_review_due") is None
    assert body.get("review_state") is None


async def test_release_computes_next_review_due(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """After release, next_review_due == effective_from + 24 months; review_state == 'current'."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-release-{subj.a}".encode()

    _did, body = await _release_doc(app_client, ha, hb, type_id, content)

    assert body.get("current_state") == "Effective"
    # Derive expected next_review_due from the body's effective_from using the same add_months rule.
    eff_from_str = body.get("effective_from")
    assert eff_from_str is not None
    eff_from_dt = datetime.datetime.fromisoformat(eff_from_str)
    from easysynq_api.services.vault.review import _org_tz

    eff_from_date = eff_from_dt.astimezone(_org_tz()).date()
    expected_due = add_months(eff_from_date, REVIEW_PERIOD_DEFAULT_MONTHS)

    assert body.get("review_period_months") == REVIEW_PERIOD_DEFAULT_MONTHS
    assert body.get("next_review_due") == expected_due.isoformat()
    assert body.get("review_state") == "current"


async def test_patch_review_period_recomputes_and_null_clears(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """PATCH review_period_months recomputes next_review_due; explicit null clears both;
    value=0 is 422; an unrelated PATCH that omits the review field leaves it unchanged
    (model_fields_set guard — the 'unconditional assignment' trap)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-patch-{subj.a}".encode()

    did, body = await _release_doc(app_client, ha, hb, type_id, content)
    eff_from_str = body.get("effective_from")
    assert eff_from_str is not None
    eff_from_dt = datetime.datetime.fromisoformat(eff_from_str)
    from easysynq_api.services.vault.review import _org_tz

    eff_from_date = eff_from_dt.astimezone(_org_tz()).date()

    # PATCH review_period_months=12 → recomputes next_review_due to eff_from + 12 months.
    r1 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"review_period_months": 12}
    )
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    expected_12 = add_months(eff_from_date, 12)
    assert b1.get("review_period_months") == 12
    assert b1.get("next_review_due") == expected_12.isoformat()

    # PATCH review_period_months=None → next_review_due clears; review_state clears.
    r2 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"review_period_months": None}
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2.get("next_review_due") is None
    assert b2.get("review_state") is None

    # PATCH review_period_months=0 → 422 (ge=1 constraint).
    r3 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"review_period_months": 0}
    )
    assert r3.status_code == 422, r3.text

    # Re-set to 12.
    r4 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"review_period_months": 12}
    )
    assert r4.status_code == 200, r4.text

    # PATCH title only (review field OMITTED) → review_period_months STILL 12, next_review_due
    # unchanged. An unconditional assignment without model_fields_set would pass the other three
    # cases but fail this one (the S-web-7d trap).
    r5 = await app_client.patch(
        f"/api/v1/documents/{did}", headers=ha, json={"title": "Drift Test Updated"}
    )
    assert r5.status_code == 200, r5.text
    b5 = r5.json()
    assert b5.get("review_period_months") == 12
    assert b5.get("next_review_due") == expected_12.isoformat()


async def test_submit_review_autodefaults_null_period(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A legacy doc whose review_period_months is NULL gets T2 auto-defaulted at submit-review
    (never a 422); GET after submit → review_period_months == 24."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")

    doc = await _create(app_client, ha, type_id)
    did = doc["id"]

    # NULL the review_period_months column directly to simulate a legacy/pre-migration row.
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE documented_information SET review_period_months = NULL WHERE id = :id"),
            {"id": uuid.UUID(did)},
        )
        await s.commit()

    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, f"drift-auto-{subj.a}".encode())
    ci = await _checkin(app_client, ha, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)

    # submit-review must succeed (NEVER a 422) even with NULL review_period_months.
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text

    # GET → review_period_months == 24 (the T2 auto-default).
    body = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()
    assert body.get("review_period_months") == REVIEW_PERIOD_DEFAULT_MONTHS  # 24


# ---------------------------------------------------------------------------
# Task 5: Beat sweep — integration proofs
# ---------------------------------------------------------------------------


async def test_sweep_creates_one_task_idempotently(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """sweep_reviews opens exactly one PERIODIC_REVIEW task per Effective doc inside the lead
    window; a second sweep leaves exactly one non-terminal instance (idempotency proof)."""
    from easysynq_api.db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
    from easysynq_api.db.models.app_user import AppUser
    from easysynq_api.db.models.workflow import Task, WorkflowInstance
    from easysynq_api.services.vault.review import sweep_reviews

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-sweep-idem-{subj.a}".encode()

    did, _body = await _release_doc(app_client, ha, hb, type_id, content)
    doc_uuid = uuid.UUID(did)

    # SET next_review_due to today + 30 days exactly (horizon boundary: <=, not <).
    today_date = datetime.datetime.now(_org_tz()).date()
    horizon_date = today_date + datetime.timedelta(days=30)
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE documented_information SET next_review_due = :d WHERE id = :id"),
            {"d": horizon_date, "id": doc_uuid},
        )
        await s.commit()

    # First sweep — should create exactly one instance + task for this doc.
    async with get_sessionmaker()() as session:
        result = await sweep_reviews(session)
    assert result["tasks_created"] >= 1

    async with get_sessionmaker()() as s:
        # Exactly ONE non-terminal PERIODIC_REVIEW instance for this doc.
        instance = (
            await s.execute(
                select(WorkflowInstance)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                    WorkflowInstance.subject_id == doc_uuid,
                    WorkflowInstance.current_state.not_in(
                        ("COMPLETED", "REJECTED", "NEEDS_ATTENTION")
                    ),
                )
                .order_by(WorkflowInstance.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        assert instance is not None, "expected a non-terminal PERIODIC_REVIEW instance"

        # The task for this instance — the LOAD-BEARING assertion: a NEEDS_ATTENTION instance
        # materializes no task (fail-closed); a typo'd stage sentinel would also miss here.
        task = (
            await s.execute(
                select(Task)
                .where(
                    Task.instance_id == instance.id,
                    Task.state == TaskState.PENDING,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        assert task is not None, "expected a PENDING task for the PERIODIC_REVIEW instance"
        assert task.type == TaskType.PERIODIC_REVIEW

        # The task is assigned to the doc owner.
        owner_row = await s.get(AppUser, task.assignee_user_id)
        assert owner_row is not None
        # The owner is the creator (subj.a was the author who created the doc).
        assert owner_row.keycloak_subject == subj.a

        # due_at anchors on next_review_due, built at midnight in the working_calendar's tz and
        # snapped FORWARD to a working day (R55/D-5). horizon_date = today+30 can be a weekend, so
        # assert against the production-snapped value (NOT horizon_date itself — that was
        # weekday-flaky once the snap landed). The stored next_review_due is unchanged.
        assert task.due_at is not None
        cal = await resolve_calendar(s, instance.org_id)
        expected_due = snap_to_working_day(
            datetime.datetime.combine(horizon_date, datetime.time(0, 0), tzinfo=cal.tz), cal
        )
        assert task.due_at == expected_due

    # Second sweep — still exactly ONE non-terminal instance (idempotency).
    async with get_sessionmaker()() as session:
        await sweep_reviews(session)

    async with get_sessionmaker()() as s:
        count = await s.execute(
            select(WorkflowInstance).where(
                WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                WorkflowInstance.subject_id == doc_uuid,
                WorkflowInstance.current_state.not_in(("COMPLETED", "REJECTED", "NEEDS_ATTENTION")),
            )
        )
        rows = count.scalars().all()
    assert len(rows) == 1, (
        f"expected exactly 1 non-terminal instance after 2 sweeps, got {len(rows)}"
    )


async def test_sweep_snaps_weekend_review_due_to_working_day(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """R55/D-5: a PERIODIC_REVIEW whose next_review_due lands on a weekend mints a task whose due_at
    is SNAPPED forward to a working day (built in the working_calendar's tz), while the STORED
    next_review_due is UNCHANGED (D-3). Mutation-distinguishing via is_working_day on the resolved
    calendar (pre-slice the raw weekend instant is stored)."""
    from easysynq_api.db.models._workflow_enums import TaskState, WorkflowSubjectType
    from easysynq_api.db.models.documented_information import DocumentedInformation
    from easysynq_api.db.models.workflow import Task, WorkflowInstance
    from easysynq_api.services.notifications.timer import is_working_day
    from easysynq_api.services.vault.review import sweep_reviews

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    did, _ = await _release_doc(app_client, ha, hb, type_id, f"drift-snap-{subj.a}".encode())
    doc_uuid = uuid.UUID(did)

    # The NEXT Saturday from today (isoweekday 6): always a weekend within the 30-day lead horizon,
    # for any run date/weekday — deterministic + not weekday-flaky.
    today = datetime.datetime.now(_org_tz()).date()
    sat = today + datetime.timedelta(days=(6 - today.isoweekday()) % 7)
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE documented_information SET next_review_due = :d WHERE id = :id"),
            {"d": sat, "id": doc_uuid},
        )
        await s.commit()

    async with get_sessionmaker()() as session:
        assert (await sweep_reviews(session))["tasks_created"] >= 1

    async with get_sessionmaker()() as s:
        instance = (
            await s.execute(
                select(WorkflowInstance).where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                    WorkflowInstance.subject_id == doc_uuid,
                    WorkflowInstance.current_state.not_in(
                        ("COMPLETED", "REJECTED", "NEEDS_ATTENTION")
                    ),
                )
            )
        ).scalar_one()
        task = (
            await s.execute(
                select(Task).where(Task.instance_id == instance.id, Task.state == TaskState.PENDING)
            )
        ).scalar_one()
        assert task.due_at is not None
        cal = await resolve_calendar(s, instance.org_id)
        expected = snap_to_working_day(
            datetime.datetime.combine(sat, datetime.time(0, 0), tzinfo=cal.tz), cal
        )
        # The task due_at is snapped onto a working day (§9.5) — NOT the raw weekend instant.
        assert task.due_at == expected
        assert is_working_day(task.due_at.astimezone(cal.tz).date(), cal)
        # D-3: the STORED next_review_due is unchanged (only the task due_at is snapped).
        doc = await s.get(DocumentedInformation, doc_uuid)
        assert doc is not None and doc.next_review_due == sat


async def test_sweep_skips_non_effective_and_unscheduled(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """Sweep skips docs that are not Effective AND docs with no review schedule.
    Both legs are non-vacuous: the Draft doc has next_review_due inside the horizon,
    and the unscheduled doc is Effective but null-period."""
    from easysynq_api.db.models._workflow_enums import WorkflowSubjectType
    from easysynq_api.db.models.workflow import WorkflowInstance
    from easysynq_api.services.vault.review import sweep_reviews

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    today_date = datetime.datetime.now(_org_tz()).date()

    # --- Draft leg: a Draft doc with next_review_due = today (inside the horizon) ---
    draft_doc = await _create(app_client, ha, type_id)
    draft_id = uuid.UUID(draft_doc["id"])
    # Set next_review_due to today directly (it stays Draft — no checkin/release).
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE documented_information SET next_review_due = :d WHERE id = :id"),
            {"d": today_date, "id": draft_id},
        )
        await s.commit()

    # --- Unscheduled leg: release a doc, then PATCH review_period_months = null ---
    unsched_content = f"drift-unsched-{subj.a}".encode()
    unsched_did, _ = await _release_doc(app_client, ha, hb, type_id, unsched_content)
    unsched_uuid = uuid.UUID(unsched_did)

    r = await app_client.patch(
        f"/api/v1/documents/{unsched_did}", headers=ha, json={"review_period_months": None}
    )
    assert r.status_code == 200, r.text
    # Assert next_review_due is actually None in the DB before sweeping.
    async with get_sessionmaker()() as s:
        from easysynq_api.db.models.documented_information import DocumentedInformation

        di = await s.get(DocumentedInformation, unsched_uuid)
        assert di is not None
        assert di.next_review_due is None, "expected next_review_due to be cleared"

    # Sweep — neither doc should get a PERIODIC_REVIEW instance.
    async with get_sessionmaker()() as session:
        await sweep_reviews(session)

    async with get_sessionmaker()() as s:
        # Draft doc: no instance.
        draft_instance = (
            await s.execute(
                select(WorkflowInstance)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                    WorkflowInstance.subject_id == draft_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        assert draft_instance is None, "Draft doc must not get a PERIODIC_REVIEW instance"

        # Unscheduled (null-period) doc: no instance.
        unsched_instance = (
            await s.execute(
                select(WorkflowInstance)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                    WorkflowInstance.subject_id == unsched_uuid,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        assert unsched_instance is None, "Unscheduled doc must not get a PERIODIC_REVIEW instance"


async def test_sweep_escalates_overdue_once(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """sweep_reviews emits exactly one REVIEW_OVERDUE audit event per overdue cycle; a second
    sweep does NOT create a second REVIEW_OVERDUE for the same instance (dedup proof).

    Negative (load-bearing subject_type filter): a second doc left in InReview has a genuinely
    PENDING DOCUMENT-subject approval task whose due_at is backdated to the past. The sweep must
    NOT emit a REVIEW_OVERDUE for it — proving the WorkflowSubjectType.PERIODIC_REVIEW filter is
    what prevents escalation, not the Task.state filter (which would also exclude a DONE task).
    """
    from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
    from easysynq_api.db.models._workflow_enums import TaskState, WorkflowSubjectType
    from easysynq_api.db.models.audit_event import AuditEvent
    from easysynq_api.db.models.workflow import Task, WorkflowInstance
    from easysynq_api.services.vault.review import sweep_reviews

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-overdue-{subj.a}".encode()

    did, body = await _release_doc(app_client, ha, hb, type_id, content)
    doc_uuid = uuid.UUID(did)
    identifier = body.get("identifier")

    # Backdate next_review_due to 40 days ago so the doc is overdue AND inside the lead window.
    today_date = datetime.datetime.now(_org_tz()).date()
    overdue_date = today_date - datetime.timedelta(days=40)
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE documented_information SET next_review_due = :d WHERE id = :id"),
            {"d": overdue_date, "id": doc_uuid},
        )
        await s.commit()

    # Negative (load-bearing): create a SECOND doc and leave it in InReview (no approval).
    # Its DOCUMENT-subject approval task is genuinely PENDING — so the Task.state == PENDING
    # filter alone does NOT exclude it. Only the subject_type == PERIODIC_REVIEW filter stops
    # the escalation pass from producing a REVIEW_OVERDUE for this instance.
    neg_content = f"drift-overdue-neg-{subj.a}".encode()
    neg_doc = await _create(app_client, ha, type_id)
    neg_did = neg_doc["id"]
    neg_uuid = uuid.UUID(neg_did)
    await app_client.post(f"/api/v1/documents/{neg_did}/checkout", headers=ha)
    neg_sha = await _upload(app_client, ha, neg_did, neg_content)
    neg_ci = await _checkin(
        app_client, ha, neg_did, neg_sha, change_reason="neg", change_significance="MAJOR"
    )
    assert neg_ci.status_code == 201, neg_ci.text
    await _map_clause(app_client, ha, neg_did)
    neg_sr = await app_client.post(f"/api/v1/documents/{neg_did}/submit-review", headers=ha)
    assert neg_sr.status_code == 200, neg_sr.text
    # neg_did is now InReview — deliberately NOT approved, so its approval task stays PENDING.

    # Find the DOCUMENT-subject instance for the negative doc and backdate its pending task.
    async with get_sessionmaker()() as s:
        neg_doc_instance = (
            await s.execute(
                select(WorkflowInstance)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.DOCUMENT,
                    WorkflowInstance.subject_id == neg_uuid,
                )
                .order_by(WorkflowInstance.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        assert neg_doc_instance is not None, "expected a DOCUMENT workflow instance for neg doc"
        # Confirm the task is genuinely PENDING (not DONE/SKIPPED), then backdate it.
        neg_task = (
            await s.execute(
                select(Task)
                .where(
                    Task.instance_id == neg_doc_instance.id,
                    Task.state == TaskState.PENDING,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        assert neg_task is not None, "expected a PENDING approval task for the InReview neg doc"
        neg_task_id = neg_task.id
        # Backdate due_at so the escalation pass would fire if the filter were absent.
        await s.execute(
            text("UPDATE task SET due_at = now() - interval '50 days' WHERE id = :tid"),
            {"tid": neg_task_id},
        )
        await s.commit()

    # First sweep — creates the PERIODIC_REVIEW instance + task for did; the task's due_at is
    # already in the past (overdue_date is 40 days ago) so the escalation pass also fires in
    # this same sweep.
    async with get_sessionmaker()() as session:
        result = await sweep_reviews(session)
    assert result["tasks_created"] >= 1

    # Find the PERIODIC_REVIEW instance just created for this run's doc.
    async with get_sessionmaker()() as s:
        pr_instance = (
            await s.execute(
                select(WorkflowInstance)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                    WorkflowInstance.subject_id == doc_uuid,
                )
                .order_by(WorkflowInstance.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        assert pr_instance is not None
        pr_instance_id = pr_instance.id

        # Backdate the task's due_at so the escalation pass fires again on the next sweep
        # (makes the dedup proof non-trivial — the sweep actively tries to escalate again).
        await s.execute(
            text(
                "UPDATE task SET due_at = now() - interval '1 hour'"
                " WHERE instance_id = :iid AND state = 'PENDING'"
            ),
            {"iid": pr_instance_id},
        )
        await s.commit()

    # Run-scoped assert after FIRST sweep: exactly one REVIEW_OVERDUE for this instance.
    async with get_sessionmaker()() as s:
        events_after_sweep1 = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_type == AuditObjectType.document,
                        AuditEvent.object_id == doc_uuid,
                        AuditEvent.event_type == EventType.REVIEW_OVERDUE,
                        AuditEvent.after["instance_id"].astext == str(pr_instance_id),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events_after_sweep1) == 1, (
        f"expected exactly 1 REVIEW_OVERDUE after first sweep, got {len(events_after_sweep1)}"
    )
    # scope_ref must carry the doc's identifier (run-scoped — only this instance qualifies).
    assert events_after_sweep1[0].scope_ref == identifier, (
        f"expected scope_ref={identifier!r}, got {events_after_sweep1[0].scope_ref!r}"
    )

    # Second sweep — dedup proof: the escalation pass sees the overdue task again but the
    # guard (existing REVIEW_OVERDUE for this instance) prevents a duplicate event.
    async with get_sessionmaker()() as session:
        await sweep_reviews(session)

    # Run-scoped assert after SECOND sweep: still exactly one REVIEW_OVERDUE for this instance.
    async with get_sessionmaker()() as s:
        events_after_sweep2 = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_type == AuditObjectType.document,
                        AuditEvent.object_id == doc_uuid,
                        AuditEvent.event_type == EventType.REVIEW_OVERDUE,
                        AuditEvent.after["instance_id"].astext == str(pr_instance_id),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events_after_sweep2) == 1, (
        f"expected exactly 1 REVIEW_OVERDUE after dedup sweep, got {len(events_after_sweep2)}"
    )

    # Negative: no REVIEW_OVERDUE must exist for the InReview doc's DOCUMENT-subject instance.
    # Its approval task was genuinely PENDING and backdated — only the subject_type filter stops it.
    async with get_sessionmaker()() as s:
        neg_overdue = (
            await s.execute(
                select(AuditEvent.id)
                .where(
                    AuditEvent.object_id == neg_uuid,
                    AuditEvent.event_type == EventType.REVIEW_OVERDUE,
                )
                .limit(1)
            )
        ).first()
    assert neg_overdue is None, (
        "a PENDING DOCUMENT-subject task must NOT produce a REVIEW_OVERDUE event"
        " — the subject_type == PERIODIC_REVIEW filter is the load-bearing guard"
    )


# ---------------------------------------------------------------------------
# Task 6: PERIODIC_REVIEW decision handler — integration proofs
# ---------------------------------------------------------------------------


async def _due_released_doc(
    app_client: AsyncClient,
    ha: dict[str, str],
    hb: dict[str, str],
    type_id: str,
    content: bytes,
    *,
    backdate_days: int = 400,
) -> tuple[str, uuid.UUID]:
    """Release a doc then backdate its effective_from by backdate_days and set next_review_due
    to today (making it overdue) so sweep_reviews creates a PERIODIC_REVIEW task.
    Returns (doc_id_str, doc_uuid)."""
    from easysynq_api.db.models.document_version import DocumentVersion
    from easysynq_api.db.models.documented_information import DocumentedInformation

    did, _body = await _release_doc(app_client, ha, hb, type_id, content)
    doc_uuid = uuid.UUID(did)
    today_date = datetime.datetime.now(_org_tz()).date()
    async with get_sessionmaker()() as s:
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        ver = await s.get(DocumentVersion, di.current_effective_version_id)
        assert ver is not None
        # Backdate effective_from so anchor is NOT confused with last_reviewed_at (test proof).
        new_eff_from = ver.effective_from - datetime.timedelta(days=backdate_days)
        ver.effective_from = new_eff_from
        di.next_review_due = today_date
        await s.commit()
    return did, doc_uuid


async def _pr_task_for_doc(doc_uuid: uuid.UUID) -> uuid.UUID:
    """Find the latest open PERIODIC_REVIEW task for this doc."""
    from easysynq_api.db.models._workflow_enums import TaskState, WorkflowSubjectType
    from easysynq_api.db.models.workflow import Task, WorkflowInstance

    async with get_sessionmaker()() as s:
        instance = (
            await s.execute(
                select(WorkflowInstance)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                    WorkflowInstance.subject_id == doc_uuid,
                    WorkflowInstance.current_state.not_in(
                        ("COMPLETED", "REJECTED", "NEEDS_ATTENTION")
                    ),
                )
                .order_by(WorkflowInstance.started_at.desc())
                .limit(1)
            )
        ).scalar_one()
        task = (
            await s.execute(
                select(Task)
                .where(
                    Task.instance_id == instance.id,
                    Task.state == TaskState.PENDING,
                )
                .limit(1)
            )
        ).scalar_one()
        return task.id


async def test_decide_complete_confirms_review(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """complete → COMPLETED, review_confirmed sig on the Effective version, clock reset from
    review date (NOT from backdated effective_from — the anchor proof)."""
    from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
    from easysynq_api.db.models._signature_enums import SignatureMeaning, SignedObjectType
    from easysynq_api.db.models._workflow_enums import WorkflowSubjectType
    from easysynq_api.db.models.audit_event import AuditEvent
    from easysynq_api.db.models.document_version import DocumentVersion
    from easysynq_api.db.models.documented_information import DocumentedInformation
    from easysynq_api.db.models.signature_event import SignatureEvent as SignatureEventRow
    from easysynq_api.db.models.workflow import WorkflowInstance
    from easysynq_api.services.vault.review import sweep_reviews

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-decide-complete-{subj.a}".encode()

    _did, doc_uuid = await _due_released_doc(app_client, ha, hb, type_id, content)

    # Capture backdated effective_from before sweep for the anchor proof.
    async with get_sessionmaker()() as s:
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        ver_id = di.current_effective_version_id
        assert ver_id is not None
        ver = await s.get(DocumentVersion, ver_id)
        assert ver is not None
        backdated_eff_from = ver.effective_from

    # Sweep → creates a PERIODIC_REVIEW task.
    async with get_sessionmaker()() as session:
        result = await sweep_reviews(session)
    assert result["tasks_created"] >= 1

    task_id = await _pr_task_for_doc(doc_uuid)

    # Owner (subj.a) decides complete.
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=ha,
        json={"outcome": "complete"},
    )
    assert dr.status_code == 200, dr.text
    body = dr.json()
    assert body["current_state"] == "COMPLETED"
    next_due_str = body.get("next_review_due")
    assert next_due_str is not None

    # Run-scoped assertions.
    async with get_sessionmaker()() as s:
        # Exactly one review_confirmed sig for this version, run-scoped.
        sigs = (
            (
                await s.execute(
                    select(SignatureEventRow).where(
                        SignatureEventRow.signed_object_id == ver_id,
                        SignatureEventRow.signed_object_type == SignedObjectType.document_version,
                        SignatureEventRow.meaning == SignatureMeaning.review_confirmed,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(sigs) == 1, f"expected 1 review_confirmed sig, got {len(sigs)}"
        sig = sigs[0]
        assert sig.content_digest == ver.source_blob_sha256

        # One REVIEW_CONFIRMED audit event for this doc.
        audit_rows = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_type == AuditObjectType.document,
                        AuditEvent.object_id == doc_uuid,
                        AuditEvent.event_type == EventType.REVIEW_CONFIRMED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 1, f"expected 1 REVIEW_CONFIRMED audit, got {len(audit_rows)}"
        assert audit_rows[0].scope_ref is not None

        # doc.last_reviewed_at is set; next_review_due is reset from review date (NOT eff_from).
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        assert di.last_reviewed_at is not None
        assert di.next_review_due is not None

        # The anchor proof: next_review_due == add_months(last_reviewed_at date, 24)
        # and must differ from add_months(backdated effective_from date, 24).
        last_reviewed_date = di.last_reviewed_at.astimezone(_org_tz()).date()
        from_review_date = add_months(last_reviewed_date, 24)
        backdated_eff_date = backdated_eff_from.astimezone(_org_tz()).date()
        from_eff_date = add_months(backdated_eff_date, 24)
        assert di.next_review_due == from_review_date, (
            f"next_review_due {di.next_review_due} != "
            f"add_months(last_reviewed_at, 24) {from_review_date}"
        )
        assert di.next_review_due != from_eff_date, (
            "anchor proof failed: next_review_due equals the backdated effective_from anchor "
            "— the handler is not using last_reviewed_at as the anchor"
        )

    # Re-run sweep → no new non-terminal PERIODIC_REVIEW instance (clock reset, not due yet).
    async with get_sessionmaker()() as session:
        await sweep_reviews(session)

    async with get_sessionmaker()() as s:
        non_terminal_count = len(
            (
                await s.execute(
                    select(WorkflowInstance).where(
                        WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                        WorkflowInstance.subject_id == doc_uuid,
                        WorkflowInstance.current_state.not_in(
                            ("COMPLETED", "REJECTED", "NEEDS_ATTENTION")
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert non_terminal_count == 0, (
        "after a complete decision, sweep must not create a new PERIODIC_REVIEW instance "
        f"(clock was reset, doc is not yet due) — got {non_terminal_count}"
    )


async def test_decide_changes_requested_keeps_clock_and_renag(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """changes_requested → REJECTED, clock unchanged, sweep re-nags with a new instance."""
    from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
    from easysynq_api.db.models._signature_enums import SignatureMeaning
    from easysynq_api.db.models.audit_event import AuditEvent
    from easysynq_api.db.models.documented_information import DocumentedInformation
    from easysynq_api.db.models.signature_event import SignatureEvent as SignatureEventRow
    from easysynq_api.db.models.workflow import Task, WorkflowInstance
    from easysynq_api.services.vault.review import sweep_reviews

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-decide-cr-{subj.a}".encode()

    # Backdate 40 days past due.
    _did, doc_uuid = await _due_released_doc(app_client, ha, hb, type_id, content, backdate_days=40)

    # Capture the original next_review_due so we can assert it's unchanged.
    async with get_sessionmaker()() as s:
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        original_next_review_due = di.next_review_due
        ver_id = di.current_effective_version_id

    # Sweep → task.
    async with get_sessionmaker()() as session:
        await sweep_reviews(session)

    task_id = await _pr_task_for_doc(doc_uuid)
    first_task_id = task_id

    # Owner decides changes_requested.
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=ha,
        json={"outcome": "changes_requested"},
    )
    assert dr.status_code == 200, dr.text
    assert dr.json()["current_state"] == "REJECTED"

    # Clock must be unchanged.
    async with get_sessionmaker()() as s:
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        assert di.next_review_due == original_next_review_due, (
            f"changes_requested must not reset next_review_due: "
            f"got {di.next_review_due}, want {original_next_review_due}"
        )
        # Zero review_confirmed signatures for this doc's versions.
        sig_count = len(
            (
                await s.execute(
                    select(SignatureEventRow).where(
                        SignatureEventRow.signed_object_id == ver_id,
                        SignatureEventRow.meaning == SignatureMeaning.review_confirmed,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sig_count == 0, (
            f"changes_requested must produce no review_confirmed sig, got {sig_count}"
        )

    # Sweep again → a NEW non-terminal PERIODIC_REVIEW instance (re-nag).
    async with get_sessionmaker()() as session:
        r2 = await sweep_reviews(session)
    assert r2["tasks_created"] >= 1

    async with get_sessionmaker()() as s:
        from easysynq_api.db.models._workflow_enums import TaskState, WorkflowSubjectType

        instances = (
            (
                await s.execute(
                    select(WorkflowInstance)
                    .where(
                        WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                        WorkflowInstance.subject_id == doc_uuid,
                        WorkflowInstance.current_state.not_in(
                            ("COMPLETED", "REJECTED", "NEEDS_ATTENTION")
                        ),
                    )
                    .order_by(WorkflowInstance.started_at.desc())
                )
            )
            .scalars()
            .all()
        )
        assert len(instances) == 1, (
            f"expected exactly 1 new non-terminal instance after re-nag sweep, got {len(instances)}"
        )
        new_instance = instances[0]

        # The new instance must be different from the one that was decided.
        new_task = (
            await s.execute(
                select(Task)
                .where(
                    Task.instance_id == new_instance.id,
                    Task.state == TaskState.PENDING,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        assert new_task is not None
        assert new_task.id != first_task_id, "re-nag must produce a NEW task, not the old one"

        # REVIEW_OVERDUE for the new instance (once-per-CYCLE, not once-EVER):
        # the sweep emitted REVIEW_OVERDUE for the old instance id; the new one gets its own.
        second_overdue = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_type == AuditObjectType.document,
                        AuditEvent.object_id == doc_uuid,
                        AuditEvent.event_type == EventType.REVIEW_OVERDUE,
                        AuditEvent.after["instance_id"].astext == str(new_instance.id),
                    )
                )
            )
            .scalars()
            .all()
        )
        # The new task's due_at is in the past (overdue_date), so the escalation pass fires.
        assert len(second_overdue) == 1, (
            f"expected 1 REVIEW_OVERDUE for the NEW instance, got {len(second_overdue)}"
        )


async def test_decide_obsoleted_doc_409_keeps_task_pending(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """complete on an obsoleted doc → 409; task stays PENDING; then changes_requested → 200."""
    from easysynq_api.db.models._signature_enums import SignatureMeaning
    from easysynq_api.db.models._workflow_enums import TaskState, WorkflowSubjectType
    from easysynq_api.db.models.documented_information import DocumentedInformation
    from easysynq_api.db.models.signature_event import SignatureEvent as SignatureEventRow
    from easysynq_api.db.models.workflow import Task, WorkflowInstance
    from easysynq_api.services.vault.review import sweep_reviews

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-decide-409-{subj.a}".encode()

    did, doc_uuid = await _due_released_doc(app_client, ha, hb, type_id, content)

    # Capture the Effective version id BEFORE obsoleting so the sig assertion
    # targets the right object (the handler signs the VERSION, not the doc).
    async with get_sessionmaker()() as s:
        di_pre = await s.get(DocumentedInformation, doc_uuid)
        assert di_pre is not None
        ver_id = di_pre.current_effective_version_id
        assert ver_id is not None

    # Sweep → task.
    async with get_sessionmaker()() as session:
        await sweep_reviews(session)

    task_id = await _pr_task_for_doc(doc_uuid)

    # Obsolete the doc (subj.a holds document.obsolete via lifecycle perms).
    obs_r = await app_client.post(
        f"/api/v1/documents/{did}/obsolete",
        headers=ha,
        json={"reason": "test obsolete for 409 gate"},
    )
    assert obs_r.status_code == 200, obs_r.text

    # Owner decides complete → 409 (no Effective version).
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=ha,
        json={"outcome": "complete"},
    )
    assert dr.status_code == 409, dr.text

    # Task must still be PENDING; instance non-terminal; zero review_confirmed sigs.
    async with get_sessionmaker()() as s:
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        # current_effective_version_id should be None after obsolete.
        assert di.current_effective_version_id is None

        instances = (
            (
                await s.execute(
                    select(WorkflowInstance).where(
                        WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                        WorkflowInstance.subject_id == doc_uuid,
                        WorkflowInstance.current_state.not_in(
                            ("COMPLETED", "REJECTED", "NEEDS_ATTENTION")
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(instances) == 1, (
            f"409 must leave instance non-terminal, got {len(instances)} non-terminal"
        )
        task_row = (
            await s.execute(
                select(Task).where(
                    Task.id == task_id,
                )
            )
        ).scalar_one()
        assert task_row.state == TaskState.PENDING, (
            f"409 must leave task PENDING, got {task_row.state}"
        )
        sig_count = len(
            (
                await s.execute(
                    select(SignatureEventRow).where(
                        SignatureEventRow.signed_object_id == ver_id,
                        SignatureEventRow.meaning == SignatureMeaning.review_confirmed,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert sig_count == 0, f"409 path must emit no review_confirmed sig, got {sig_count}"

    # Task was NOT poisoned — changes_requested still works.
    dr2 = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=ha,
        json={"outcome": "changes_requested"},
    )
    assert dr2.status_code == 200, dr2.text
    assert dr2.json()["current_state"] == "REJECTED"


async def test_decide_rejects_non_owner_and_bad_outcomes(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """Non-member 404; live-authority re-check 404; bad outcome 422; idempotent replay."""
    from easysynq_api.db.models._signature_enums import SignatureMeaning
    from easysynq_api.db.models.app_user import AppUser
    from easysynq_api.db.models.documented_information import DocumentedInformation
    from easysynq_api.db.models.signature_event import SignatureEvent as SignatureEventRow
    from easysynq_api.services.vault.review import sweep_reviews

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-decide-authz-{subj.a}".encode()

    did, doc_uuid = await _due_released_doc(app_client, ha, hb, type_id, content)

    # Ensure subj.b's app_user row exists.
    async with get_sessionmaker()() as s:
        b_user = (
            await s.execute(select(AppUser).where(AppUser.keycloak_subject == subj.b))
        ).scalar_one_or_none()
        assert b_user is not None, "subj.b user must exist after grant_lifecycle"
        b_user_id = b_user.id

    # Sweep → task.
    async with get_sessionmaker()() as session:
        await sweep_reviews(session)

    task_id = await _pr_task_for_doc(doc_uuid)

    # subj.b (not the owner, not in candidate pool) → 404 (the 404-COLLAPSE).
    r_non_member = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=hb,
        json={"outcome": "complete"},
    )
    assert r_non_member.status_code == 404, r_non_member.text

    # Reassign owner_user_id to subj.b — subj.a is still in the FROZEN candidate pool.
    async with get_sessionmaker()() as s:
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        di.owner_user_id = b_user_id
        await s.commit()

    # subj.a (in frozen pool but no longer the live owner) → 404 (the live-authority re-check).
    r_live_check = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=ha,
        json={"outcome": "complete"},
    )
    assert r_live_check.status_code == 404, r_live_check.text

    # Restore owner to subj.a.
    async with get_sessionmaker()() as s:
        a_user = (
            await s.execute(select(AppUser).where(AppUser.keycloak_subject == subj.a))
        ).scalar_one()
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        di.owner_user_id = a_user.id
        await s.commit()

    # Bad outcome → 422 (whitelist: complete | changes_requested only).
    r_bad = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=ha,
        json={"outcome": "approve"},
    )
    assert r_bad.status_code == 422, r_bad.text

    # Successful complete with an Idempotency-Key.
    idem_key = uuid.uuid4().hex
    r1 = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers={**ha, "Idempotency-Key": idem_key},
        json={"outcome": "complete"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["current_state"] == "COMPLETED"

    # Retry the same Idempotency-Key → 200 replay, still exactly 1 review_confirmed sig.
    r2 = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers={**ha, "Idempotency-Key": idem_key},
        json={"outcome": "complete"},
    )
    assert r2.status_code == 200, r2.text

    # Replay-parity: the second response must carry the same document_id, next_review_due,
    # and signature_event_id as the first.  The replay-enrichment branch is the point.
    b1 = r1.json()
    b2 = r2.json()
    assert b2.get("document_id") == b1.get("document_id") == did, (
        f"replayed body document_id {b2.get('document_id')!r} != original {b1.get('document_id')!r}"
    )
    assert b2.get("next_review_due") == b1.get("next_review_due"), (
        f"replayed next_review_due {b2.get('next_review_due')!r} "
        f"!= original {b1.get('next_review_due')!r}"
    )
    assert b1.get("signature_event_id") is not None, (
        "first response must carry a signature_event_id"
    )
    assert b2.get("signature_event_id") == b1.get("signature_event_id"), (
        f"replayed signature_event_id {b2.get('signature_event_id')!r} "
        f"!= original {b1.get('signature_event_id')!r}"
    )

    async with get_sessionmaker()() as s:
        di = await s.get(DocumentedInformation, doc_uuid)
        assert di is not None
        ver_id = di.current_effective_version_id
        sig_count = len(
            (
                await s.execute(
                    select(SignatureEventRow).where(
                        SignatureEventRow.signed_object_id == ver_id,
                        SignatureEventRow.meaning == SignatureMeaning.review_confirmed,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sig_count == 1, (
        f"idempotent replay must not add a second review_confirmed sig, got {sig_count}"
    )


# ---------------------------------------------------------------------------
# populate_existing interleave proof (fix for the authz-dependency identity-map bug)
# ---------------------------------------------------------------------------


async def test_locked_load_sees_concurrent_commit(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """_load_document(for_update=True) must return the DB-current row, not the stale
    pre-lock snapshot the authz dependency cached in the session's identity map.

    Without populate_existing=True the authz dependency's session.get() primes the
    identity-map entry, and the subsequent locked SELECT acquires the row lock but
    hands back the cached (stale) attributes — a concurrent commit that landed while
    we waited for the lock stays invisible.

    With populate_existing=True the locked SELECT overwrites the identity-map entry
    with the locked row's current column values, so the handler sees the committed
    change (this is the diff-critic MAJOR fix).
    """
    from easysynq_api.api.documents import _load_document
    from easysynq_api.db.models.app_user import AppUser
    from easysynq_api.db.models.documented_information import DocumentedInformation

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-interleave-{subj.a}".encode()

    # Release a doc so it has a valid DocumentedInformation row.
    did, _body = await _release_doc(app_client, ha, hb, type_id, content)
    doc_uuid = uuid.UUID(did)

    # Open session S — this simulates the per-request session.
    async with get_sessionmaker()() as session_s:
        # Prime the identity map — exactly what the authz dependency does.
        cached = await session_s.get(DocumentedInformation, doc_uuid)
        assert cached is not None
        original_period = cached.review_period_months

        # In a SEPARATE session B: commit a change to review_period_months (sentinel = 7).
        sentinel_period = 7
        async with get_sessionmaker()() as session_b:
            di_b = await session_b.get(DocumentedInformation, doc_uuid)
            assert di_b is not None
            di_b.review_period_months = sentinel_period
            await session_b.commit()

        # Resolve the caller AppUser for session_s (needed by _load_document's org_id check).
        caller = (
            await session_s.execute(select(AppUser).where(AppUser.keycloak_subject == subj.a))
        ).scalar_one()

        # Call _load_document with for_update=True — this is the path under test.
        # Before the fix, it returned original_period (stale identity-map snapshot).
        # After the fix (populate_existing=True), it must return sentinel_period.
        doc = await _load_document(session_s, caller, doc_uuid, for_update=True)

    assert doc.review_period_months == sentinel_period, (
        f"_load_document(for_update=True) returned stale period "
        f"{doc.review_period_months!r} (from identity-map cache) "
        f"instead of the committed value {sentinel_period!r}. "
        f"Original was {original_period!r}. "
        "populate_existing=True is required on the locked SELECT."
    )


# ---------------------------------------------------------------------------
# Task 7: read surface — review fields on the document serializer
# ---------------------------------------------------------------------------


async def test_document_serializer_carries_review_fields(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """GET /documents/{id} carries review_period_months, next_review_due, last_reviewed_at,
    review_state; GET /documents (list) carries the same four fields on every row.

    Sequence:
      1. Release a doc → detail GET → review_period_months==24, next_review_due is a date ISO,
         last_reviewed_at is None, review_state=="current".
      2. Backdate next_review_due to today-1d directly → detail GET →
         review_state=="overdue".
      3. List GET → find THIS doc's row by id → same four fields, same values.
    """
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"drift-serializer-{subj.a}".encode()

    did, body = await _release_doc(app_client, ha, hb, type_id, content)

    # Step 1: after release — four fields present, review_state is "current".
    assert body.get("review_period_months") == REVIEW_PERIOD_DEFAULT_MONTHS
    nrd = body.get("next_review_due")
    assert nrd is not None, "next_review_due must be set after release"
    # Validate it's a parseable date ISO string.
    datetime.date.fromisoformat(nrd)
    assert body.get("last_reviewed_at") is None
    assert body.get("review_state") == "current"

    # Step 2: backdate next_review_due to yesterday → overdue.
    today_date = datetime.datetime.now(_org_tz()).date()
    yesterday = today_date - datetime.timedelta(days=1)
    async with get_sessionmaker()() as s:
        await s.execute(
            text("UPDATE documented_information SET next_review_due = :d WHERE id = :id"),
            {"d": yesterday, "id": uuid.UUID(did)},
        )
        await s.commit()

    body2 = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()
    assert body2.get("next_review_due") == yesterday.isoformat()
    assert body2.get("review_state") == "overdue"

    # Step 3: list endpoint — find this doc's row by id; same four fields present.
    list_r = await app_client.get("/api/v1/documents", headers=ha)
    assert list_r.status_code == 200, list_r.text
    list_body = list_r.json()
    # The list response uses {"data": [...], "page": {...}}.
    doc_rows = list_body.get("data", [])
    matching = [r for r in doc_rows if r.get("id") == did]
    assert len(matching) == 1, (
        f"expected 1 row for doc {did} in the list response, got {len(matching)}"
    )
    row = matching[0]
    assert row.get("review_period_months") == REVIEW_PERIOD_DEFAULT_MONTHS
    assert row.get("next_review_due") == yesterday.isoformat()
    assert row.get("review_state") == "overdue"
    assert row.get("last_reviewed_at") is None  # never confirmed — all four fields on the list row
