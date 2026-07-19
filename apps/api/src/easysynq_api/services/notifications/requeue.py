"""S-cleanup-bundle #6: an admin ops-recovery action — reset this org's FAILED notification-email
rows to PENDING so the outbox drain retries them. Structured-log only (email is advisory; the
/tasks inbox is authoritative) — no audit_event, no WORM touch. Does NOT commit, and does NOT log:
the route owns both, so the ``notifications.requeued`` record fires only AFTER a successful commit
(a rolled-back requeue must not leave a false success in the sole record of the action)."""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._notification_enums import NotificationEmailStatus
from ...db.models.notification import NotificationEmail


async def requeue_failed(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Reset org's FAILED delivery rows → PENDING (attempts=0 so the drain actually re-sends rather
    than immediately re-failing an already-exhausted row). Returns the number of rows requeued.

    Caller must have already established the org's email delivery is ON — requeuing while it is off
    would only let the next drain terminally SUPPRESS the rows (drain._still_eligible)."""
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
    return result.rowcount
