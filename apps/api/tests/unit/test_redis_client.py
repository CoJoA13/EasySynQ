"""Unit test for the shared async-redis factory (C-1).

Pins the ``decode_responses`` contract: the lock/string callers pass ``True``, the readiness ping +
perm-epoch incr leave the default ``False`` (bytes). A future flip of the default would silently
change str-vs-bytes responses at every lock site, so this locks it."""

from __future__ import annotations

from typing import Any

import pytest

from easysynq_api import redis_client as rc


def test_redis_client_threads_decode_responses_and_settings_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_from_url(url: str, **kwargs: Any) -> str:
        calls.append({"url": url, **kwargs})
        return "client"

    monkeypatch.setattr(rc.aioredis, "from_url", _fake_from_url)

    assert rc.redis_client() == "client"  # default: bytes (readiness / pep)
    assert rc.redis_client(decode_responses=True) == "client"  # str (locks / setup)

    assert [c["decode_responses"] for c in calls] == [False, True]
    assert all(c["url"] == rc.get_settings().redis_url for c in calls)
