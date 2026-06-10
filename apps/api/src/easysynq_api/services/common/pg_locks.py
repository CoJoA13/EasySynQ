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
# S11: serialize the operator-grade LIVE restore (restore-to-verified-target). DISTINCT from the
# drill lock so a standing verified target is never swept by a concurrent nightly drill, and so two
# operator restores cannot collide on the restore_easysynq_* scratch namespace.
LOCK_RESTORE_LIVE = 7710005
# S-drift-1: serialize the daily D5 periodic re-review sweep (one open-instance pass + the
# once-per-cycle overdue-audit pass) so two overlapping Beat fires cannot double-open instances.
LOCK_REVIEW_SWEEP = 7710006
# (The S-ing-5 commit single-flight is the per-item ledger CLAIM — INSERT ON CONFLICT DO UPDATE
# WHERE result='failed' — in ingestion.repository.claim_commit_result, NOT an advisory lock.)


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


async def holds_advisory_lock(session: AsyncSession, key: int) -> bool:
    """Does THIS session's connection still hold session-level advisory lock ``key``? A dropped
    connection silently FREES the lock while the Python context manager believes it is held —
    the pool then hands the next statement a fresh, lockless connection. Callers doing
    work-after-failure (the S-drift-2 scan pipeline) re-verify before irreversible steps.
    (Single-arg ``pg_try_advisory_lock(bigint)`` stores the key as classid=high32/objid=low32
    with ``objsubid = 1``; our keys fit in 32 bits, so classid is 0. The ``objsubid = 1`` guard
    distinguishes it from a hypothetical two-arg ``(0, key)`` lock, whose objsubid is 2.)"""
    return bool(
        (
            await session.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND classid = 0 AND objid = :k AND objsubid = 1 AND pid = pg_backend_pid())"
                ),
                {"k": key},
            )
        ).scalar()
    )
