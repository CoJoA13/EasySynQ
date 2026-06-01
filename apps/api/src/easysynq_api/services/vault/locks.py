"""The exclusive check-out lock (Redis) — the runtime authority for who may edit a document.

Key ``lock:doc:{id}`` holds an opaque acquire token; ``SET … NX EX 28800`` gives a single
holder for 8h (R24). The holder's *identity* lives in the ``working_draft`` PG mirror
(``checked_out_by``), so a 409 can name them. Release is token-checked (CAS) so only the holder
clears their own lock; break-lock force-clears it without the token (and preserves scratch, R9).
"""

from __future__ import annotations

import uuid
from typing import Any

import redis.asyncio as aioredis

from ...config import get_settings

LOCK_TTL_SECONDS = 28800  # 8 hours (R24)

_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

_HEARTBEAT_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


def _key(document_id: uuid.UUID) -> str:
    return f"lock:doc:{document_id}"


def _redis() -> Any:
    # Typed as Any: redis.asyncio's response unions don't play well with mypy --strict await.
    return aioredis.from_url(get_settings().redis_url, decode_responses=True)  # type: ignore[no-untyped-call]


async def acquire(document_id: uuid.UUID) -> str | None:
    """Try to take the lock. Returns an opaque token on success, ``None`` if already held."""
    token = uuid.uuid4().hex
    async with _redis() as client:
        ok = await client.set(_key(document_id), token, nx=True, ex=LOCK_TTL_SECONDS)
    return token if ok else None


async def is_locked(document_id: uuid.UUID) -> bool:
    async with _redis() as client:
        return bool(await client.exists(_key(document_id)))


async def ttl(document_id: uuid.UUID) -> int:
    """Remaining lock lifetime in seconds (-2 if absent, -1 if no expiry)."""
    async with _redis() as client:
        return int(await client.ttl(_key(document_id)))


async def release(document_id: uuid.UUID, token: str) -> bool:
    """Owner-checked release (CAS on the token). True iff this holder's lock was cleared."""
    async with _redis() as client:
        return bool(await client.eval(_RELEASE_LUA, 1, _key(document_id), token))


async def force_release(document_id: uuid.UUID) -> None:
    """Break-lock: clear the lock without the token (the holder's scratch is preserved in PG)."""
    async with _redis() as client:
        await client.delete(_key(document_id))


async def heartbeat(document_id: uuid.UUID, token: str) -> bool:
    """Owner-checked TTL refresh, so an active editor's lock does not lapse mid-session."""
    async with _redis() as client:
        return bool(
            await client.eval(_HEARTBEAT_LUA, 1, _key(document_id), token, str(LOCK_TTL_SECONDS))
        )
