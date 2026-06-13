"""PostgreSQL session-level advisory locks for exactly-once Beat tasks (slice S6, R12).

The chain-linker MUST run as exactly one process at a time (otherwise two linkers could double-stamp
the same unchained rows). A session-level ``pg_try_advisory_lock`` gives that guarantee cheaply and
is auto-released if the worker crashes (the lock dies with the connection) — strictly stronger than
relying on ``beat`` being a singleton. Non-blocking: a second linker that cannot acquire the lock
simply skips this tick and retries on the next schedule.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

logger = logging.getLogger("easysynq.locks")

# Distinct keys per exactly-once Beat task (arbitrary but fixed). Session-level locks are
# connection-scoped; ``pg_advisory_lock`` pins the lock to a DEDICATED engine connection held
# open for the context's duration, so it persists across the commits the linker makes within a
# single run BY CONSTRUCTION — not by the worker-engine accident of a single-connection pool.
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
# S-drift-3: serialize the daily D1 blob re-hash — one rotation pass at a time (a second
# concurrent verify skips its tick). DISTINCT from LOCK_MIRROR_SYNC: blob verify never touches
# the mirror, and sharing would couple unrelated cadences.
LOCK_BLOB_VERIFY = 7710007
# S-ack-1: serialize the acknowledgement sweep (daily Beat + the doc-scoped release/distribution
# enqueues share one lock — overlapping fires must not double-mint per-user instances).
LOCK_ACK_SWEEP = 7710008
# S-mr-1: serialize the daily management-review cadence sweep (one mint-the-next-scheduled-MR pass)
# so two overlapping Beat fires cannot double-open the next review (acks-late re-delivery makes
# concurrent runs real — the sweep_reviews posture; the org-scoped open_review_exists check is the
# idempotency guard the lock complements).
LOCK_MGMT_REVIEW_SWEEP = 7710009
# (The S-ing-5 commit single-flight is the per-item ledger CLAIM — INSERT ON CONFLICT DO UPDATE
# WHERE result='failed' — in ingestion.repository.claim_commit_result, NOT an advisory lock.)


@asynccontextmanager
async def pg_advisory_lock(session: AsyncSession, key: int) -> AsyncIterator[bool]:
    """Try to take session-level advisory lock ``key``. Yields True if acquired (caller does its
    work, then the lock is released on exit), False if another holder has it (caller should skip
    and retry next tick). The unlock is non-transactional, so it releases even if the surrounding
    transaction later rolls back.

    The lock lives on a DEDICATED connection from the session's engine (held open for the
    context's duration) — a Session releases its pooled connection at every commit, so locking via
    the session would strand the lock on an idle pooled connection when the finally's unlock
    rotates onto a different backend (the S-ack-1 CI forensics; the mirror_scan "a Session's
    connection is not stable across commit" posture). Auto-release on a crash still holds: the
    lock dies with the dedicated connection."""
    # Every caller builds its session via async_sessionmaker(engine), so .bind is the AsyncEngine;
    # tolerate an AsyncConnection bind by climbing to its engine.
    bind = session.bind
    engine: AsyncEngine = bind if isinstance(bind, AsyncEngine) else bind.engine
    async with engine.connect() as conn:
        # The advisory SELECTs autobegin a transaction on this connection that is never committed;
        # on context exit the connection rolls back — fine: session-level advisory locks are
        # transaction-independent.
        acquired = bool(
            (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar()
        )
        try:
            yield acquired
        finally:
            if acquired:
                released = (
                    await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                ).scalar()
                if not released:  # pragma: no cover — would mean the dedicated conn lost the lock
                    logger.error("pg_advisory_unlock failed for key %s", key)


async def holds_advisory_lock(session: AsyncSession, key: int) -> bool:
    """Does THIS session's connection still hold session-level advisory lock ``key``? A dropped
    connection silently FREES the lock while the Python context manager believes it is held —
    the pool then hands the next statement a fresh, lockless connection. No live src caller
    today: the S-drift-2 scan pipeline's in-session recheck was removed (a Session's connection
    is not stable across commit — see mirror_scan.scan_and_sync), and ``pg_advisory_lock`` now
    holds its lock on a DEDICATED engine connection, so this session-pid probe would NOT see that
    helper's hold anyway. It remains valid for a session that took a lock on its OWN connection
    (and held it without committing).
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
