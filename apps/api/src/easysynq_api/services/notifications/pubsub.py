# apps/api/src/easysynq_api/services/notifications/pubsub.py
"""SSE pub/sub publisher (S-notify-5c, doc 10 §9).

A Beat sweep PUBLISHes a content-free per-user nudge to Redis for each freshly-committed
notification; the api SSE endpoint subscribes per-user and forwards the nudge so the bell
refetches its unread count.

De-dup is keyed on the IMMUTABLE ``notification.id`` (a Redis ``SET … NX``), NOT a
``created_at`` watermark: ``created_at`` is ``func.now()`` = PostgreSQL
``transaction_timestamp()`` (the caller txn's START), so a notification enqueued inside a
long/SERIALIZABLE caller txn carries a ``created_at`` far behind its eventual COMMIT — a
monotone watermark would permanently miss it. Keying on the id is immune to the clock;
``LOOKBACK_SECONDS`` only bounds how far back to scan for late-committing rows.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.notification import Notification

# Re-scan window — must exceed the longest plausible caller txn (the ack sweep mints a whole
# org's DOC_ACK batch in one txn; _cutover is SERIALIZABLE). 120 s is hugely generous at D1
# single-org scale; the 5-min FE backstop is the net for the pathological case.
LOOKBACK_SECONDS = 120
# 2x LOOKBACK — a per-notification de-dup key outlives the scan window, then expires (the id
# has aged out of the window so it can never be re-scanned/re-nudged).
DEDUP_TTL_SECONDS = 240


def channel_for_user(user_id: uuid.UUID) -> str:
    return f"notify:user:{user_id}"


def dedup_key(notification_id: uuid.UUID) -> str:
    return f"notify:pushed:{notification_id}"


async def publish_user_nudge(redis: Any, user_id: uuid.UUID) -> None:
    await redis.publish(
        channel_for_user(user_id), "1"
    )  # payload is irrelevant — a content-free nudge


async def sweep_and_publish(session: AsyncSession, redis: Any) -> int:
    """Nudge each user with a fresh unread not-yet-pushed notification. Returns # distinct users."""
    db_now = (await session.execute(select(func.now()))).scalar_one()
    cutoff = db_now - datetime.timedelta(seconds=LOOKBACK_SECONDS)
    rows = (
        await session.execute(
            select(Notification.id, Notification.recipient_user_id).where(
                Notification.read_at.is_(None),
                Notification.created_at > cutoff,
            )
        )
    ).all()
    users: set[uuid.UUID] = set()
    for nid, uid in rows:
        # SET key 1 NX EX → truthy ONLY the first time this id is seen → nudge that user once.
        if await redis.set(dedup_key(nid), "1", nx=True, ex=DEDUP_TTL_SECONDS):
            users.add(uid)
    for uid in users:
        await publish_user_nudge(redis, uid)
    return len(users)
