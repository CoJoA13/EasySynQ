# apps/api/tests/unit/test_notification_pubsub.py
"""Unit tests for the SSE pubsub sweep — de-dup/publish logic over a fake session + fake Redis."""

from __future__ import annotations

import datetime
import uuid

import pytest

from easysynq_api.services.notifications.pubsub import (
    channel_for_user,
    dedup_key,
    sweep_and_publish,
)

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, scalar: object = None, rows: object = None) -> None:
        self._scalar = scalar
        self._rows = rows

    def scalar_one(self) -> object:
        return self._scalar

    def all(self) -> object:
        return self._rows


class _FakeSession:
    """execute() #1 → db_now scalar; #2 → the (id, user_id) rows."""

    def __init__(self, db_now: datetime.datetime, rows: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        self._queue = [_Result(scalar=db_now), _Result(rows=rows)]

    async def execute(self, _stmt: object) -> _Result:
        return self._queue.pop(0)


class _FakeRedis:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.store: set[str] = set(existing or set())
        self.published: list[tuple[str, str]] = []

    async def set(
        self, key: str, _val: str, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store.add(key)
        return True

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


_NOW = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


async def test_nudges_each_user_once_then_dedups() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    id1, id2 = uuid.uuid4(), uuid.uuid4()
    redis = _FakeRedis()

    n = await sweep_and_publish(_FakeSession(_NOW, [(id1, a), (id2, b)]), redis)
    assert n == 2
    assert set(redis.published) == {(channel_for_user(a), "1"), (channel_for_user(b), "1")}

    # Same ids next sweep → already-seen → no new publish (no re-nudge of standing-unread).
    redis.published.clear()
    n2 = await sweep_and_publish(_FakeSession(_NOW, [(id1, a), (id2, b)]), redis)
    assert n2 == 0
    assert redis.published == []


async def test_two_ids_same_user_collapse_to_one_publish() -> None:
    a = uuid.uuid4()
    redis = _FakeRedis()
    n = await sweep_and_publish(_FakeSession(_NOW, [(uuid.uuid4(), a), (uuid.uuid4(), a)]), redis)
    assert n == 1
    assert redis.published == [(channel_for_user(a), "1")]


async def test_redis_flush_renudges_once() -> None:
    a = uuid.uuid4()
    id1 = uuid.uuid4()
    redis = _FakeRedis()
    await sweep_and_publish(_FakeSession(_NOW, [(id1, a)]), redis)
    # Flush → de-dup keys gone → the still-in-window row re-nudges once (over-publish is harmless).
    redis.store.clear()
    redis.published.clear()
    n = await sweep_and_publish(_FakeSession(_NOW, [(id1, a)]), redis)
    assert n == 1
    assert redis.published == [(channel_for_user(a), "1")]


def test_dedup_key_is_per_notification() -> None:
    nid = uuid.uuid4()
    assert dedup_key(nid) == f"notify:pushed:{nid}"
    assert channel_for_user(nid) == f"notify:user:{nid}"
