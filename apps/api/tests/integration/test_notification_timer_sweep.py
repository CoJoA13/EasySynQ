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
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.notification import Notification
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.workflow import Task, WorkflowDefinition, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.classes import NotificationClass, class_of
from easysynq_api.services.notifications.constants import (
    EVENT_TASK_DUE_SOON,
    EVENT_TASK_ESCALATED,
    EVENT_TASK_OVERDUE,
)
from easysynq_api.services.notifications.escalation import process_task_timers, sweep_task_timers

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixed reference time: a point in the past with no quiet-window overlap.
# Seeded SLA policy (migration 0065): remind_1_before=3d, remind_2_before=1d, escalate_1_after=1d.
# ---------------------------------------------------------------------------
_BASE = datetime.datetime(2032, 3, 10, 10, 0, 0, tzinfo=datetime.UTC)  # 10:00 UTC

# A sentinel "already sent" stamp: used to pre-stamp steps we don't want a test to fire.
_STAMPED = datetime.datetime(2032, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)


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
    remind_2 is off (NULL remind_2_before) → no second reminder, no pre-stamp needed.
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
    now = datetime.datetime.now(datetime.UTC)
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
    now = datetime.datetime.now(datetime.UTC)
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
    now = datetime.datetime.now(datetime.UTC)
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
    now = datetime.datetime.now(datetime.UTC)
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

    resolve_escalation_recipients now mirrors _recipient_for_user's skip criteria: it only returns
    [manager_id] when the manager is active (status not in _INACTIVE) and has a non-empty email.
    When the manager is LOCKED (inactive), it must fall through to the QMS Owner pool.

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint
    (see test_escalate_to_manager).
    """
    now = datetime.datetime.now(datetime.UTC)
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
