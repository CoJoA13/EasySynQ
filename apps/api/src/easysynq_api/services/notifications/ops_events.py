"""In-DB operator alarms: ``system.backup_failed`` + ``integrity.alarm`` (Batch 11, review
2026-07-22 finding 2).

Both keys have been class-mapped since S-notify-3a (``classes.py``) but had no emitter, so a nightly
backup that failed every night and a chain-verify break both produced nothing but a worker log line.
This module is the missing emitter for the DB-UP modes; ``ops_channel.py`` is the DB-DOWN arm, and
the callers fire both.

Recipients are the org's System Administrators (``admins.py``). There is no floor role above them:
if an org has none, or all are inactive, the in-DB path legitimately reaches nobody — which is
exactly why the out-of-band channel is not optional-in-spirit. That case is logged loudly rather
than silently counted as delivered.

Delivery reuses the normal machinery (``resolve_delivery`` → per-class digest mode, quiet hours, the
CRITICAL pierce, the org email flag and the per-user opt-out), so an operator alarm honours the same
preferences as every other notification. It is NOT ``dispatch.emit_system_notification``: that
helper is in-app-only by design (emailing an admin about a failed email is circular), whereas
doc 10 §9.2 lists both of these events as default-email ✓.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.notification import Notification, NotificationEmail
from ...db.models.system_config import SystemConfig
from .admins import admin_user_ids
from .constants import EVENT_BACKUP_FAILED, EVENT_INTEGRITY_ALARM, SUBJECT_SYSTEM
from .dispatch import resolve_delivery, variables_as_json
from .recipients import recipient_for_user
from .render import render
from .subjects import prefs_link

logger = logging.getLogger("easysynq.notifications.ops_events")

# ``integrity.alarm`` discriminator — which detector fired. One template, many detectors: the
# recipient sees the same "audit integrity alarm" framing with the specific check named.
CHECK_CHAIN_VERIFY = "chain_verify"  # the in-DB walk or the signed checkpoint failed
CHECK_OFFHOST_WITNESS = "offhost_witness"  # the INDEPENDENT off-host read-back failed
CHECK_VERIFY_KEY = "verify_key"  # no verify key → signature + off-host attestation DISABLED
CHECK_WITNESS_REQUIRED = "witness_required"  # a declared-required witness is absent from the DB


async def _emit_admin_alert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_key: str,
    context: dict[str, object],
    now: datetime.datetime,
) -> int:
    """Insert one in-app notification per System Administrator (+ an outbox email row when their
    resolved delivery is immediate). Returns the count of in-app rows created.

    Best-effort, wrapped in a SAVEPOINT (the ``enqueue_task_notifications`` posture): any failure
    rolls back ONLY these rows and logs. That matters most for the audit caller, whose session also
    carries the ``CHAIN_VERIFY_FAIL`` audit row — the durable in-DB alarm must survive a broken
    notification, never be rolled back with it. ``begin_nested`` flushes the caller's pending rows
    before opening the savepoint, so the audit INSERT is already outside it.
    """
    try:
        async with session.begin_nested():
            admin_ids = await admin_user_ids(session, org_id)
            if not admin_ids:
                logger.warning(
                    "notifications.ops_event_no_admin",
                    extra={
                        "extra_fields": {
                            "event_key": event_key,
                            "org_id": str(org_id),
                            "detail": "no System Administrator in this org — the out-of-band "
                            "operator channel is the only carrier for this alarm",
                        }
                    },
                )
                return 0

            cfg = await session.get(SystemConfig, org_id)
            org_enabled = bool(cfg and cfg.notifications_email_enabled)
            org_pierce = bool(cfg and cfg.notifications_escalation_pierce_quiet_hours)

            # Probe the template ONCE, before any row is written (the fanout.py precedent). A
            # missing template is a deployment problem, not a per-recipient one — probing inside
            # the loop would either log once per admin or return a count that contradicts the rows
            # already inserted.
            if await render(session, event_key, {}) is None:
                logger.warning("notification.template_missing", extra={"event_key": event_key})
                return 0

            created = 0
            attempted = 0
            for uid in admin_ids:
                recipient = await recipient_for_user(session, uid, org_id=org_id)
                if recipient is None:  # inactive / cross-org / vanished
                    continue
                attempted += 1
                variables: dict[str, object] = {
                    "recipient.first_name": recipient.first_name,
                    "prefs_link": prefs_link(),
                    **context,
                }
                forms = await render(session, event_key, variables)
                if forms is None:  # pragma: no cover — TOCTOU: deactivated between probe and now
                    logger.warning("notification.template_missing", extra={"event_key": event_key})
                    return created
                plan = await resolve_delivery(
                    session,
                    recipient=recipient,
                    event_key=event_key,
                    org_enabled=org_enabled,
                    org_pierce=org_pierce,
                    now=now,
                )
                note = Notification(
                    org_id=org_id,
                    recipient_user_id=recipient.user_id,
                    event_key=event_key,
                    subject_type=SUBJECT_SYSTEM,
                    subject_id=None,
                    task_id=None,
                    title=forms.in_app_title,
                    body=forms.in_app_body,
                    deep_link="",  # operational-only: never a deep link into the vault
                    template_id=forms.template_id,
                    template_version=forms.template_version,
                    context=variables_as_json(variables),
                    # Carry the digest marker so an admin whose class mode is DAILY still receives
                    # the alarm by email via the digest sweep. Without it, a non-immediate mode
                    # would produce an in-app row and NO email at all — a silent half-delivery.
                    digest_due_at=plan.digest_due_at,
                )
                session.add(note)
                await session.flush()  # need note.id for the email row's FK
                created += 1
                if plan.wants_email and plan.is_immediate:
                    email_addr: str = recipient.email  # type: ignore[assignment]
                    session.add(
                        NotificationEmail(
                            org_id=org_id,
                            notification_id=note.id,
                            recipient_user_id=recipient.user_id,
                            recipient_email=email_addr,
                            subject=forms.email_subject,
                            body=forms.email_body,
                            next_attempt_at=plan.email_next_attempt_at,
                        )
                    )
            if attempted == 0:
                logger.warning(
                    "notifications.ops_event_no_valid_recipient",
                    extra={
                        "extra_fields": {
                            "event_key": event_key,
                            "org_id": str(org_id),
                            "admins": len(admin_ids),
                            "detail": "every System Administrator was inactive or cross-org — the "
                            "out-of-band operator channel is the only carrier for this alarm",
                        }
                    },
                )
            return created
    except Exception:  # noqa: BLE001 — an alarm-notification failure must never lose the audit row
        logger.warning(
            "notifications.ops_event_emit_failed",
            exc_info=True,
            extra={"extra_fields": {"event_key": event_key, "org_id": str(org_id)}},
        )
        return 0


async def emit_backup_failed(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    destination: str,
    error: str,
    now: datetime.datetime,
) -> int:
    """``system.backup_failed`` → the org's System Administrators. Operational metadata only."""
    return await _emit_admin_alert(
        session,
        org_id=org_id,
        event_key=EVENT_BACKUP_FAILED,
        context={
            "destination": destination,
            "error": error,
            "failed_at": now.isoformat(),
        },
        now=now,
    )


async def emit_integrity_alarm(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    check: str,
    reasons: list[str],
    break_count: int,
    now: datetime.datetime,
) -> int:
    """``integrity.alarm`` → the org's System Administrators. CRITICAL class, so it is immediate by
    default and pierces quiet hours when the org flag allows (doc 10 §9.4)."""
    return await _emit_admin_alert(
        session,
        org_id=org_id,
        event_key=EVENT_INTEGRITY_ALARM,
        context={
            "check": check,
            "reason_summary": "; ".join(reasons) if reasons else "(no detail reported)",
            "break_count": break_count,
            "detected_at": now.isoformat(),
        },
        now=now,
    )
