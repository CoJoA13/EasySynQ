"""Operator CLI for the read-only filesystem mirror (slice S7) — runs inside the api/worker image.

    python -m easysynq_api.cli.mirror sync      # full reconcile: rebuild + atomic swap
    python -m easysynq_api.cli.mirror rebuild   # alias (doc 04 §10.4 "easysynq mirror rebuild")

Both subcommands run the same full, idempotent rebuild of the Effective-only mirror from the vault
(PG + MinIO) — the manual fallback for the nightly Beat reconcile and the way an operator forces the
mirror back into agreement on demand. Acquires the same ``LOCK_MIRROR_SYNC`` advisory lock the Beat
task uses, so a manual rebuild and the scheduler cannot race (it skips, printing a notice, if a sync
is already in progress).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.common.pg_locks import LOCK_MIRROR_SYNC, pg_advisory_lock
from ..services.vault.mirror import MirrorSyncResult, sync_mirror


async def _sync() -> MirrorSyncResult | None:
    """Rebuild under the advisory lock; ``None`` if another sync holds the lock (skip)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                return None
            return await sync_mirror(session=session)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-mirror", description="Read-only mirror CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="full rebuild + atomic swap of the Effective-only mirror")
    sub.add_parser("rebuild", help="alias for sync (regenerate the whole mirror from the vault)")
    parser.parse_args(argv)  # both commands are the same full rebuild; parsing validates the verb

    result = asyncio.run(_sync())
    if result is None:
        print("mirror sync skipped: another sync is already in progress")
        return 0
    print(
        f"mirror synced: documents={result.documents} files={result.files} "
        f"pending_renditions={result.pending_renditions}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
