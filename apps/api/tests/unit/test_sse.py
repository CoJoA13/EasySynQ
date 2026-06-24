# apps/api/tests/unit/test_sse.py
"""Unit tests for the SSE helper — sse_event formatting + event_stream (fake pubsub)."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.api._sse import event_stream, sse_event

pytestmark = pytest.mark.unit


class _FakeRequest:
    def __init__(self, disconnect_after: int = 10_000) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnect_after


class _FakePubSub:
    def __init__(self, messages: list[dict | None]) -> None:
        self._messages = list(messages)
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, ch: str) -> None:
        self.subscribed.append(ch)

    async def get_message(
        self,
        ignore_subscribe_messages: bool = True,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> dict | None:
        return self._messages.pop(0) if self._messages else None

    async def unsubscribe(self, ch: str) -> None:
        self.unsubscribed.append(ch)

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> _FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:
        self.closed = True


def test_sse_event_format() -> None:
    assert sse_event("notify") == "event: notify\ndata: \n\n"
    assert sse_event("notify", "x") == "event: notify\ndata: x\n\n"


async def test_event_stream_initial_message_heartbeat_and_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pubsub = _FakePubSub([{"type": "message", "data": "1"}, None])  # one msg, then timeout
    redis = _FakeRedis(pubsub)
    monkeypatch.setattr("easysynq_api.api._sse.redis_client", lambda **k: redis)
    req = _FakeRequest(disconnect_after=2)
    uid = uuid.uuid4()

    frames = [chunk async for chunk in event_stream(req, uid)]

    assert frames[0] == "event: notify\ndata: \n\n"  # on-connect sync
    assert frames[1] == "event: notify\ndata: \n\n"  # the pubsub message
    assert frames[2] == ": ping\n\n"  # the heartbeat on timeout
    assert pubsub.subscribed == [f"notify:user:{uid}"]
    assert pubsub.unsubscribed == [f"notify:user:{uid}"]  # teardown ran
    assert pubsub.closed and redis.closed


async def test_event_stream_teardown_on_aclose(monkeypatch: pytest.MonkeyPatch) -> None:
    pubsub = _FakePubSub([None] * 100)  # heartbeats forever
    redis = _FakeRedis(pubsub)
    monkeypatch.setattr("easysynq_api.api._sse.redis_client", lambda **k: redis)
    agen = event_stream(_FakeRequest(), uuid.uuid4())
    assert (await agen.__anext__()) == "event: notify\ndata: \n\n"
    await agen.aclose()  # the client vanished mid-stream
    assert pubsub.unsubscribed and pubsub.closed and redis.closed


class _UnsubscribeRaisesPubSub(_FakePubSub):
    """Variant of _FakePubSub whose unsubscribe raises to simulate a broken Redis connection."""

    async def unsubscribe(self, ch: str) -> None:
        raise RuntimeError("simulated unsubscribe failure")


async def test_event_stream_acloses_redis_even_if_unsubscribe_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inner finally in event_stream must reach redis.aclose() even when unsubscribe throws."""
    pubsub = _UnsubscribeRaisesPubSub([None] * 100)
    redis = _FakeRedis(pubsub)
    monkeypatch.setattr("easysynq_api.api._sse.redis_client", lambda **k: redis)
    agen = event_stream(_FakeRequest(), uuid.uuid4())
    assert (await agen.__anext__()) == "event: notify\ndata: \n\n"
    # aclose() may propagate the unsubscribe exception through the generator teardown;
    # assert redis.closed regardless — the inner finally MUST reach redis.aclose().
    try:
        await agen.aclose()
    except RuntimeError:
        pass
    assert redis.closed, "redis.aclose() must be called even when pubsub.unsubscribe raises"
