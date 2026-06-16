"""Shared async-Redis client factory (D4: Redis is the broker / cache / lock store).

Centralizes the single ``redis.asyncio.from_url`` call so the unavoidable
``# type: ignore[no-untyped-call]`` (redis is in mypy's untyped-module override) lives in exactly
one place. Returns ``Any`` deliberately: redis.asyncio's response unions don't play well with
``mypy --strict`` ``await`` — the long-standing convention in the lock modules."""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

from .config import get_settings


def redis_client(*, decode_responses: bool = False) -> Any:
    """A new async Redis client bound to ``settings.redis_url``. Supports both ``async with`` and a
    manual ``aclose()`` (callers use both styles). ``decode_responses=True`` for the lock / string
    callers; the readiness ping + the perm-epoch incr leave bytes (the prior behaviour)."""
    return aioredis.from_url(  # type: ignore[no-untyped-call]
        get_settings().redis_url, decode_responses=decode_responses
    )
