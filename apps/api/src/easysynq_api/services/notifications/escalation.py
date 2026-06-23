"""S-notify-4: escalation recipient resolution + the non-swallowing single-event emit the
timer_sweep uses. (The best-effort SAVEPOINT enqueue_task_notifications stays for the engine; here
the sweep OWNS the txn, so a failed emit must roll back the per-task txn — the step is not stamped
and retries next sweep.)

Also contains the timer_sweep orchestrator: process_task_timers (one idempotent per-task txn with
advisory lock) + sweep_task_timers (fresh session per task, mirrors digest.py:sweep_digests)."""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.app_user import AppUser, UserStatus
from ...db.models.audit_event import AuditEvent
from ...db.models.notification import NotificationPreference
from ...db.models.sla_policy import SlaPolicy
from ...db.models.system_config import SystemConfig
from ...db.models.workflow import Task, WorkflowInstance
from ..workflow.repository import users_with_roles
from .constants import EVENT_TASK_DUE_SOON, EVENT_TASK_ESCALATED, EVENT_TASK_OVERDUE
from .dispatch import _enqueue_one
from .recipients import Recipient, _first_name, resolve_recipients
from .subjects import resolve_subject
from .timer import TimerPolicy, TimerStamps, TimerStep, due_steps

_QM_ROLE = "QMS Owner"
_OPEN = ("PENDING", "CLAIMED")
_STAMP_COL: dict[TimerStep, str] = {
    TimerStep.REMIND_1: "remind_1_sent_at",
    TimerStep.REMIND_2: "remind_2_sent_at",
    TimerStep.OVERDUE: "overdue_notified_at",
    TimerStep.ESCALATE_1: "escalated_1_at",
}
# Mirror recipients.py: a deactivated user must never be a notification recipient.
_INACTIVE = {UserStatus.LOCKED, UserStatus.DISABLED, UserStatus.RETIRED}

logger = logging.getLogger(__name__)


async def resolve_escalation_recipients(session: AsyncSession, task: Task) -> list[uuid.UUID]:
    """Return the escalation recipient list for a task.

    Priority:
    1. The assignee's manager (``manager_id``) if set.
    2. All ``QMS Owner`` role-holders in the task's org (fallback).
    3. Empty list if neither exists.
    """
    if task.assignee_user_id is not None:
        assignee = await session.get(AppUser, task.assignee_user_id)
        if assignee is not None and assignee.manager_id is not None:
            return [assignee.manager_id]
    # No manager (or pool-only task) → QM fallback (org-scoped).
    return await users_with_roles(session, task.org_id, [_QM_ROLE])


async def _recipient_for_user(session: AsyncSession, user_id: uuid.UUID) -> Recipient | None:
    """Build a Recipient from an AppUser id, mirroring recipients.py's construction.

    Returns None if the user is inactive, has no email, or doesn't exist — skip silently.
    """
    user = await session.get(AppUser, user_id)
    if user is None or user.status in _INACTIVE or not user.email:
        return None
    pref = await session.get(NotificationPreference, user_id)
    email_enabled = pref.email_enabled if pref is not None else True  # absence ⇒ enabled
    return Recipient(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name or "",
        first_name=_first_name(user.display_name),
        email_enabled=email_enabled,
    )


async def emit_task_event(
    session: AsyncSession,
    *,
    instance: WorkflowInstance,
    task: Task,
    recipient: Recipient,
    event_key: str,
    now: datetime.datetime,
) -> None:
    """Emit a single notification event for one recipient + task.

    NON-swallowing: unlike ``enqueue_task_notifications`` (which wraps in a SAVEPOINT and
    swallows failures), this function propagates any exception so the caller's per-task txn
    rolls back and the step is NOT stamped — it will retry on the next sweep.
    """
    subject = await resolve_subject(session, instance.subject_type.value, instance.subject_id)
    cfg = await session.get(SystemConfig, instance.org_id)
    await _enqueue_one(
        session,
        instance=instance,
        task=task,
        subject=subject,
        recipient=recipient,
        due_at=task.due_at,
        org_enabled=bool(cfg and cfg.notifications_email_enabled),
        org_pierce=bool(cfg and cfg.notifications_escalation_pierce_quiet_hours),
        now=now,
        event_key=event_key,
    )


async def _due_task_ids(session: AsyncSession, now: datetime.datetime) -> list[uuid.UUID]:
    """Fetch ids of open tasks with an active SLA policy and at least one unstamped timer step."""
    rows = (
        (
            await session.execute(
                select(Task.id)
                .join(
                    SlaPolicy,
                    (SlaPolicy.org_id == Task.org_id) & (SlaPolicy.task_type == Task.type),
                )
                .where(
                    Task.state.in_(_OPEN),
                    Task.due_at.is_not(None),
                    SlaPolicy.active.is_(True),
                    (
                        Task.remind_1_sent_at.is_(None)
                        | Task.remind_2_sent_at.is_(None)
                        | Task.overdue_notified_at.is_(None)
                        | Task.escalated_1_at.is_(None)
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def process_task_timers(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    now: datetime.datetime,
) -> int:
    """One idempotent txn for one task: advisory-lock → claim FOR UPDATE SKIP LOCKED → fire each
    pending step (notify + stamp + audit-on-escalate) → commit.

    Returns the number of steps fired.

    Mirrors digest.py:bundle_user_digest for the lock/claim/stamp-in-txn safety pattern:
    - The pg_advisory_xact_lock serialises concurrent sweeps for this task.
    - The second concurrent caller blocks until the first commits (stamps), then finds the row
      already stamped and no-ops → exactly-once escalation.
    - populate_existing=True on the locked load avoids the S-drift-1 stale-attr trap.
    """
    # Serialize concurrent sweeps for THIS task (the digest.py advisory-lock pattern).
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(str(task_id)))))

    # Claim with FOR UPDATE SKIP LOCKED + populate_existing to avoid stale-cache (S-drift-1).
    task = (
        await session.execute(
            select(Task)
            .where(Task.id == task_id, Task.state.in_(_OPEN), Task.due_at.is_not(None))
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if task is None or task.due_at is None:
        return 0

    policy = (
        await session.execute(
            select(SlaPolicy).where(
                SlaPolicy.org_id == task.org_id,
                SlaPolicy.task_type == task.type,
                SlaPolicy.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if policy is None:
        return 0

    instance = await session.get(WorkflowInstance, task.instance_id)
    if instance is None:
        return 0

    tpolicy = TimerPolicy(
        remind_1_before=policy.remind_1_before,
        remind_2_before=policy.remind_2_before,
        escalate_1_after=policy.escalate_1_after,
    )
    stamps = TimerStamps(
        remind_1_sent_at=task.remind_1_sent_at,
        remind_2_sent_at=task.remind_2_sent_at,
        overdue_notified_at=task.overdue_notified_at,
        escalated_1_at=task.escalated_1_at,
    )

    fired = 0
    for step in due_steps(tpolicy, task.due_at, stamps, now):
        if step in (TimerStep.REMIND_1, TimerStep.REMIND_2):
            event_key = EVENT_TASK_DUE_SOON
            for r in await resolve_recipients(session, task):
                await emit_task_event(
                    session, instance=instance, task=task, recipient=r, event_key=event_key, now=now
                )
        elif step is TimerStep.OVERDUE:
            for r in await resolve_recipients(session, task):
                await emit_task_event(
                    session,
                    instance=instance,
                    task=task,
                    recipient=r,
                    event_key=EVENT_TASK_OVERDUE,
                    now=now,
                )
        else:  # TimerStep.ESCALATE_1
            recipient_ids = await resolve_escalation_recipients(session, task)
            via = "manager" if (task.assignee_user_id and recipient_ids) else "qm_fallback"
            for uid in recipient_ids:
                r_maybe = await _recipient_for_user(session, uid)
                if r_maybe is not None:
                    await emit_task_event(
                        session,
                        instance=instance,
                        task=task,
                        recipient=r_maybe,
                        event_key=EVENT_TASK_ESCALATED,
                        now=now,
                    )
            # Write one TASK_ESCALATED AuditEvent (system actor, workflow_instance keyed).
            session.add(
                AuditEvent(
                    org_id=task.org_id,
                    occurred_at=now,
                    actor_id=None,
                    actor_type=ActorType.system,
                    event_type=EventType.TASK_ESCALATED,
                    object_type=AuditObjectType.workflow_instance,
                    object_id=instance.id,
                    scope_ref=str(task.id),
                    after={
                        "task_id": str(task.id),
                        "escalated_to": [str(u) for u in recipient_ids],
                        "via": via,
                        "due_at": task.due_at.isoformat(),
                    },
                )
            )

        # Stamp in the SAME txn as the enqueue/audit — this is the idempotency key.
        # A non-null stamp means "already fired"; due_steps gates on it before firing.
        setattr(task, _STAMP_COL[step], now)
        fired += 1

    await session.commit()
    return fired


async def sweep_task_timers(
    sessionmaker: async_sessionmaker[AsyncSession],
    now: datetime.datetime,
) -> dict[str, int]:
    """Fire all pending timer steps for every open task with an active SLA policy.

    Fresh session per task (the MissingGreenlet guard from engineering-patterns).
    Per-task exception isolation: one task's failure must not wedge the cohort.
    Mirrors digest.py:sweep_digests verbatim for the lock/fresh-session pattern.
    """
    counts: dict[str, int] = {"tasks": 0, "steps": 0}
    async with sessionmaker() as session:
        ids = await _due_task_ids(session, now)
    for task_id in ids:
        try:
            async with sessionmaker() as session:
                steps = await process_task_timers(session, task_id=task_id, now=now)
        except Exception:  # noqa: BLE001 — one task's failure must not wedge the sweep
            logger.warning(
                "notifications.timer_task_failed",
                exc_info=True,
                extra={"task_id": str(task_id)},
            )
            continue
        counts["tasks"] += 1
        counts["steps"] += steps
    return counts
