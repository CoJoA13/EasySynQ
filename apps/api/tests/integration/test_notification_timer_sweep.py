"""S-notify-4: timer_sweep Beat — reminder / overdue / escalate-to-manager orchestrator.

Integration tests against a real migrated PG16 via testcontainers.

Design note: ``sla_policy`` is SELECT-only for the app role (migration 0065 REVOKE). Tests rely
on the seeded policy for the default org: remind_1_before=3d, remind_2_before=None (one-reminder
MVP), escalate_1_after=1d (except DOC_ACK/PERIODIC_REVIEW which have escalate_1_after=None).
Pre-stamp columns we don't want to fire in a given test.

Scenarios:
1. remind_1 fires once for the assignee when now >= due_at - remind_1_before; re-sweep no-op.
2. overdue fires (CRITICAL class via class_of) at now >= due_at.
3. escalate fires: manager gets task.escalated + TASK_ESCALATED audit_event + escalated_1_at stamp.
4. No manager + QMS Owner role-holder → holder gets task.escalated.
5. A DONE task past every threshold → no notifications.
6. Concurrency: two process_task_timers on the same task via asyncio.gather → escalated once.
7. DOC_ACK task past escalate threshold → reminders+overdue only; no task.escalated + no audit.
8. Inactive manager → falls through to QMS Owner fallback.
9. (R2-3) Active no-email manager → in-app task.escalated created; stamp written.
10. (R2-2) Escalate with no valid recipient (no manager + no QM) → stamped; re-sweep no-op.
11. (R2-4) Cross-org QM fallback holder → NOT notified; step stamped (no valid recipient).
12. (R3-1) Self-manager (manager_id == assignee.id) → escalation goes to QMS Owner fallback.
13. (R3-2) Dedup hit (notification already exists, stamp NULL) → sweep stamps; no double-send.
14. (R3-2 regression guard) Genuine template miss (attempted>0) → step NOT stamped; retry.
15. (R4-1) Dedup sweep with existing TASK_ESCALATED audit → stamps but writes NO second audit row.
16. (R4-1) Genuinely-new escalation → exactly one audit row with escalated_to populated.
17. (R4-2) Self-manager who is sole QMS Owner → escalation audit via == "qm_fallback".
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
import zoneinfo
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.notification import Notification
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.workflow import Task, WorkflowDefinition, WorkflowInstance
from easysynq_api.db.models.working_calendar import WorkingCalendar
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.common.org_clock import resolve_org_tz
from easysynq_api.services.notifications.classes import NotificationClass, class_of
from easysynq_api.services.notifications.constants import (
    EVENT_TASK_DUE_FINAL,
    EVENT_TASK_DUE_SOON,
    EVENT_TASK_ESCALATED,
    EVENT_TASK_OVERDUE,
)
from easysynq_api.services.notifications.escalation import (
    _due_task_ids,
    emit_task_event,
    process_task_timers,
    resolve_working_calendar,
    sweep_task_timers,
)
from easysynq_api.services.notifications.recipients import Recipient

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixed reference time. MUST be:
#   - a WEDNESDAY (S-notify-6: the timer now applies the org's Mon-Fri business calendar, so a
#     real-now anchor would be weekday-flaky; due_at = _BASE - 2d = Monday → business escalate
#     threshold = Tuesday ≤ _BASE, deterministic regardless of when CI runs); and
#   - inside an existing audit_event monthly partition. Migration 0010 creates ONLY 2026-06/07/08
#     at install (the roll_partitions Beat doesn't run in tests), and escalation writes a
#     TASK_ESCALATED audit at occurred_at = now → that month's partition must exist.
# 2026-06-24 is a Wednesday in the 2026-06 partition. 10:00 UTC avoids any quiet-window overlap.
# Seeded SLA policy (migration 0065): remind_1_before=3d, remind_2_before=None, escalate_1_after=1d.
# ---------------------------------------------------------------------------
_BASE = datetime.datetime(2026, 6, 24, 10, 0, 0, tzinfo=datetime.UTC)  # Wednesday, 10:00 UTC

# A sentinel "already sent" stamp: used to pre-stamp steps we don't want a test to fire (only its
# non-null-ness matters — due_steps gates on `stamp is None`, never the value).
_STAMPED = datetime.datetime(2026, 6, 1, 0, 0, 0, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _seed_user(
    org_id: uuid.UUID,
    *,
    display_name: str | None = None,
    email: str | None = "timer-test@example.com",
    manager_id: uuid.UUID | None = None,
) -> uuid.UUID:
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-timer-{salt}",
            display_name=display_name or f"Timer Test {salt}",
            email=email,
            status=UserStatus.ACTIVE,
            manager_id=manager_id,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _assign_role(org_id: uuid.UUID, user_id: uuid.UUID, role_name: str) -> None:
    async with get_sessionmaker()() as s:
        role = (
            await s.execute(select(Role).where(Role.org_id == org_id, Role.name == role_name))
        ).scalar_one_or_none()
        if role is None:
            role = Role(org_id=org_id, name=role_name, is_reserved=False)
            s.add(role)
            await s.flush()
        s.add(RoleAssignment(org_id=org_id, role_id=role.id, user_id=user_id, bound_scope=None))
        await s.commit()


async def _seed_workflow_objects(
    org_id: uuid.UUID,
    assignee_user_id: uuid.UUID,
    *,
    due_at: datetime.datetime,
    task_state: TaskState = TaskState.PENDING,
    task_type: TaskType = TaskType.APPROVE,
    # Pre-stamp these steps so the sweep skips them (SELECT-only sla_policy workaround).
    remind_1_sent_at: datetime.datetime | None = None,
    remind_2_sent_at: datetime.datetime | None = None,
    overdue_notified_at: datetime.datetime | None = None,
    escalated_1_at: datetime.datetime | None = None,
) -> tuple[WorkflowInstance, Task]:
    key = f"timer_test_{uuid.uuid4().hex[:8]}"
    async with get_sessionmaker()() as s:
        defn = WorkflowDefinition(
            org_id=org_id,
            key=key,
            version=1,
            effective=True,
            subject_type=WorkflowSubjectType.DOCUMENT,
            stages={"entry": "approve"},
        )
        s.add(defn)
        await s.flush()

        instance = WorkflowInstance(
            org_id=org_id,
            definition_id=defn.id,
            definition_version=1,
            subject_type=WorkflowSubjectType.DOCUMENT,
            subject_id=uuid.uuid4(),  # phantom — resolve_subject degrades gracefully
            current_state="IN_APPROVAL",
        )
        s.add(instance)
        await s.flush()

        task = Task(
            org_id=org_id,
            instance_id=instance.id,
            stage_key="approve",
            assignee_user_id=assignee_user_id,
            type=task_type,
            state=task_state,
            due_at=due_at,
            remind_1_sent_at=remind_1_sent_at,
            remind_2_sent_at=remind_2_sent_at,
            overdue_notified_at=overdue_notified_at,
            escalated_1_at=escalated_1_at,
        )
        s.add(task)
        await s.commit()

        # Reload in a fresh session to avoid identity-map issues
        async with get_sessionmaker()() as s2:
            inst2 = await s2.get(WorkflowInstance, instance.id)
            task2 = await s2.get(Task, task.id)
            return inst2, task2  # type: ignore[return-value]


async def _count_notifications(
    recipient_user_id: uuid.UUID, task_id: uuid.UUID, event_key: str
) -> int:
    async with get_sessionmaker()() as s:
        rows = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == recipient_user_id,
                        Notification.task_id == task_id,
                        Notification.event_key == event_key,
                    )
                )
            )
            .scalars()
            .all()
        )
        return len(rows)


async def _count_audit_events(org_id: uuid.UUID, event_type: EventType, scope_ref: str) -> int:
    async with get_sessionmaker()() as s:
        rows = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.event_type == event_type,
                        AuditEvent.scope_ref == scope_ref,
                    )
                )
            )
            .scalars()
            .all()
        )
        return len(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_remind_fires_once(app_under_test: Any) -> None:
    """remind_1 fires when now >= due_at - remind_1_before=3d; re-sweep is a no-op.

    Seeded policy (one-reminder MVP): remind_1_before=3d, remind_2_before=None, escalate_1_after=1d.
    Strategy: due_at = _BASE + 2d → remind_1 threshold = due_at - 3d = _BASE - 1d < _BASE → FIRES.
    remind_2 (remind_2_before=1d) does NOT fire here: its threshold (due - 1bd, with
    due = _BASE + 2d) is in the future at _BASE → no second reminder, no pre-stamp needed.
    overdue/escalate thresholds are in future → don't fire.
    Pre-stamp overdue/escalated to isolate remind_1.
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Remind Assignee")

    # due_at = _BASE + 2d: remind_1 fires (threshold _BASE - 1d); overdue + escalate in future.
    due_at = _BASE + datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        # remind_2_before is NULL in seeded policy → no second reminder; no pre-stamp needed.
        overdue_notified_at=_STAMPED,
        escalated_1_at=_STAMPED,
    )

    sm = get_sessionmaker()

    # First sweep: remind_1 fires
    summary = await sweep_task_timers(sm, _BASE)
    assert summary["steps"] >= 1

    count_after_first = await _count_notifications(assignee_id, task.id, EVENT_TASK_DUE_SOON)
    assert count_after_first == 1, f"Expected 1 remind notification, got {count_after_first}"

    # Second sweep at same now: idempotent — still exactly one
    await sweep_task_timers(sm, _BASE)
    count_after_second = await _count_notifications(assignee_id, task.id, EVENT_TASK_DUE_SOON)
    assert count_after_second == 1, (
        f"Expected 1 after re-sweep (idempotent), got {count_after_second}"
    )

    # Verify stamp is set
    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        assert task_fresh is not None
        assert task_fresh.remind_1_sent_at is not None, "remind_1_sent_at must be stamped"


async def test_overdue_is_critical(app_under_test: Any) -> None:
    """task.overdue fires when now >= due_at; its class is CRITICAL.

    Seeded policy: remind_1_before=3d, remind_2_before=1d, escalate_1_after=1d.
    Strategy: due_at = _BASE - 1h (overdue). Pre-stamp remind_* and escalated_1 to isolate overdue.
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Overdue Assignee")

    due_at = _BASE - datetime.timedelta(hours=1)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        escalated_1_at=_STAMPED,  # escalate_1_after=1d → threshold = _BASE - 1h + 1d > _BASE
    )

    sm = get_sessionmaker()
    await sweep_task_timers(sm, _BASE)

    count = await _count_notifications(assignee_id, task.id, EVENT_TASK_OVERDUE)
    assert count >= 1, f"Expected at least 1 overdue notification, got {count}"

    # Verify the class is CRITICAL
    assert class_of(EVENT_TASK_OVERDUE) == NotificationClass.CRITICAL


async def test_escalate_to_manager(app_under_test: Any) -> None:
    """Escalate fires to the manager; writes a TASK_ESCALATED AuditEvent; stamps escalated_1_at.

    Seeded policy: escalate_1_after=1d.
    Strategy: due_at = now - 2d → escalate threshold = due_at + 1d = now - 1d < now → FIRES.
    Pre-stamp remind_*/overdue to isolate the escalation step.

    NOTE: ``now`` is datetime.now(UTC) — not a fixed future date — because AuditEvent is monthly
    RANGE-partitioned and testcontainers only creates partitions for the current month.
    """
    now = _BASE
    org_id = await _default_org_id()
    manager_id = await _seed_user(org_id, display_name="Escalation Manager")
    assignee_id = await _seed_user(
        org_id, display_name="Escalation Assignee", manager_id=manager_id
    )

    # due_at = now - 2d: escalate fires (threshold now - 1d), remind/overdue pre-stamped
    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    sm = get_sessionmaker()
    await sweep_task_timers(sm, now)

    # Manager should have exactly one task.escalated notification
    count = await _count_notifications(manager_id, task.id, EVENT_TASK_ESCALATED)
    assert count == 1, f"Expected exactly 1 escalated notification for manager, got {count}"

    # TASK_ESCALATED audit event should exist exactly once
    audit_count = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_count == 1, f"Expected exactly 1 TASK_ESCALATED audit event, got {audit_count}"

    # Audit event content checks
    async with get_sessionmaker()() as s:
        ae = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.event_type == EventType.TASK_ESCALATED,
                        AuditEvent.scope_ref == str(task.id),
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ae is not None
    assert ae.actor_type == ActorType.system
    assert ae.actor_id is None
    assert ae.object_type == AuditObjectType.workflow_instance
    assert ae.after is not None
    assert str(task.id) in ae.after.get("task_id", "")
    assert str(manager_id) in ae.after.get("escalated_to", [])
    assert ae.after.get("via") == "manager"

    # escalated_1_at stamped
    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        assert task_fresh is not None
        assert task_fresh.escalated_1_at is not None, "escalated_1_at must be stamped"


async def test_escalate_fallback_to_qm(app_under_test: Any) -> None:
    """No manager → QMS Owner role-holder gets task.escalated.

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint
    (see test_escalate_to_manager).
    """
    now = _BASE
    org_id = await _default_org_id()
    qm_id = await _seed_user(org_id, display_name="QMS Owner Fallback Timer")
    await _assign_role(org_id, qm_id, "QMS Owner")
    assignee_id = await _seed_user(
        org_id, display_name="No-Manager Assignee For Timer", manager_id=None
    )

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    sm = get_sessionmaker()
    await sweep_task_timers(sm, now)

    count = await _count_notifications(qm_id, task.id, EVENT_TASK_ESCALATED)
    assert count == 1, f"Expected exactly 1 escalated notification for QM fallback, got {count}"

    # Audit event via must be qm_fallback (proves Fix 1 for the no-manager case)
    async with get_sessionmaker()() as s:
        ae = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.event_type == EventType.TASK_ESCALATED,
                        AuditEvent.scope_ref == str(task.id),
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ae is not None
    assert ae.after is not None
    assert ae.after.get("via") == "qm_fallback"


async def test_done_task_skipped(app_under_test: Any) -> None:
    """A DONE task past every threshold gets no notifications."""
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Done Task Assignee")

    due_at = _BASE - datetime.timedelta(days=3)
    _, task = await _seed_workflow_objects(
        org_id, assignee_id, due_at=due_at, task_state=TaskState.DONE
    )

    count_due_before = await _count_notifications(assignee_id, task.id, EVENT_TASK_DUE_SOON)
    count_overdue_before = await _count_notifications(assignee_id, task.id, EVENT_TASK_OVERDUE)
    count_escalated_before = await _count_notifications(assignee_id, task.id, EVENT_TASK_ESCALATED)

    sm = get_sessionmaker()
    await sweep_task_timers(sm, _BASE)

    assert (
        await _count_notifications(assignee_id, task.id, EVENT_TASK_DUE_SOON) == count_due_before
    ), "DONE task should not get due_soon notifications"
    assert (
        await _count_notifications(assignee_id, task.id, EVENT_TASK_OVERDUE) == count_overdue_before
    ), "DONE task should not get overdue notifications"
    assert (
        await _count_notifications(assignee_id, task.id, EVENT_TASK_ESCALATED)
        == count_escalated_before
    ), "DONE task should not get escalated notifications"


async def test_concurrency_escalate_once(app_under_test: Any) -> None:
    """Two concurrent process_task_timers on the same task → manager escalated exactly once.

    The per-task pg_advisory_xact_lock serialises the two coroutines; the second one finds
    escalated_1_at already stamped (due_steps returns []) and fires 0 steps.

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint
    (see test_escalate_to_manager).
    """
    now = _BASE
    org_id = await _default_org_id()
    manager_id = await _seed_user(org_id, display_name="Concurrency Manager")
    assignee_id = await _seed_user(
        org_id, display_name="Concurrency Assignee", manager_id=manager_id
    )

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    sm = get_sessionmaker()

    async def _run_one() -> int:
        async with sm() as session:
            return await process_task_timers(session, task_id=task.id, now=now)

    results = await asyncio.gather(_run_one(), _run_one())

    total_steps = sum(results)
    assert total_steps >= 1, (
        f"Expected ≥1 total steps fired between both runners, got {total_steps}"
    )

    # Manager escalated exactly once (advisory lock prevents double-send)
    count = await _count_notifications(manager_id, task.id, EVENT_TASK_ESCALATED)
    assert count == 1, f"Expected exactly 1 escalated notification (advisory lock), got {count}"

    # TASK_ESCALATED audit event exactly once
    audit_count = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_count == 1, (
        f"Expected exactly 1 TASK_ESCALATED audit event (advisory lock), got {audit_count}"
    )


async def test_doc_ack_no_escalation(app_under_test: Any) -> None:
    """DOC_ACK tasks get reminders + overdue but NO manager escalation (escalate_1_after=None).

    Seeded policy for DOC_ACK: remind_1_before=3d, remind_2_before=None, escalate_1_after=None.
    Strategy: due_at = now - 2d → past overdue AND past the would-be escalate threshold.
    Pre-stamp remind_1 / overdue; let only the escalate step be eligible — confirm it does NOT fire.

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint
    (see test_escalate_to_manager).
    """
    now = _BASE
    org_id = await _default_org_id()
    manager_id = await _seed_user(org_id, display_name="DocAck Manager")
    assignee_id = await _seed_user(org_id, display_name="DocAck Assignee", manager_id=manager_id)

    # due_at = now - 2d: past every threshold (remind, overdue, and the would-be escalate).
    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        task_type=TaskType.DOC_ACK,
        remind_1_sent_at=_STAMPED,  # pre-stamp remind to isolate the escalate check
        overdue_notified_at=_STAMPED,
    )

    # Capture pre-sweep counts (run-scoped).
    escalated_before = await _count_notifications(manager_id, task.id, EVENT_TASK_ESCALATED)
    audit_before = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))

    sm = get_sessionmaker()
    await sweep_task_timers(sm, now)

    # escalate step must NOT have fired (escalate_1_after=None for DOC_ACK).
    escalated_after = await _count_notifications(manager_id, task.id, EVENT_TASK_ESCALATED)
    assert escalated_after == escalated_before, (
        f"DOC_ACK task must not trigger task.escalated "
        f"(got {escalated_after}, was {escalated_before})"
    )

    audit_after = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_after == audit_before, (
        f"DOC_ACK task must not write TASK_ESCALATED audit event "
        f"(got {audit_after}, was {audit_before})"
    )

    # Confirm escalated_1_at is still NULL (step not stamped).
    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        assert task_fresh is not None
        assert task_fresh.escalated_1_at is None, "DOC_ACK escalated_1_at must remain NULL"


async def test_inactive_manager_falls_through_to_qm(app_under_test: Any) -> None:
    """An inactive manager is skipped; the QMS Owner fallback receives the escalation instead.

    resolve_escalation_recipients accepts the manager only when active AND in the same org.
    When the manager is LOCKED (inactive), it must fall through to the QMS Owner pool.

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint
    (see test_escalate_to_manager).
    """
    now = _BASE
    org_id = await _default_org_id()
    qm_id = await _seed_user(org_id, display_name="QMS Owner Inactive-Mgr Fallback")
    await _assign_role(org_id, qm_id, "QMS Owner")

    # Seed an inactive (LOCKED) manager — _recipient_for_user would drop this user.
    inactive_manager_id = await _seed_user(
        org_id, display_name="Inactive Manager", email="inactive-mgr@example.com"
    )
    # Mark the manager LOCKED (inactive) via a direct DB update.
    async with get_sessionmaker()() as s:
        mgr = await s.get(AppUser, inactive_manager_id)
        assert mgr is not None
        mgr.status = UserStatus.LOCKED
        await s.commit()

    assignee_id = await _seed_user(
        org_id, display_name="Assignee With Inactive Mgr", manager_id=inactive_manager_id
    )

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    sm = get_sessionmaker()
    await sweep_task_timers(sm, now)

    # Inactive manager must NOT receive a notification.
    count_inactive = await _count_notifications(inactive_manager_id, task.id, EVENT_TASK_ESCALATED)
    assert count_inactive == 0, (
        f"Inactive manager must not receive task.escalated (got {count_inactive})"
    )

    # QMS Owner fallback must receive the notification.
    count_qm = await _count_notifications(qm_id, task.id, EVENT_TASK_ESCALATED)
    assert count_qm == 1, (
        f"QMS Owner fallback must receive task.escalated when manager is inactive (got {count_qm})"
    )

    # Audit via must be qm_fallback (proves the fallback path was taken).
    async with get_sessionmaker()() as s:
        ae = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.event_type == EventType.TASK_ESCALATED,
                        AuditEvent.scope_ref == str(task.id),
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ae is not None
    assert ae.after is not None
    assert ae.after.get("via") == "qm_fallback", (
        f"Expected via=qm_fallback when manager is inactive, got {ae.after.get('via')}"
    )


async def test_no_email_manager_gets_inapp_escalation(app_under_test: Any) -> None:
    """R2-3: an active manager with email=None still receives the in-app task.escalated row.

    _recipient_for_user no longer drops a user for having a NULL email — only email-row
    creation depends on an address.  The in-app notification row must be created regardless.

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = _BASE
    org_id = await _default_org_id()

    # Seed a manager with no email address (valid: AppUser.email is nullable).
    no_email_manager_id = await _seed_user(org_id, display_name="No Email Manager", email=None)
    assignee_id = await _seed_user(
        org_id, display_name="Assignee No-Email Mgr", manager_id=no_email_manager_id
    )

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    sm = get_sessionmaker()
    await sweep_task_timers(sm, now)

    # The in-app notification row must exist for the no-email manager.
    count = await _count_notifications(no_email_manager_id, task.id, EVENT_TASK_ESCALATED)
    assert count == 1, f"No-email manager must receive in-app task.escalated row (got {count})"

    # escalated_1_at must be stamped.
    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        assert task_fresh is not None
        assert task_fresh.escalated_1_at is not None, (
            "escalated_1_at must be stamped when no-email manager is the valid recipient"
        )


async def test_no_recipient_stamps_and_does_not_repeat(app_under_test: Any) -> None:
    """R2-2: a step with no valid recipient (no manager + no QM in org) is stamped as a
    terminal no-op.  A second sweep must not reprocess the already-stamped step.

    Distinguishes terminal no-op (attempted=0 → stamp) from a delivery miss
    (attempted>0, notified=False → don't stamp, retry).

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = _BASE
    org_id = await _default_org_id()

    # Assignee with no manager and no QMS Owner in the org (use a unique assignee
    # so no pre-existing QMS Owner assignment in this org affects the test).
    # We don't assign the QMS Owner role to anyone for this task's scope.
    # Use a unique-enough assignee display name to avoid cross-test collisions.
    assignee_id = await _seed_user(
        org_id, display_name="No Recipient Assignee R22", manager_id=None
    )

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
        # escalated_1_at left NULL → step is eligible
    )

    sm = get_sessionmaker()

    # First sweep: the escalate step fires but has no valid recipient.
    # It should stamp escalated_1_at (terminal no-op).
    await sweep_task_timers(sm, now)

    async with get_sessionmaker()() as s:
        task_after_first = await s.get(Task, task.id)
        assert task_after_first is not None
        assert task_after_first.escalated_1_at is not None, (
            "escalated_1_at must be stamped even when there are no valid recipients "
            "(terminal no-op must not loop)"
        )

    # Second sweep: the step is now stamped → due_steps returns [] → 0 steps fired for this task.
    # We cannot assert result2["steps"] == 0 globally (other tasks in the shared DB may fire),
    # so we check that the stamp has not moved (still the value from sweep 1).
    stamp_after_first = task_after_first.escalated_1_at
    await sweep_task_timers(sm, now)
    async with get_sessionmaker()() as s:
        task_after_second = await s.get(Task, task.id)
        assert task_after_second is not None
        assert task_after_second.escalated_1_at == stamp_after_first, (
            "escalated_1_at must not change on a re-sweep (step already stamped)"
        )

    # No escalation notification must have been created (no recipient to send to).
    count = await _count_notifications(assignee_id, task.id, EVENT_TASK_ESCALATED)
    assert count == 0, (
        f"No task.escalated notification expected when there are no valid recipients (got {count})"
    )


async def test_cross_org_qm_fallback_not_notified(app_under_test: Any) -> None:
    """R2-4: a QMS Owner role assignment whose app_user belongs to a different org is filtered out
    by _recipient_for_user's org_id check.  The cross-org user must not receive the escalation.

    The step is stamped as a terminal no-op (attempted=0 after the org filter).

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = _BASE
    org_id = await _default_org_id()

    # Create a second org so we have a cross-org user to assign.
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        other_org = Organization(
            legal_name=f"Cross Org {salt}",
            short_code=f"XO{salt[:6].upper()}",
        )
        s.add(other_org)
        await s.commit()
        other_org_id = other_org.id

    # Seed a user in the OTHER org.
    cross_org_user_id = await _seed_user(
        other_org_id,
        display_name="Cross Org QM",
        email=f"cross-qm-{salt}@example.com",
    )

    # Assign the "QMS Owner" role to the cross-org user IN the task's org.
    # (ops tooling can grant a role to an existing subject without moving the user)
    await _assign_role(org_id, cross_org_user_id, "QMS Owner")

    # Assignee with no manager in the task org → escalation falls through to QM fallback.
    assignee_id = await _seed_user(org_id, display_name="Assignee Cross-Org QM", manager_id=None)

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    try:
        sm = get_sessionmaker()
        await sweep_task_timers(sm, now)

        # Cross-org QM must NOT receive an escalation notification.
        count = await _count_notifications(cross_org_user_id, task.id, EVENT_TASK_ESCALATED)
        assert count == 0, f"Cross-org QM must not receive task.escalated (got {count})"

        # The step must be stamped (terminal no-op — org filter produced attempted=0).
        async with get_sessionmaker()() as s:
            task_fresh = await s.get(Task, task.id)
            assert task_fresh is not None
            assert task_fresh.escalated_1_at is not None, (
                "escalated_1_at must be stamped when the only QM candidate is cross-org"
            )
    finally:
        # Remove the extra org (other_org_id) so it doesn't pollute the shared integration DB.
        # Other tests (e.g. test_restore.py) use scalar_one() on Organization and fail if > 1 row.
        # FK-safe order: role_assignment → app_user → organization.
        async with get_sessionmaker()() as s:
            await s.execute(
                delete(RoleAssignment).where(RoleAssignment.user_id == cross_org_user_id)
            )
            await s.execute(delete(AppUser).where(AppUser.id == cross_org_user_id))
            await s.execute(delete(Organization).where(Organization.id == other_org_id))
            await s.commit()


async def test_self_manager_falls_through_to_qm(app_under_test: Any) -> None:
    """R3-1: when manager_id == assignee.id the manager is treated as missing.

    The self-manager is not an independent owner, so the escalation must go to the QMS Owner
    fallback (or the empty list) rather than echoing back to the same person.
    Audit via must be 'qm_fallback', NOT 'manager'.

    NOTE: now is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = _BASE
    org_id = await _default_org_id()

    qm_id = await _seed_user(org_id, display_name="QMS Owner Self-Mgr Fallback R31")
    await _assign_role(org_id, qm_id, "QMS Owner")

    # Seed an assignee whose manager_id points at themselves (self-manager).
    # We need the user's own id, so seed without manager_id first, then update.
    assignee_id = await _seed_user(org_id, display_name="Self-Manager Assignee R31")
    async with get_sessionmaker()() as s:
        assignee = await s.get(AppUser, assignee_id)
        assert assignee is not None
        assignee.manager_id = assignee.id  # type: ignore[assignment]  # self-FK
        await s.commit()

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    sm = get_sessionmaker()
    await sweep_task_timers(sm, now)

    # Self-manager must NOT receive an escalation notification (they are the assignee).
    count_self = await _count_notifications(assignee_id, task.id, EVENT_TASK_ESCALATED)
    assert count_self == 0, (
        f"Self-manager must not receive task.escalated as if they were an independent "
        f"escalation target (got {count_self})"
    )

    # QMS Owner fallback must receive the notification.
    count_qm = await _count_notifications(qm_id, task.id, EVENT_TASK_ESCALATED)
    assert count_qm == 1, (
        f"QMS Owner fallback must receive task.escalated when assignee is their own manager "
        f"(got {count_qm})"
    )

    # Audit via must be 'qm_fallback' (prove the fallback path was taken, not 'manager').
    async with get_sessionmaker()() as s:
        ae = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.event_type == EventType.TASK_ESCALATED,
                        AuditEvent.scope_ref == str(task.id),
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ae is not None
    assert ae.after is not None
    assert ae.after.get("via") == "qm_fallback", (
        f"Expected via=qm_fallback for a self-manager, got {ae.after.get('via')!r}"
    )

    # escalated_1_at must be stamped (QM fallback notified).
    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        assert task_fresh is not None
        assert task_fresh.escalated_1_at is not None, (
            "escalated_1_at must be stamped after QM fallback escalation"
        )


async def test_dedup_hit_stamps_step_not_infinite_retry(app_under_test: Any) -> None:
    """R3-2: a step whose notification already exists but whose stamp is NULL is treated as
    already-emitted.  The sweep must stamp escalated_1_at and a re-sweep must be a no-op.

    Simulates the downgrade→re-upgrade state: the notification row survives the downgrade
    (no DELETE on notification), the stamp columns are dropped then recreated as NULL, and
    the next sweep's ON CONFLICT DO NOTHING dedup path now returns True → step is stamped.

    NOTE: now is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = _BASE
    org_id = await _default_org_id()

    manager_id = await _seed_user(org_id, display_name="Dedup Manager R32")
    assignee_id = await _seed_user(org_id, display_name="Dedup Assignee R32", manager_id=manager_id)

    # Seed the task with escalated_1_at=NULL (un-stamped) so the sweep considers the step due.
    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
        # escalated_1_at=None → step is eligible
    )

    # Pre-insert the notification row directly, simulating the notification that survived
    # the downgrade.  No stamp on the task yet (escalated_1_at is NULL).
    async with get_sessionmaker()() as s:
        s.add(
            Notification(
                org_id=org_id,
                recipient_user_id=manager_id,
                event_key=EVENT_TASK_ESCALATED,
                subject_type="document",
                subject_id=uuid.uuid4(),  # phantom
                task_id=task.id,
                title="Pre-existing escalation",
                body="Pre-existing escalation body",
                deep_link="/tasks",
                template_id=None,
                template_version=None,
                context={},
            )
        )
        await s.commit()

    sm = get_sessionmaker()

    # First sweep: hits the dedup (ON CONFLICT DO NOTHING) → must now stamp (not loop forever).
    await sweep_task_timers(sm, now)

    async with get_sessionmaker()() as s:
        task_after_first = await s.get(Task, task.id)
        assert task_after_first is not None
        assert task_after_first.escalated_1_at is not None, (
            "escalated_1_at must be stamped when the notification already exists (dedup = emitted)"
        )

    stamp_after_first = task_after_first.escalated_1_at

    # Second sweep: step is now stamped → due_steps returns [] → stamp must not change.
    await sweep_task_timers(sm, now)
    async with get_sessionmaker()() as s:
        task_after_second = await s.get(Task, task.id)
        assert task_after_second is not None
        assert task_after_second.escalated_1_at == stamp_after_first, (
            "escalated_1_at must not change on a re-sweep after dedup-stamp (no infinite retry)"
        )

    # Exactly one notification row must exist (no duplicate created by the sweep).
    count = await _count_notifications(manager_id, task.id, EVENT_TASK_ESCALATED)
    assert count == 1, (
        f"Sweep must not create a duplicate notification row on a dedup path (got {count})"
    )


async def test_template_miss_does_not_stamp(app_under_test: Any) -> None:
    """R3-2 regression guard: a genuine template miss (attempted>0) must NOT stamp.

    Uses a synthetic event_key that has no effective template in the DB so _enqueue_one
    returns "no_template".  Calls emit_task_event directly (the non-swallowing helper) and
    verifies the return value, then confirms the task's escalated_1_at is still NULL.

    This guards against regressing R2-2: a delivery miss must stay retryable.
    """
    now = _BASE
    org_id = await _default_org_id()

    recipient_id = await _seed_user(org_id, display_name="Template Miss Recipient R32guard")
    assignee_id = await _seed_user(org_id, display_name="Template Miss Assignee R32guard")
    instance, task = await _seed_workflow_objects_for_escalation(org_id, assignee_id, now=now)

    recipient = Recipient(
        user_id=recipient_id,
        email="tmiss@example.com",
        display_name="Template Miss Recipient R32guard",
        first_name="Template",
        email_enabled=True,
    )

    # Use a synthetic event_key that has no effective template — render() returns None.
    bogus_key = "task.no_such_template_r32guard"

    async with get_sessionmaker()() as s:
        inst_fresh = await s.get(WorkflowInstance, instance.id)
        task_fresh = await s.get(Task, task.id)
        result = await emit_task_event(
            s,
            instance=inst_fresh,
            task=task_fresh,
            recipient=recipient,
            event_key=bogus_key,
            now=now,
        )
        await s.commit()

    assert result == "no_template", (
        f"emit_task_event must return 'no_template' on a template miss, got {result!r}"
    )

    # No notification row must have been created.
    count = await _count_notifications(recipient_id, task.id, bogus_key)
    assert count == 0, f"No notification row must exist after a template miss (got {count})"

    # escalated_1_at must still be NULL (the caller would not stamp on False).
    async with get_sessionmaker()() as s:
        task_fresh2 = await s.get(Task, task.id)
        assert task_fresh2 is not None
        assert task_fresh2.escalated_1_at is None, (
            "escalated_1_at must remain NULL after a template miss (must not stamp on False)"
        )


async def _seed_workflow_objects_for_escalation(
    org_id: uuid.UUID,
    assignee_user_id: uuid.UUID,
    *,
    now: datetime.datetime,
) -> tuple[WorkflowInstance, Task]:
    """Helper for tests that need a workflow instance + task without timer stamps."""
    due_at = now - datetime.timedelta(days=2)
    return await _seed_workflow_objects(
        org_id,
        assignee_user_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )


# ---------------------------------------------------------------------------
# R4 tests
# ---------------------------------------------------------------------------


async def test_r4_1_dedup_sweep_does_not_write_second_audit(app_under_test: Any) -> None:
    """R4-1: a dedup sweep (notification already exists + stamp NULL) stamps the step but
    must NOT write a second TASK_ESCALATED audit row.

    Simulates the downgrade→re-upgrade state: notification + original TASK_ESCALATED audit
    survive the downgrade, stamp columns are recreated as NULL.  The next sweep's ON CONFLICT
    DO NOTHING dedup path returns "deduped" → step is stamped, no new audit appended.

    NOTE: now is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = _BASE
    org_id = await _default_org_id()

    manager_id = await _seed_user(org_id, display_name="Dedup Audit Manager R41")
    assignee_id = await _seed_user(
        org_id, display_name="Dedup Audit Assignee R41", manager_id=manager_id
    )

    # Seed the task with escalated_1_at=NULL (un-stamped) so the sweep considers the step due.
    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
        # escalated_1_at=None → step is eligible
    )

    # Pre-insert both the notification row AND the TASK_ESCALATED audit row, simulating
    # the state after a downgrade: both rows survive but stamp columns are NULL again.
    async with get_sessionmaker()() as s:
        s.add(
            Notification(
                org_id=org_id,
                recipient_user_id=manager_id,
                event_key=EVENT_TASK_ESCALATED,
                subject_type="document",
                subject_id=uuid.uuid4(),  # phantom
                task_id=task.id,
                title="Pre-existing escalation R41",
                body="Pre-existing escalation body R41",
                deep_link="/tasks",
                template_id=None,
                template_version=None,
                context={},
            )
        )
        s.add(
            AuditEvent(
                org_id=org_id,
                occurred_at=now - datetime.timedelta(days=1),  # originally written before downgrade
                actor_id=None,
                actor_type=ActorType.system,
                event_type=EventType.TASK_ESCALATED,
                object_type=AuditObjectType.workflow_instance,
                object_id=uuid.uuid4(),  # phantom instance id — scope_ref is the lookup key
                scope_ref=str(task.id),
                after={
                    "task_id": str(task.id),
                    "escalated_to": [str(manager_id)],
                    "via": "manager",
                    "due_at": due_at.isoformat(),
                },
            )
        )
        await s.commit()

    audit_before = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_before == 1, f"Expected 1 pre-existing audit row, got {audit_before}"

    sm = get_sessionmaker()

    # Sweep: hits the dedup (ON CONFLICT DO NOTHING) → must stamp but NOT write a second audit.
    await sweep_task_timers(sm, now)

    # Step must be stamped.
    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        assert task_fresh is not None
        assert task_fresh.escalated_1_at is not None, (
            "escalated_1_at must be stamped after a dedup sweep"
        )

    # Audit count must stay at 1 (no duplicate appended).
    audit_after = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_after == 1, (
        f"TASK_ESCALATED audit count must remain 1 after a dedup sweep (got {audit_after})"
    )

    # Re-sweep must be a no-op (stamp already set → due_steps returns []).
    await sweep_task_timers(sm, now)
    audit_after_resweep = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_after_resweep == 1, (
        f"Audit count must remain 1 after re-sweep (got {audit_after_resweep})"
    )


async def test_r4_1_genuine_new_escalation_writes_exactly_one_audit(app_under_test: Any) -> None:
    """R4-1 positive path: a genuinely-new escalation writes exactly one TASK_ESCALATED audit
    with escalated_to populated, and a re-sweep does not append a second row.

    NOTE: now is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = _BASE
    org_id = await _default_org_id()

    manager_id = await _seed_user(org_id, display_name="New Esc Manager R41pos")
    assignee_id = await _seed_user(
        org_id, display_name="New Esc Assignee R41pos", manager_id=manager_id
    )

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
        # escalated_1_at=None → step is eligible; no pre-existing notification or audit
    )

    audit_before = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_before == 0, f"Expected 0 audit rows before sweep, got {audit_before}"

    sm = get_sessionmaker()
    await sweep_task_timers(sm, now)

    # Exactly one TASK_ESCALATED audit row must exist.
    audit_after = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_after == 1, (
        f"Expected exactly 1 TASK_ESCALATED audit after a new escalation (got {audit_after})"
    )

    # escalated_to must contain the manager id.
    async with get_sessionmaker()() as s:
        ae = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.event_type == EventType.TASK_ESCALATED,
                        AuditEvent.scope_ref == str(task.id),
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ae is not None
    assert ae.after is not None
    assert str(manager_id) in ae.after.get("escalated_to", []), (
        f"escalated_to must include the manager id (got {ae.after.get('escalated_to')})"
    )

    # Re-sweep must not append a second audit row.
    await sweep_task_timers(sm, now)
    audit_after_resweep = await _count_audit_events(org_id, EventType.TASK_ESCALATED, str(task.id))
    assert audit_after_resweep == 1, (
        f"Re-sweep must not create a second audit row (got {audit_after_resweep})"
    )


async def test_r4_2_self_manager_sole_qm_via_is_qm_fallback(app_under_test: Any) -> None:
    """R4-2: when the assignee is their own manager AND is the only QMS Owner, the escalation
    must go to that QM-fallback recipient and the audit via must be 'qm_fallback', NOT 'manager'.

    This is the specific edge-case that R4-2 fixes: manager_id_for_task == assignee.id, so
    resolve_escalation_recipients falls through to the QM pool (which is [assignee.id]), but
    the old via computation would label it 'manager' because recipient_ids == [manager_id_for_task].

    NOTE: now is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = _BASE
    org_id = await _default_org_id()

    # Seed the assignee who will also be the sole QMS Owner (self-manager edge case).
    assignee_id = await _seed_user(org_id, display_name="Self-Mgr Sole-QM Assignee R42")

    # Point manager_id at themselves (self-manager).
    async with get_sessionmaker()() as s:
        assignee = await s.get(AppUser, assignee_id)
        assert assignee is not None
        assignee.manager_id = assignee.id  # type: ignore[assignment]  # self-FK
        await s.commit()

    # Assign QMS Owner role to the assignee so they are in the QM fallback pool.
    await _assign_role(org_id, assignee_id, "QMS Owner")

    due_at = now - datetime.timedelta(days=2)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    sm = get_sessionmaker()
    await sweep_task_timers(sm, now)

    # The assignee (sole QM) receives the escalation (QM fallback path).
    count = await _count_notifications(assignee_id, task.id, EVENT_TASK_ESCALATED)
    assert count == 1, (
        f"Sole QM self-manager must receive task.escalated as the QM fallback (got {count})"
    )

    # Audit via must be 'qm_fallback', not 'manager'.
    async with get_sessionmaker()() as s:
        ae = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.org_id == org_id,
                        AuditEvent.event_type == EventType.TASK_ESCALATED,
                        AuditEvent.scope_ref == str(task.id),
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ae is not None
    assert ae.after is not None
    assert ae.after.get("via") == "qm_fallback", (
        f"Expected via=qm_fallback for a self-manager sole QM, got {ae.after.get('via')!r}"
    )

    # escalated_1_at must be stamped.
    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        assert task_fresh is not None
        assert task_fresh.escalated_1_at is not None, (
            "escalated_1_at must be stamped after QM fallback escalation"
        )


# ---------------------------------------------------------------------------
# S-notify-6: working-calendar resolution + business-day escalation wiring.
# ---------------------------------------------------------------------------


async def test_resolve_working_calendar_reads_default_row(app_under_test: Any) -> None:
    """resolve_working_calendar reflects the org's is_default row (holidays + tz round-trip).

    UPDATE AHT's seeded default in place, assert, then RESTORE in finally (working_calendar keeps
    UPDATE for the app role; a 2nd is_default would violate uq_working_calendar_one_default, and the
    row is DELETE-revoked, so update-and-restore is the only safe path)."""
    org_id = await _default_org_id()
    async with get_sessionmaker()() as s:
        before = (
            await s.execute(
                select(WorkingCalendar).where(
                    WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one_or_none()
    assert before is not None, "0067 must seed an is_default calendar for the default org"
    orig = {
        "working_days": list(before.working_days),
        "holidays": list(before.holidays),
        "timezone": before.timezone,
    }
    try:
        async with get_sessionmaker()() as s:
            await s.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.id == before.id)
                .values(
                    working_days=[1, 2, 3, 4, 5],
                    holidays=["2026-12-25"],
                    timezone="America/New_York",
                )
            )
            await s.commit()
        async with get_sessionmaker()() as s:
            cal = await resolve_working_calendar(s, org_id)
        assert cal.working_weekdays == frozenset({1, 2, 3, 4, 5})
        assert datetime.date(2026, 12, 25) in cal.holidays
        assert cal.tz == zoneinfo.ZoneInfo("America/New_York")
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(
                update(WorkingCalendar).where(WorkingCalendar.id == before.id).values(**orig)
            )
            await s.commit()


async def test_resolve_working_calendar_missing_row_falls_back_to_default(
    app_under_test: Any,
) -> None:
    """An org with no working_calendar row resolves to a Mon-Fri default in the org's resolved tz
    (NOT a crash). Post S-orgtz-unify the fallback tz is pick_tz(None, organization.timezone), which
    equals resolve_org_tz — assert structure + tz parity, not the UTC DEFAULT_CALENDAR constant."""
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(legal_name=f"NoCal Org {salt}", short_code=f"NC{salt[:6].upper()}")
        s.add(org)
        await s.commit()
        no_cal_org_id = org.id
    try:
        async with get_sessionmaker()() as s:
            cal = await resolve_working_calendar(s, no_cal_org_id)
            assert cal.working_weekdays == frozenset({1, 2, 3, 4, 5})
            assert cal.holidays == frozenset()
            assert cal.tz == await resolve_org_tz(s, no_cal_org_id)  # parity by construction
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Organization).where(Organization.id == no_cal_org_id))
            await s.commit()


async def test_resolve_working_calendar_malformed_holidays_does_not_crash(
    app_under_test: Any,
) -> None:
    """A non-list `holidays` JSONB scalar must not raise (fail-safe) — treated as no holidays, the
    valid week mask + tz kept. Guards the future-editor robustness contract. UPDATE-AHT-restore."""
    org_id = await _default_org_id()
    async with get_sessionmaker()() as s:
        before = (
            await s.execute(
                select(WorkingCalendar).where(
                    WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one_or_none()
    assert before is not None
    orig_holidays = list(before.holidays)
    try:
        async with get_sessionmaker()() as s:
            # Write a structurally-broken scalar (5) into the JSONB holidays column.
            await s.execute(
                update(WorkingCalendar).where(WorkingCalendar.id == before.id).values(holidays=5)
            )
            await s.commit()
        async with get_sessionmaker()() as s:
            cal = await resolve_working_calendar(s, org_id)  # must NOT raise
        assert cal.holidays == frozenset(), "a non-list holidays scalar -> no holidays (kept-safe)"
        assert cal.working_weekdays == frozenset({1, 2, 3, 4, 5}), "valid week mask kept"
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.id == before.id)
                .values(holidays=orig_holidays)
            )
            await s.commit()


async def test_resolve_working_calendar_bad_working_days_falls_back_keeping_tz(
    app_under_test: Any,
) -> None:
    """A structurally-broken `working_days` (a JSON string "67" → would wrongly become {6,7}) falls
    back to the Mon-Fri DEFAULT WEEKDAYS but KEEPS the row's VALID timezone (Codex round-2) — else
    the is_working_day(now) gate would judge weekends in UTC for a non-UTC org. UPDATE+restore."""
    org_id = await _default_org_id()
    async with get_sessionmaker()() as s:
        before = (
            await s.execute(
                select(WorkingCalendar).where(
                    WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one_or_none()
    assert before is not None
    orig = {"working_days": list(before.working_days), "timezone": before.timezone}
    try:
        async with get_sessionmaker()() as s:
            await s.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.id == before.id)
                .values(working_days="67", timezone="America/New_York")
            )
            await s.commit()
        async with get_sessionmaker()() as s:
            cal = await resolve_working_calendar(s, org_id)
        assert cal.working_weekdays == frozenset({1, 2, 3, 4, 5}), "bad working_days -> Mon-Fri"
        assert cal.tz == zoneinfo.ZoneInfo("America/New_York"), "the VALID tz must be preserved"
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(
                update(WorkingCalendar).where(WorkingCalendar.id == before.id).values(**orig)
            )
            await s.commit()


async def test_escalation_skips_weekend_business_day(app_under_test: Any) -> None:
    """Wiring proof: escalation fires one BUSINESS day after a Friday due_at — Monday, not Saturday.

    Uses AHT's seeded Mon-Fri calendar (or the DEFAULT_CALENDAR fallback — both Mon-Fri) AS-IS,
    no mutation, no holiday. Anti-tautology: Test A FAILS against the old raw-wall-clock timer.py
    (which escalates on Saturday). Pre-stamp remind+overdue to isolate ESCALATE_1. Dates are built
    in the resolved calendar's own tz so the test is correct for any seeded tz (the weekday of a
    calendar date is tz-independent)."""
    org_id = await _default_org_id()
    manager_id = await _seed_user(org_id, display_name="Weekend Escalation Manager")
    assignee_id = await _seed_user(
        org_id, display_name="Weekend Escalation Assignee", manager_id=manager_id
    )
    async with get_sessionmaker()() as s:
        cal = await resolve_working_calendar(s, org_id)
    tz = cal.tz
    # due_at = Fri 2026-06-26 10:00 local; raw escalate = Sat 06-27, business escalate = Mon 06-29.
    due_at = datetime.datetime(2026, 6, 26, 10, 0, tzinfo=tz)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )
    sm = get_sessionmaker()

    # Test A: now = Sat 06-27 18:00 local — past raw due+1d, BEFORE the business Monday threshold.
    now_sat = datetime.datetime(2026, 6, 27, 18, 0, tzinfo=tz)
    await sweep_task_timers(sm, now_sat)
    async with get_sessionmaker()() as s:
        t = await s.get(Task, task.id)
        assert t is not None and t.escalated_1_at is None, (
            "must NOT escalate on a Saturday (business-day)"
        )
        esc = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.task_id == task.id,
                        Notification.event_key == EVENT_TASK_ESCALATED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert esc == [], "no task.escalated notification before the business threshold"

    # Test B: now = Monday 06-29 12:00 local — the business escalate threshold has passed.
    now_mon = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=tz)
    await sweep_task_timers(sm, now_mon)
    async with get_sessionmaker()() as s:
        t = await s.get(Task, task.id)
        assert t is not None and t.escalated_1_at is not None, (
            "must escalate on Monday (business-day)"
        )
        esc = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == manager_id,
                        Notification.task_id == task.id,
                        Notification.event_key == EVENT_TASK_ESCALATED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(esc) == 1, "exactly one escalation notification to the manager on Monday"


async def test_due_task_ids_excludes_fully_fired_escalate_enabled(app_under_test: Any) -> None:
    """A fully-fired OPEN APPROVE task is NOT re-claimed (all configured steps stamped).

    Post-S-remind2 remind_2 is a real configured step (remind_2_before=1d), so a fully-fired task
    stamps all four steps. This is now a plain fully-fired regression guard for the policy-aware
    claim (the original remind_2 tautology no longer exists for APPROVE — remind_2 is real).
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Claim Fully Fired APPROVE")
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=_BASE - datetime.timedelta(days=2),  # well past due; OPEN (PENDING)
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
        escalated_1_at=_STAMPED,
    )

    async with get_sessionmaker()() as s:
        ids = await _due_task_ids(s, _BASE)

    assert task.id not in ids, (
        "fully-fired APPROVE task must NOT be re-claimed (remind_2 tautology closed)"
    )


async def test_due_task_ids_excludes_fully_fired_doc_ack(app_under_test: Any) -> None:
    """A fully-fired OPEN DOC_ACK task is NOT re-claimed (mutation-distinguishing via escalate).

    DOC_ACK is escalate-exempt (escalate_1_after=None) → escalated_1_at stays NULL forever. With
    remind_1 + remind_2 + overdue stamped, the new policy-aware claim gates the escalate clause on
    escalate_1_after IS NOT NULL (false for DOC_ACK) → not claimed; the OLD 4-way claim re-selected
    it via `escalated_1_at IS NULL` → this case remains RED-on-old / GREEN-on-new.
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Claim Fully Fired DOC_ACK")
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=_BASE - datetime.timedelta(days=2),  # well past due; OPEN (PENDING)
        task_type=TaskType.DOC_ACK,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
        # escalated_1_at stays NULL (escalate never fires for DOC_ACK — escalate_1_after=None).
    )

    async with get_sessionmaker()() as s:
        ids = await _due_task_ids(s, _BASE)

    assert task.id not in ids, (
        "fully-fired DOC_ACK task must NOT be re-claimed (remind_2 + escalate tautologies closed)"
    )


async def test_due_task_ids_still_claims_pending_steps(app_under_test: Any) -> None:
    """Over-tightening guard: a task with ANY configured, unstamped step IS still claimed.

    Four positive controls: three on escalate-enabled APPROVE tasks (each isolates one claim
    reason) and one on DOC_ACK to prove the policy JOIN resolves for that type.
    These pass on BOTH the old and new query — they prove the policy-aware predicate did not drop
    a task that genuinely has a fireable step.
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Claim Pending Steps")
    past_due = _BASE - datetime.timedelta(days=2)

    # (a) pending remind_1: remind_1_sent_at NULL → claimable
    # (remind_2_sent_at also NULL but inert).
    _, task_remind = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=past_due,
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        overdue_notified_at=_STAMPED,
        escalated_1_at=_STAMPED,
    )
    # (b) pending overdue: overdue_notified_at NULL → claimable via always-on step
    # (remind_2_sent_at also NULL but inert).
    _, task_overdue = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=past_due,
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        escalated_1_at=_STAMPED,
    )
    # (c) pending escalate: escalated_1_at NULL → claimable (APPROVE escalate_1_after=1d;
    # remind_2_sent_at also NULL but inert).
    _, task_escalate = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=past_due,
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )
    # (d) DOC_ACK pending overdue: proves the DOC_ACK policy JOIN resolves, so the DOC_ACK
    # EXCLUSION test (test_due_task_ids_excludes_fully_fired_doc_ack) is genuinely
    # mutation-distinguishing and not a silent pass-for-wrong-reason if the seed ever changes.
    _, task_doc_ack = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=past_due,
        task_type=TaskType.DOC_ACK,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        # overdue_notified_at NULL → claimed via the always-on OVERDUE disjunct.
    )

    async with get_sessionmaker()() as s:
        ids = await _due_task_ids(s, _BASE)

    assert task_remind.id in ids, "pending remind_1 task must still be claimed"
    assert task_overdue.id in ids, "pending overdue task must still be claimed"
    assert task_escalate.id in ids, "pending escalate task must still be claimed"
    assert task_doc_ack.id in ids, "pending-overdue DOC_ACK must be claimed (policy join resolves)"


async def test_remind_2_distinct_final_reminder(app_under_test: Any) -> None:
    """REMIND_2 fires under a DISTINCT event key (task.due_final) → a real second reminder.

    S-remind2: migration 0068 sets sla_policy.remind_2_before = 1 day. A task whose `now` is past
    BOTH reminder thresholds (with overdue + escalate pre-stamped to isolate the reminders) produces
    TWO distinct notifications — exactly one task.due_soon AND exactly one task.due_final — for the
    assignee. Under the OLD shared-key path the second insert hit the (recipient,
    task_id, event_key) dedup and delivered nothing (only one notification).
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Remind2 Final Assignee")

    # due_at = _BASE - 2d (Mon): remind_1 threshold (due-3bd) AND remind_2 threshold (due-1bd) are
    # both <= _BASE. Pre-stamp overdue + escalate so ONLY the two reminders can fire.
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=_BASE - datetime.timedelta(days=2),
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        overdue_notified_at=_STAMPED,
        escalated_1_at=_STAMPED,
    )

    await sweep_task_timers(get_sessionmaker(), _BASE)

    due_soon = await _count_notifications(assignee_id, task.id, EVENT_TASK_DUE_SOON)
    due_final = await _count_notifications(assignee_id, task.id, EVENT_TASK_DUE_FINAL)
    assert due_soon == 1, f"expected exactly one task.due_soon, got {due_soon}"
    assert due_final == 1, f"expected exactly one task.due_final, got {due_final}"

    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        assert task_fresh is not None
        assert task_fresh.remind_1_sent_at is not None, "remind_1 must be stamped"
        assert task_fresh.remind_2_sent_at is not None, "remind_2 must be stamped"
