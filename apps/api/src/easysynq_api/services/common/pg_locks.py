"""PostgreSQL session-level advisory locks for exactly-once Beat tasks (slice S6, R12).

The chain-linker MUST run as exactly one process at a time (otherwise two linkers could double-stamp
the same unchained rows). A session-level ``pg_try_advisory_lock`` gives that guarantee cheaply and
is auto-released if the worker crashes (the lock dies with the connection) — strictly stronger than
relying on ``beat`` being a singleton. Non-blocking: a second linker that cannot acquire the lock
simply skips this tick and retries on the next schedule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Distinct keys per exactly-once Beat task (arbitrary but fixed). Session-level locks are
# connection-scoped, so they persist across the commits the linker makes within a single run.
LOCK_CHAIN_LINK = 7710001
LOCK_ROLL_PARTITIONS = 7710002
# S7: serialize mirror rebuilds so two overlapping syncs cannot race on the temp-tree → swap.
LOCK_MIRROR_SYNC = 7710003
# S8b2: serialize the backup/restore-test drill — one scratch DB + scratch-bucket prefix at a time
# (a second concurrent drill skips this tick). The advisory lock also auto-releases on a crash.
LOCK_RESTORE_DRILL = 7710004


@asynccontextmanager
async def pg_advisory_lock(session: AsyncSession, key: int) -> AsyncIterator[bool]:
    """Try to take session-level advisory lock ``key`` on ``session``'s connection. Yields True if
    acquired (caller does its work, then the lock is released on exit), False if another holder has
    it (caller should skip and retry next tick). The unlock is non-transactional, so it releases
    even if the surrounding transaction later rolls back."""
    acquired = bool(
        (await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar()
    )
    try:
        yield acquired
    finally:
        if acquired:
            await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
