"""S-notify-5b: a read-only, org-scoped notification delivery-health aggregator (doc 10 §9).

Counts over notification_email by status + the recent-failures list + the awareness fan-out backlog.
The ``pending_now`` predicate mirrors the drain's real claim (drain.py: status=PENDING AND
(next_attempt_at IS NULL OR next_attempt_at <= now())); ``pending_scheduled`` is its complement.
Ages are returned as ISO timestamps — the FE derives the relative label (clock-free +
test-deterministic). Pure reads; no side effects; no WORM touch."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import Settings
from ...db.models._notification_enums import NotificationEmailStatus
from ...db.models.awareness_event import AwarenessEvent
from ...db.models.notification import NotificationEmail
from ...db.models.system_config import SystemConfig
from ..common.serialize import iso
from .delivery import email_delivery_ready

_RECENT_FAILURES_LIMIT = 10


async def get_delivery_health(
    session: AsyncSession, org_id: uuid.UUID, *, settings: Settings
) -> dict[str, Any]:
    st = NotificationEmailStatus
    pending = NotificationEmail.status == st.PENDING
    claimable = pending & (
        NotificationEmail.next_attempt_at.is_(None)
        | (NotificationEmail.next_attempt_at <= func.now())
    )
    scheduled = pending & (NotificationEmail.next_attempt_at > func.now())

    failed, pending_now, pending_scheduled, suppressed, oldest_pending = (
        await session.execute(
            select(
                func.count().filter(NotificationEmail.status == st.FAILED),
                func.count().filter(claimable),
                func.count().filter(scheduled),
                func.count().filter(NotificationEmail.status == st.SUPPRESSED),
                func.min(NotificationEmail.created_at).filter(pending),
            ).where(
                NotificationEmail.org_id == org_id,
                NotificationEmail.status.in_([st.FAILED, st.PENDING, st.SUPPRESSED]),
            )
        )
    ).one()

    failure_rows = (
        await session.execute(
            select(
                NotificationEmail.recipient_email,
                NotificationEmail.last_error,
                NotificationEmail.attempts,
                NotificationEmail.failed_at,
                NotificationEmail.email_kind,
            )
            .where(
                NotificationEmail.org_id == org_id,
                NotificationEmail.status == st.FAILED,
            )
            .order_by(NotificationEmail.failed_at.desc().nullslast())
            .limit(_RECENT_FAILURES_LIMIT)
        )
    ).all()

    aw_pending, aw_oldest = (
        await session.execute(
            select(func.count(), func.min(AwarenessEvent.created_at)).where(
                AwarenessEvent.org_id == org_id,
                AwarenessEvent.fanned_out_at.is_(None),
            )
        )
    ).one()

    cfg = await session.get(SystemConfig, org_id)

    org_email_enabled = bool(cfg and cfg.notifications_email_enabled)
    return {
        "org_email_enabled": org_email_enabled,
        "delivery_ready": email_delivery_ready(
            org_email_enabled=org_email_enabled,
            settings=settings,
        ),
        "email": {
            "failed": failed,
            "pending_now": pending_now,
            "pending_scheduled": pending_scheduled,
            "suppressed": suppressed,
            "oldest_pending_at": iso(oldest_pending),
        },
        "recent_failures": [
            {
                "recipient_email": r.recipient_email,
                "last_error": r.last_error,
                "attempts": r.attempts,
                "failed_at": iso(r.failed_at),
                "email_kind": r.email_kind.value,
            }
            for r in failure_rows
        ],
        "awareness": {
            "pending": aw_pending,
            "oldest_pending_at": iso(aw_oldest),
        },
    }
