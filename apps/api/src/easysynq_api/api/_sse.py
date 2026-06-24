# apps/api/src/easysynq_api/api/_sse.py
"""Server-Sent-Events helper for the notification bell stream (S-notify-5c).

The generator is a PURE Redis subscriber — it holds NO DB session (the route authenticates in a
short-lived session that closes before streaming), so an indefinite SSE connection never pins a
pooled DB connection. It yields a content-free ``event: notify`` on connect + on each Redis nudge,
and a ``: ping`` heartbeat on idle (the heartbeat WRITE is the load-bearing detector of an abrupt
client drop; the finally always tears down the pubsub subscription + client).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import Request

from ..redis_client import redis_client
from ..services.notifications.pubsub import channel_for_user

HEARTBEAT_SECONDS = 20


def sse_event(event: str, data: str = "") -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def event_stream(request: Request, user_id: uuid.UUID) -> AsyncIterator[str]:
    redis = redis_client(decode_responses=True)
    pubsub = redis.pubsub()
    channel = channel_for_user(user_id)
    try:
        await pubsub.subscribe(channel)
        yield sse_event("notify")  # on-connect sync (covers a missed-while-disconnected gap)
        while True:
            if await request.is_disconnected():
                break
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=HEARTBEAT_SECONDS
            )
            if msg is not None:
                yield sse_event("notify")
            else:
                # heartbeat comment — keeps the proxy/client live; write surfaces a dead peer
                yield ": ping\n\n"
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        finally:
            await redis.aclose()
