"""The outbox drain core (spec §4). Claims PENDING due rows FOR UPDATE SKIP LOCKED; COUNTS the
attempt + sets a backoff lease BEFORE the SMTP send (so a post-send crash can't loop forever); on
exhaustion → FAILED + a system.email_delivery_failed in-app notification to admins (R32)."""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db.models._notification_enums import NotificationEmailStatus
from ...db.models.app_user import AppUser, UserStatus
from ...db.models.notification import Notification, NotificationEmail, NotificationPreference
from ...db.models.system_config import SystemConfig
from .admins import admin_user_ids
from .constants import EVENT_EMAIL_DELIVERY_FAILED
from .dispatch import emit_system_notification
from .mail import MailMessage, MailSender

logger = logging.getLogger("easysynq.notifications.drain")

# Mirror auth/dependencies.py: a notification/email must never reach a deactivated account.
_INACTIVE = {UserStatus.LOCKED, UserStatus.DISABLED, UserStatus.RETIRED}


def _backoff(attempts: int, base: int) -> datetime.timedelta:
    return datetime.timedelta(seconds=base * (2 ** max(0, attempts - 1)))


async def _still_eligible(
    session: AsyncSession, row: NotificationEmail, settings: Settings
) -> bool:
    """Re-evaluate at send time (spec §4): an admin may have disabled the org flag, or the user
    may have opted out / been deactivated, between enqueue and drain. Any of those → suppress.
    Also folds in the unconfigured-SMTP short-circuit (no deliverable transport)."""
    if not settings.smtp_host:
        return False
    note = await session.get(Notification, row.notification_id)
    if note is None:
        return False
    cfg = await session.get(SystemConfig, note.org_id)
    if not (cfg and cfg.notifications_email_enabled):
        return False
    user = await session.get(AppUser, note.recipient_user_id)
    if user is None or user.status in _INACTIVE:
        return False
    pref = await session.get(NotificationPreference, note.recipient_user_id)
    if pref is not None and not pref.email_enabled:  # absence ⇒ enabled (spec §3.4)
        return False
    return True


async def drain_once(
    session: AsyncSession,
    sender: MailSender,
    settings: Settings,
    *,
    now: datetime.datetime,
    limit: int = 50,
) -> dict[str, int]:
    counts: dict[str, int] = {"sent": 0, "failed": 0, "suppressed": 0, "retried": 0}

    # Claim ONE eligible row per iteration: a per-row commit releases the FOR UPDATE lock on ALL
    # still-unprocessed siblings in a batch, so an overlapping drain could claim+send a sibling this
    # drain also held → double-send. A single-row claim holds the lock on exactly that row until its
    # lease commits. Bound the loop to at most `limit` iterations.
    for _ in range(limit):
        row = (
            await session.execute(
                select(NotificationEmail)
                .where(
                    NotificationEmail.status == NotificationEmailStatus.PENDING,
                    (NotificationEmail.next_attempt_at.is_(None))
                    | (NotificationEmail.next_attempt_at <= now),
                )
                .order_by(NotificationEmail.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            break

        # Re-check eligibility at send time → suppress if no longer eligible (org-off / no-smtp /
        # user-inactive / user-opted-out). No attempt increment, no send, no failure emit.
        if not await _still_eligible(session, row, settings):
            row.status = NotificationEmailStatus.SUPPRESSED
            await session.commit()
            counts["suppressed"] += 1
            continue

        # Exhausted? → make FAILED durable FIRST, then emit best-effort (adjustment #1 hardening).
        if row.attempts >= settings.notification_max_send_attempts:
            row.status = NotificationEmailStatus.FAILED
            row.failed_at = now
            await session.commit()
            try:
                await _emit_failure(session, row)
                await session.commit()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "notification.emit_failure_failed",
                    exc_info=True,
                    extra={"id": str(row.id)},
                )
                await session.rollback()
            counts["failed"] += 1
            continue

        # COUNT-BEFORE-SEND lease: increment + push next_attempt_at out, COMMIT (releases the lock;
        # a concurrent drain in the send window skips on next_attempt_at), THEN send.
        row.attempts += 1
        row.next_attempt_at = now + _backoff(row.attempts, settings.notification_retry_base_seconds)
        await session.commit()

        try:
            await sender.send(
                MailMessage(to=row.recipient_email, subject=row.subject, body=row.body)
            )
        except Exception as exc:  # noqa: BLE001 — transient; leave PENDING, lease retries after backoff
            row.last_error = str(exc)[:1000]
            await session.commit()
            counts["retried"] += 1
            logger.warning("notification.email_send_failed", extra={"id": str(row.id)})
            continue

        row.status = NotificationEmailStatus.SENT
        row.sent_at = now
        await session.commit()
        counts["sent"] += 1

    return counts


async def _emit_failure(session: AsyncSession, row: NotificationEmail) -> None:
    note = await session.get(Notification, row.notification_id)
    if note is None:
        return
    admins = await admin_user_ids(session, row.org_id)
    if not admins:
        return
    await emit_system_notification(
        session,
        org_id=row.org_id,
        recipient_user_ids=admins,
        event_key=EVENT_EMAIL_DELIVERY_FAILED,
        context={
            "recipient_email": row.recipient_email,
            "attempts": row.attempts,
            "last_error": row.last_error or "(unknown)",
            "notification_id": str(row.notification_id),
            "created_at": row.created_at.isoformat() if row.created_at else "",
        },
    )
