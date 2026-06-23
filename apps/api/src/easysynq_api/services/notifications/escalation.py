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
from .dispatch import EnqueueOutcome, _enqueue_one
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
    1. The assignee's manager (``manager_id``) if set, active, and in the same org as the task.
       A no-email manager is still a valid in-app escalation target (only email-row creation
       depends on an address — mirror _recipient_for_user / resolve_recipients behaviour).
       An inactive or cross-org manager falls through to the QMS Owner fallback.
    2. All ``QMS Owner`` role-holders in the task's org (fallback).
    3. Empty list if neither exists (stamp anyway — no valid recipient).
    """
    if task.assignee_user_id is not None:
        assignee = await session.get(AppUser, task.assignee_user_id)
        if assignee is not None and assignee.manager_id is not None:
            # Accept the manager when: exists + active + same org as task.
            # Email is NOT required here — a no-email manager still gets the in-app row.
            # Cross-org manager FK is possible (manager_id is a self-FK with no org constraint);
            # an out-of-org manager leaks task metadata → fall through to the QM fallback.
            manager = await session.get(AppUser, assignee.manager_id)
            if (
                manager is not None
                and manager.status not in _INACTIVE
                and manager.org_id == task.org_id
                and assignee.manager_id != assignee.id  # R3-1: reject self-manager
            ):
                return [assignee.manager_id]
            # Manager is inactive, cross-org, or self-manager → fall through to QM fallback.
    # No manager (or pool-only task, or inactive/cross-org manager) → QM fallback (org-scoped).
    return await users_with_roles(session, task.org_id, [_QM_ROLE])


async def _recipient_for_user(
    session: AsyncSession, user_id: uuid.UUID, *, org_id: uuid.UUID
) -> Recipient | None:
    """Build a Recipient from an AppUser id, mirroring recipients.py's construction.

    Returns None if the user doesn't exist, is inactive, or belongs to a different org.
    A NULL email is allowed: the in-app notification row is always created; only the
    downstream email send depends on an address (mirror resolve_recipients' behaviour).
    Cross-org users are silently dropped to prevent task metadata leaking out of the org.
    """
    user = await session.get(AppUser, user_id)
    if user is None or user.status in _INACTIVE or user.org_id != org_id:
        return None
    pref = await session.get(NotificationPreference, user_id)
    email_enabled = pref.email_enabled if pref is not None else True  # absence ⇒ enabled
    return Recipient(
        user_id=user.id,
        email=user.email,  # may be None — only email-row creation depends on an address
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
) -> EnqueueOutcome:
    """Emit a single notification event for one recipient + task.

    Returns the 3-state outcome from ``_enqueue_one``:
    - ``"created"``     - a new Notification row was inserted this call.
    - ``"deduped"``     - the row already existed; the step is considered already-emitted.
    - ``"no_template"`` - template miss; the step must NOT be stamped (retry after restore).

    NON-swallowing: unlike ``enqueue_task_notifications`` (which wraps in a SAVEPOINT and
    swallows failures), this function propagates any exception so the caller's per-task txn
    rolls back and the step is NOT stamped — it will retry on the next sweep.

    The caller MUST stamp the timer step when at least one call returns ``"created"`` or
    ``"deduped"`` (notification exists — emitted now or in a prior cycle).  ``"no_template"``
    must not be stamped, so the step retries after the template is restored.
    """
    subject = await resolve_subject(session, instance.subject_type.value, instance.subject_id)
    cfg = await session.get(SystemConfig, instance.org_id)
    return await _enqueue_one(
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
            recipients = await resolve_recipients(session, task)
            attempted = len(recipients)
            # "exists" = at least one notification row exists (created OR deduped) after the call.
            exists = False
            for r in recipients:
                outcome = await emit_task_event(
                    session, instance=instance, task=task, recipient=r, event_key=event_key, now=now
                )
                if outcome in ("created", "deduped"):
                    exists = True
        elif step is TimerStep.OVERDUE:
            recipients = await resolve_recipients(session, task)
            attempted = len(recipients)
            exists = False
            for r in recipients:
                outcome = await emit_task_event(
                    session,
                    instance=instance,
                    task=task,
                    recipient=r,
                    event_key=EVENT_TASK_OVERDUE,
                    now=now,
                )
                if outcome in ("created", "deduped"):
                    exists = True
        else:  # TimerStep.ESCALATE_1
            recipient_ids = await resolve_escalation_recipients(session, task)
            # Derive the audit `via` label from the actual recipients returned, not from
            # whether manager_id is set — an inactive/cross-org/self-manager falls through to
            # the QM fallback inside resolve_escalation_recipients, so manager_id being non-NULL
            # does NOT mean the manager was chosen as the recipient.
            # R4-2: mirror R3-1's self-manager guard — when manager_id == assignee.id the
            # recipient list is the QM fallback even though manager_id_for_task is non-NULL.
            assignee = (
                await session.get(AppUser, task.assignee_user_id) if task.assignee_user_id else None
            )
            manager_id_for_task = assignee.manager_id if assignee is not None else None
            assignee_id_for_task = assignee.id if assignee is not None else None
            via = (
                "manager"
                if (
                    manager_id_for_task is not None
                    and manager_id_for_task != assignee_id_for_task  # R4-2: not a self-manager
                    and recipient_ids == [manager_id_for_task]
                )
                else "qm_fallback"
            )
            # created_ids: recipients for whom a NEW notification row was inserted this sweep.
            # exists_ids:  recipients whose notification row exists (created OR deduped).
            # The audit is written only when created_ids is non-empty (R4-1: never on a pure
            # dedup so the WORM audit log has exactly one TASK_ESCALATED per escalation event).
            created_ids: list[uuid.UUID] = []
            exists_ids: list[uuid.UUID] = []
            attempted = 0
            for uid in recipient_ids:
                # Pass org_id so cross-org role-holders are filtered here too (R2-4).
                r_maybe = await _recipient_for_user(session, uid, org_id=task.org_id)
                if r_maybe is not None:
                    attempted += 1
                    outcome = await emit_task_event(
                        session,
                        instance=instance,
                        task=task,
                        recipient=r_maybe,
                        event_key=EVENT_TASK_ESCALATED,
                        now=now,
                    )
                    if outcome == "created":
                        created_ids.append(uid)
                        exists_ids.append(uid)
                    elif outcome == "deduped":
                        exists_ids.append(uid)
            exists = bool(exists_ids)
            if created_ids:
                # Write one TASK_ESCALATED AuditEvent only for genuinely-new escalations
                # (R4-1: a pure dedup sweep must not append a second audit row — the original
                # TASK_ESCALATED already exists in the WORM log from the first sweep).
                # escalated_to lists only newly-created recipients (not deduped ones).
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
                            "escalated_to": [str(u) for u in created_ids],
                            "via": via,
                            "due_at": task.due_at.isoformat(),
                        },
                    )
                )

        # Stamp in the SAME txn as the enqueue/audit — this is the idempotency key.
        # A non-null stamp means "already fired"; due_steps gates on it before firing.
        #
        # Three distinct cases:
        #   exists=True             → at least one notification row exists (created or deduped) →
        #                             stamp.
        #   exists=False, attempted=0 → terminal no-op (no valid recipient to attempt,
        #                             e.g. empty pool, all-inactive, no manager+no QM) →
        #                             stamp to avoid the sweep re-processing this task
        #                             on every 5-min cycle forever.
        #   exists=False, attempted>0 → delivery miss (template absent/deactivated) →
        #                             do NOT stamp; retry next sweep after template restored.
        should_stamp = exists or attempted == 0
        if should_stamp:
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
