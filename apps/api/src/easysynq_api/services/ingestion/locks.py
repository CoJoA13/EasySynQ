"""The import source-root lock (Redis) — the runtime authority for "one active run per source root"
(slice S-ing-1, doc 09 §3.3).

Key ``import:src:{hash}`` holds an opaque acquire token; ``SET … NX EX`` is atomic, so two
concurrent
``POST /admin/imports`` for the same root can never both win — the loser gets ``None`` → 409 (no DB
constraint needed). Release is token-checked (CAS) so only the holder clears its own lock; the
worker
``heartbeat``s per batch (a large tree can out-live any fixed TTL), and the stalled-scan reaper
``force_release``s a lock whose worker died. Mirrors ``services/vault/locks.py`` (the check-out
lock),
keyed on the source-root hash instead of a document id."""

from __future__ import annotations

from typing import Any

from ...redis_client import redis_client

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


def _key(source_root_hash: str) -> str:
    return f"import:src:{source_root_hash}"


def _redis() -> Any:
    return redis_client(decode_responses=True)


async def acquire(source_root_hash: str, *, ttl: int) -> str | None:
    """Try to take the source-root lock. Returns an opaque token on success, ``None`` if already
    held."""
    import uuid

    token = uuid.uuid4().hex
    async with _redis() as client:
        ok = await client.set(_key(source_root_hash), token, nx=True, ex=ttl)
    return token if ok else None


async def release(source_root_hash: str, token: str) -> bool:
    """Owner-checked release (CAS on the token). True iff this holder's lock was cleared."""
    async with _redis() as client:
        return bool(await client.eval(_RELEASE_LUA, 1, _key(source_root_hash), token))


async def force_release(source_root_hash: str) -> None:
    """Break the lock without the token — the stalled-run reaper recovering an abandoned source
    root."""
    async with _redis() as client:
        await client.delete(_key(source_root_hash))


async def is_alive(source_root_hash: str) -> bool:
    """True iff the source-root lock key still exists (S-ing-2). The lock is held continuously
    scan→extract→classify and heartbeated per batch, so a *missing* key on an in-progress run means
    the worker died (its TTL lapsed with no heartbeat) — the reaper's primary stall signal."""
    async with _redis() as client:
        return bool(await client.exists(_key(source_root_hash)))


async def heartbeat(source_root_hash: str, token: str, *, ttl: int) -> bool:
    """Owner-checked TTL refresh, so a long scan's lock does not lapse mid-walk (ties liveness to
    per-batch progress)."""
    async with _redis() as client:
        return bool(await client.eval(_HEARTBEAT_LUA, 1, _key(source_root_hash), token, str(ttl)))
