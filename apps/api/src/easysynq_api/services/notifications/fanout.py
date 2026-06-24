"""Awareness fan-out Beat (slice S-notify-5a, doc 10 §9.2).

Claims pending awareness_event rows, resolves the read-scoped audience (the per-user PDP loop), and
creates per-recipient notification rows reusing the slice-3a machinery — idempotently. Mirrors the
escalation/digest claim+stamp shape but with PK-pinned FOR UPDATE SKIP LOCKED and NO per-event
advisory lock (the outbox_drain precedent). One commit per event → atomic claim+fanout+stamp; a
worker death rolls the whole txn back (fanned_out_at stays NULL → re-claimed). No reaper needed
(fully machine-driven, terminal-on-stamp).
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...db.models.awareness_event import AwarenessEvent
from ...db.models.system_config import SystemConfig
from ..authz.audience import resolve_document_readers
from .dispatch import enqueue_awareness_one
from .escalation import _recipient_for_user
from .render import render
from .subjects import resolve_subject

logger = logging.getLogger("easysynq.notifications.fanout")

_CLAIM_LIMIT = 200  # bound a release-burst fan-out per sweep (spec §8)


async def _pending_event_ids(session: AsyncSession, now: datetime.datetime) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(AwarenessEvent.id)
                .where(AwarenessEvent.fanned_out_at.is_(None))
                .order_by(AwarenessEvent.occurred_at)
                .limit(_CLAIM_LIMIT)
            )
        )
        .scalars()
        .all()
    )


async def _org_flags(session: AsyncSession, org_id: uuid.UUID) -> tuple[bool, bool]:
    cfg = (
        await session.execute(select(SystemConfig).where(SystemConfig.org_id == org_id))
    ).scalar_one_or_none()
    if cfg is None:
        return (False, False)
    return (cfg.notifications_email_enabled, cfg.notifications_escalation_pierce_quiet_hours)


async def process_one_awareness_event(
    session: AsyncSession, *, event_id: uuid.UUID, now: datetime.datetime
) -> int:
    """Fan out ONE awareness event. Returns the count of newly-created in-app rows. Idempotent:
    claims FOR UPDATE SKIP LOCKED + populate_existing; stamps fanned_out_at + commits ONCE. A
    template miss does NOT stamp (retry after restore — the 3a/4 rule)."""
    event = (
        await session.execute(
            select(AwarenessEvent)
            .where(AwarenessEvent.id == event_id, AwarenessEvent.fanned_out_at.is_(None))
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if event is None:
        return 0  # already claimed/stamped by a concurrent sweep, or vanished

    # Capture scalar event attributes into locals BEFORE any potential early-return so we
    # never access an ORM instance after the session context has closed or expired it.
    # (The S-ing-4 MissingGreenlet trap: accessing an expired ORM attr on an async session
    # triggers a synchronous lazy-refresh → MissingGreenlet at pool teardown.)
    event_key = event.event_key
    event_org_id = event.org_id
    event_subject_type = event.subject_type
    event_subject_id = event.subject_id
    event_subject_version_id = event.subject_version_id
    event_actor_user_id = event.actor_user_id
    event_context = event.context

    subject = await resolve_subject(session, event_subject_type, event_subject_id)

    audience = await resolve_document_readers(session, event_org_id, event_subject_id, now=now)
    recipients = [uid for uid in audience if uid != event_actor_user_id]

    # Template-existence probe ONCE (recipient-independent). Missing → do NOT stamp (retry).
    # No rollback here: only READs have happened so far (resolve_subject,
    # resolve_document_readers, render probe) plus the FOR UPDATE claim lock, which releases
    # when the session context exits without commit — fanned_out_at stays NULL → re-claimed.
    # Rolling back after loading the ORM instance would expire it and cause a synchronous
    # lazy-refresh on the subsequent attr accesses → MissingGreenlet (S-ing-4 trap).
    if recipients and (await render(session, event_key, {})) is None:
        logger.warning("notifications.awareness_template_missing", extra={"event_key": event_key})
        return 0

    org_enabled, org_pierce = await _org_flags(session, event_org_id)
    context_vars = dict(event_context or {})
    created = 0
    for uid in recipients:
        recipient = await _recipient_for_user(session, uid, org_id=event_org_id)
        if recipient is None:
            continue
        outcome = await enqueue_awareness_one(
            session,
            org_id=event_org_id,
            subject=subject,
            subject_id=event_subject_id,
            subject_version_id=event_subject_version_id,
            recipient=recipient,
            event_key=event_key,
            context_vars=context_vars,
            now=now,
            org_enabled=org_enabled,
            org_pierce=org_pierce,
        )
        if outcome == "no_template":
            # The template was deactivated/deleted between the probe and now (TOCTOU). Do NOT
            # stamp — return without committing so the partial inserts roll back on session close
            # and the event is re-claimed next sweep (retry-after-restore; the dedup index keeps
            # the eventual full re-fan-out exactly-once). (Codex P2)
            logger.warning(
                "notifications.awareness_template_missing", extra={"event_key": event_key}
            )
            return 0
        if outcome == "created":
            created += 1

    event.fanned_out_at = now
    await session.commit()
    return created


async def fan_out_awareness(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime.datetime
) -> dict[str, int]:
    """Fan out every pending awareness event. Fresh session per event (the MissingGreenlet guard);
    per-event exception isolation (one event's failure must not wedge the cohort)."""
    counts: dict[str, int] = {"events": 0, "notifications": 0}
    async with sessionmaker() as session:
        ids = await _pending_event_ids(session, now)
    for event_id in ids:
        try:
            async with sessionmaker() as session:
                n = await process_one_awareness_event(session, event_id=event_id, now=now)
        except Exception:  # noqa: BLE001 — one event's failure must not wedge the sweep
            logger.warning(
                "notifications.awareness_event_failed",
                exc_info=True,
                extra={"event_id": str(event_id)},
            )
            continue
        counts["events"] += 1
        counts["notifications"] += n
    return counts
