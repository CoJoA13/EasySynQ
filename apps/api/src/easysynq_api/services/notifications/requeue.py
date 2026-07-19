"""S-cleanup-bundle #6: an admin ops-recovery action — reset this org's FAILED notification-email
rows to PENDING so the outbox drain retries them. Structured-log only (email is advisory; the
/tasks inbox is authoritative) — no audit_event, no WORM touch. Does NOT commit (the route
commits)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._notification_enums import NotificationEmailStatus
from ...db.models.notification import NotificationEmail

logger = logging.getLogger("easysynq.notifications.requeue")


async def requeue_failed(session: AsyncSession, org_id: uuid.UUID, *, actor_id: uuid.UUID) -> int:
    """Reset org's FAILED delivery rows → PENDING (attempts=0 so the drain actually re-sends rather
    than immediately re-failing an already-exhausted row). Returns the number of rows requeued."""
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(NotificationEmail)
            .where(
                NotificationEmail.org_id == org_id,
                NotificationEmail.status == NotificationEmailStatus.FAILED,
            )
            .values(
                status=NotificationEmailStatus.PENDING,
                attempts=0,
                next_attempt_at=None,
                failed_at=None,
                last_error=None,
            )
        ),
    )
    count = result.rowcount
    logger.info(
        "notifications.requeued",
        extra={"count": count, "org_id": str(org_id), "actor_id": str(actor_id)},
    )
    return count
