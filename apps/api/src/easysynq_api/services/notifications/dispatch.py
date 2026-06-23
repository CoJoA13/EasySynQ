"""Notification enqueue (spec §4 — the transactional outbox). The whole enqueue is wrapped in a
SAVEPOINT inside the caller's task-creation txn: atomic-on-success, and ANY failure rolls back only
the savepoint + logs (the parent task txn is untouched → a notification bug never blocks a workflow
transition). The async drain (tasks/notifications.py) does the actual send."""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._notification_enums import NotificationDigestMode
from ...db.models.notification import Notification, NotificationEmail, NotificationPreference
from ...db.models.system_config import SystemConfig
from ...db.models.workflow import Task, WorkflowInstance
from .classes import class_of
from .constants import EVENT_TASK_ASSIGNED, SUBJECT_SYSTEM
from .preferences import effective_preferences
from .quiet import in_quiet_window, should_pierce, window_end
from .recipients import Recipient, resolve_recipients
from .render import render
from .schedule import next_digest_at
from .subjects import SubjectInfo, prefs_link, resolve_subject

# 3-state outcome for _enqueue_one / emit_task_event:
#   "created"     - a new Notification row was inserted this call
#   "deduped"     - the row already existed (ON CONFLICT DO NOTHING); no second email created
#   "no_template" - render() returned None; the step must NOT be stamped (retry after restore)
EnqueueOutcome = Literal["created", "deduped", "no_template"]

logger = logging.getLogger("easysynq.notifications.dispatch")


def _email_eligible(*, org_enabled: bool, email: str | None, user_opt_in: bool) -> bool:
    return bool(org_enabled and email and user_opt_in)


async def enqueue_task_notifications(
    session: AsyncSession,
    instance: WorkflowInstance,
    tasks: list[Task],
    *,
    due_at_override: datetime.datetime | None = None,
    now: datetime.datetime | None = None,
) -> None:
    """Enqueue a task.assigned notification per recipient per task.

    Never raises (best-effort, spec §4). Any failure rolls back only the SAVEPOINT and
    logs a warning; the parent task-creation txn is untouched.
    """
    try:
        async with session.begin_nested():
            _now = now or datetime.datetime.now(datetime.UTC)
            subject = await resolve_subject(
                session, instance.subject_type.value, instance.subject_id
            )
            cfg = await session.get(SystemConfig, instance.org_id)
            org_enabled = bool(cfg and cfg.notifications_email_enabled)
            org_pierce = bool(cfg and cfg.notifications_escalation_pierce_quiet_hours)
            for task in tasks:
                recipients = await resolve_recipients(session, task)
                due_at = due_at_override if due_at_override is not None else task.due_at
                for r in recipients:
                    await _enqueue_one(
                        session,
                        instance=instance,
                        task=task,
                        subject=subject,
                        recipient=r,
                        due_at=due_at,
                        org_enabled=org_enabled,
                        org_pierce=org_pierce,
                        now=_now,
                        event_key=EVENT_TASK_ASSIGNED,
                    )
    except Exception:  # noqa: BLE001 — best-effort: notification bug must never block transition
        logger.warning("notification.enqueue_failed", exc_info=True)


async def _enqueue_one(
    session: AsyncSession,
    *,
    instance: WorkflowInstance,
    task: Task,
    subject: SubjectInfo,
    recipient: Recipient,
    due_at: datetime.datetime | None,
    org_enabled: bool,
    org_pierce: bool,
    now: datetime.datetime,
    event_key: str,
) -> EnqueueOutcome:
    """Return the 3-state outcome for this recipient:

    - ``"created"``     - a new Notification row was inserted this call.
    - ``"deduped"``     - the row already existed (ON CONFLICT DO NOTHING); no second email.
    - ``"no_template"`` - render() returned None; step must NOT be stamped (retry after restore).

    Callers MUST stamp timer steps when the outcome is ``"created"`` or ``"deduped"`` (the
    notification was emitted or already existed) and must NOT stamp on ``"no_template"``
    (template miss - retry after restore).
    """
    variables: dict[str, object] = {
        "recipient.first_name": recipient.first_name,
        "subject.identifier": subject.identifier,
        "subject.title": subject.title,
        "subject.kind": subject.kind,
        "task.action_expected": task.action_expected or "Action required on",
        "task.due_at": due_at,
        "deep_link": subject.deep_link,
        "prefs_link": prefs_link(),
    }
    forms = await render(session, event_key, variables)
    if forms is None:
        logger.warning("notification.template_missing", extra={"event_key": event_key})
        return "no_template"

    # Resolve per-recipient digest class and mode.
    pref = await session.get(NotificationPreference, recipient.user_id)
    eff = effective_preferences(pref)
    klass = class_of(event_key)
    mode = eff.modes[klass]

    base_eligible = _email_eligible(
        org_enabled=org_enabled,
        email=recipient.email,
        user_opt_in=eff.email_enabled,
    )
    wants_email = base_eligible and mode is not NotificationDigestMode.OFF
    is_daily = wants_email and mode is NotificationDigestMode.DAILY
    digest_due_at = next_digest_at(eff, now) if is_daily else None

    # Insert the in-app row; ON CONFLICT DO NOTHING + RETURNING → a dup is a no-op (spec §3.1).
    stmt = (
        pg_insert(Notification)
        .values(
            org_id=instance.org_id,
            recipient_user_id=recipient.user_id,
            event_key=event_key,
            subject_type=subject.kind,
            subject_id=instance.subject_id,
            task_id=task.id,
            title=forms.in_app_title,
            body=forms.in_app_body,
            deep_link=subject.deep_link,
            template_id=forms.template_id,
            template_version=forms.template_version,
            context=variables_as_json(variables),
            digest_due_at=digest_due_at,
        )
        # The dedup index is a PARTIAL unique index (WHERE task_id IS NOT NULL); PostgreSQL
        # requires the index_where predicate on ON CONFLICT when targeting a partial index.
        .on_conflict_do_nothing(
            index_elements=["recipient_user_id", "task_id", "event_key"],
            index_where=sa.text("task_id IS NOT NULL"),
        )
        .returning(Notification.id)
    )
    new_id = (await session.execute(stmt)).scalar_one_or_none()
    if new_id is None:
        # Dedup hit: the notification row already exists (ON CONFLICT DO NOTHING).
        # The notification WAS emitted (created in a prior sweep or a downgrade→re-upgrade cycle).
        # Return "deduped" so the caller stamps the timer step as already-emitted — not as a
        # delivery miss.  Email creation is correctly skipped (no orphan: spec §4 / refute L2-3).
        # Only a template miss (checked above, before the insert) returns "no_template".
        return "deduped"

    if wants_email and mode is NotificationDigestMode.IMMEDIATE:
        # _email_eligible guarantees email is truthy here; cast for mypy.
        email_addr: str = recipient.email  # type: ignore[assignment]
        next_attempt_at: datetime.datetime | None = None
        if in_quiet_window(eff, now) and not should_pierce(klass, org_pierce):
            next_attempt_at = window_end(eff, now)
        session.add(
            NotificationEmail(
                org_id=instance.org_id,
                notification_id=new_id,
                recipient_user_id=recipient.user_id,
                recipient_email=email_addr,
                subject=forms.email_subject,
                body=forms.email_body,
                next_attempt_at=next_attempt_at,
            )
        )
    return "created"


def variables_as_json(variables: dict[str, object]) -> dict[str, object]:
    """JSON-safe copy of the variable bag (datetimes → isoformat) for the context column."""
    out: dict[str, object] = {}
    for k, v in variables.items():
        out[k] = v.isoformat() if isinstance(v, datetime.datetime) else v
    return out


async def emit_system_notification(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    recipient_user_ids: list[uuid.UUID],
    event_key: str,
    context: dict[str, object],
) -> None:
    """In-app-only system event (e.g. system.email_delivery_failed → admins).

    No email row, no subject metadata (operational-only template, spec §5/§6).
    Deduped by the caller, not a constraint.
    """
    forms = await render(session, event_key, context)
    if forms is None:
        logger.warning("notification.template_missing", extra={"event_key": event_key})
        return
    for uid in recipient_user_ids:
        session.add(
            Notification(
                org_id=org_id,
                recipient_user_id=uid,
                event_key=event_key,
                subject_type=SUBJECT_SYSTEM,
                subject_id=None,
                task_id=None,
                title=forms.in_app_title,
                body=forms.in_app_body,
                deep_link=str(context.get("deep_link") or ""),
                template_id=forms.template_id,
                template_version=forms.template_version,
                context=context,
            )
        )
