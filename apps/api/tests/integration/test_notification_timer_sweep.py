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
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, select

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
from easysynq_api.services.notifications.escalation import (
    emit_task_event,
    process_task_timers,
    sweep_task_timers,
)
from easysynq_api.services.notifications.recipients import Recipient

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

    resolve_escalation_recipients accepts the manager only when active AND in the same org.
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


async def test_no_email_manager_gets_inapp_escalation(app_under_test: Any) -> None:
    """R2-3: an active manager with email=None still receives the in-app task.escalated row.

    _recipient_for_user no longer drops a user for having a NULL email — only email-row
    creation depends on an address.  The in-app notification row must be created regardless.

    NOTE: ``now`` is datetime.now(UTC) — AuditEvent partition constraint.
    """
    now = datetime.datetime.now(datetime.UTC)
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
    now = datetime.datetime.now(datetime.UTC)
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
    now = datetime.datetime.now(datetime.UTC)
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
    now = datetime.datetime.now(datetime.UTC)
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
    now = datetime.datetime.now(datetime.UTC)
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
    """R3-2 regression guard: a genuine template miss (attempted>0, notified=False) must NOT stamp.

    Uses a synthetic event_key that has no effective template in the DB so _enqueue_one
    returns False.  Calls emit_task_event directly (the non-swallowing helper) and verifies
    the return value, then confirms the task's escalated_1_at is still NULL.

    This guards against regressing R2-2: a delivery miss must stay retryable.
    """
    now = datetime.datetime.now(datetime.UTC)
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

    assert result is False, f"emit_task_event must return False on a template miss, got {result!r}"

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
