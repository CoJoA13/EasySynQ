"""S-notify-5c — the SSE pubsub sweep end-to-end (real DB + testcontainer Redis) and the
GET /notifications/stream endpoint auth. The endpoint tests are added in Task 5."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.notification import Notification
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.redis_client import redis_client
from easysynq_api.services.notifications.pubsub import channel_for_user, sweep_and_publish

pytestmark = pytest.mark.integration


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _seed_user(org_id: uuid.UUID, salt: str) -> AppUser:
    async with get_sessionmaker()() as s:
        u = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-sse-{salt}",
            display_name=f"SSE Test {salt}",
            email=f"sse-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u


async def _seed_notification(
    org_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
    *,
    created_at: datetime.datetime | None = None,
    read_at: datetime.datetime | None = None,
) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        n = Notification(
            org_id=org_id,
            recipient_user_id=recipient_user_id,
            event_key="task.assigned",
            subject_type="document",
            subject_id=None,
            title="t",
            body="b",
            deep_link="/tasks",
            read_at=read_at,
        )
        if created_at is not None:
            n.created_at = created_at
        s.add(n)
        await s.commit()
        await s.refresh(n)
        return n.id


async def _drain_subscribe(ps: Any) -> None:
    # consume the 'subscribe' confirmation message
    for _ in range(10):
        m = await ps.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if m is None:
            return


async def _await_nudge(ps: Any, *, expect: bool) -> bool:
    for _ in range(25):
        m = await ps.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if m is not None and m.get("type") == "message":
            return True
    return False


async def test_sweep_publishes_nudge_for_recent_unread(app_under_test: Any) -> None:
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, salt)
    await _seed_notification(org_id, user.id)  # created_at defaults to now()

    sub = redis_client(decode_responses=True)
    ps = sub.pubsub()
    try:
        await ps.subscribe(channel_for_user(user.id))
        await _drain_subscribe(ps)
        pub = redis_client(decode_responses=True)
        try:
            async with get_sessionmaker()() as session:
                await sweep_and_publish(session, pub)
        finally:
            await pub.aclose()
        assert await _await_nudge(ps, expect=True), "expected a nudge for the recent unread row"
    finally:
        await ps.unsubscribe(channel_for_user(user.id))
        await ps.aclose()
        await sub.aclose()


async def test_sweep_skips_read_and_old(app_under_test: Any) -> None:
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, salt)
    now = datetime.datetime.now(datetime.UTC)
    await _seed_notification(org_id, user.id, read_at=now)  # already read → skip
    await _seed_notification(
        org_id, user.id, created_at=now - datetime.timedelta(seconds=130)
    )  # > LOOKBACK → skip

    sub = redis_client(decode_responses=True)
    ps = sub.pubsub()
    try:
        await ps.subscribe(channel_for_user(user.id))
        await _drain_subscribe(ps)
        pub = redis_client(decode_responses=True)
        try:
            async with get_sessionmaker()() as session:
                await sweep_and_publish(session, pub)
        finally:
            await pub.aclose()
        assert not await _await_nudge(ps, expect=False), "read/old rows must not nudge"
    finally:
        await ps.unsubscribe(channel_for_user(user.id))
        await ps.aclose()
        await sub.aclose()


async def test_sweep_nudges_txn_start_clock_row_within_lookback(app_under_test: Any) -> None:
    """The validation regression: a row whose created_at is well behind db_now (the batch /
    txn-start clock case) is STILL nudged as long as it is within LOOKBACK — the watermark
    miss is closed."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, salt)
    now = datetime.datetime.now(datetime.UTC)
    await _seed_notification(org_id, user.id, created_at=now - datetime.timedelta(seconds=60))

    sub = redis_client(decode_responses=True)
    ps = sub.pubsub()
    try:
        await ps.subscribe(channel_for_user(user.id))
        await _drain_subscribe(ps)
        pub = redis_client(decode_responses=True)
        try:
            async with get_sessionmaker()() as session:
                await sweep_and_publish(session, pub)
        finally:
            await pub.aclose()
        assert await _await_nudge(ps, expect=True), (
            "a 60s-old (within LOOKBACK) row must still nudge"
        )
    finally:
        await ps.unsubscribe(channel_for_user(user.id))
        await ps.aclose()
        await sub.aclose()


async def test_stream_requires_bearer(app_client: AsyncClient, app_under_test: Any) -> None:
    r = await app_client.get("/api/v1/notifications/stream")
    assert r.status_code == 401


# Intentionally NO HTTP-level test that reads the 200 stream body: httpx's in-process ASGITransport
# buffers a response until the ASGI app completes, but the SSE generator is infinite -- so
# app_client.stream(...) on an authed stream hangs (empirically confirmed, even the status never
# arrives). The full HTTP stream (real ASGI + Caddy flush_interval -1) is live-smoke-verified.
# Here we drive the generator directly against the REAL testcontainer Redis to prove the redis-py
# pubsub API + the on-connect frame + teardown end-to-end.
class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


async def test_event_stream_first_frame_against_real_redis(app_under_test: Any) -> None:
    from easysynq_api.api._sse import event_stream

    agen = event_stream(_ConnectedRequest(), uuid.uuid4())  # type: ignore[arg-type]
    try:
        first = await agen.__anext__()
        assert first == "event: notify\ndata: \n\n"
    finally:
        await agen.aclose()  # real-Redis unsubscribe + aclose teardown (must not raise)
