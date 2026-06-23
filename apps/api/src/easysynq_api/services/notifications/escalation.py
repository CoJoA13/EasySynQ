"""S-notify-4: escalation recipient resolution + the non-swallowing single-event emit the
timer_sweep uses. (The best-effort SAVEPOINT enqueue_task_notifications stays for the engine; here
the sweep OWNS the txn, so a failed emit must roll back the per-task txn — the step is not stamped
and retries next sweep.)"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser
from ...db.models.system_config import SystemConfig
from ...db.models.workflow import Task, WorkflowInstance
from ..workflow.repository import users_with_roles
from .dispatch import _enqueue_one
from .recipients import Recipient
from .subjects import resolve_subject

_QM_ROLE = "QMS Owner"


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
