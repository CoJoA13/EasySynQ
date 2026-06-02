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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models.document_version import DocumentVersion
from ..services.common.pg_locks import LOCK_MIRROR_SYNC, pg_advisory_lock
from ..services.vault.mirror import MirrorSyncResult, sync_mirror
from ..services.vault.render_gotenberg import GotenbergRenderSink


async def _sync(*, force: bool) -> MirrorSyncResult | None:
    """Rebuild under the advisory lock; ``None`` if another sync holds the lock (skip). ``force``
    clears every cached rendition first (``rebuild``) so each doc re-renders — used after a template
    change (e.g. the S7c verify QR) where the content-addressed cache would otherwise be a hit."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                return None
            if force:
                # Deliberate blanket null (single-org per install, D1; runs under LOCK_MIRROR_SYNC).
                # Add a WHERE (org/document) predicate if multi-org or selective rebuild ever lands.
                await session.execute(update(DocumentVersion).values(rendition_blob_sha256=None))
            # Render for real (S7b) — like the Beat task; without this the CLI rebuild would write
            # every doc as render_status="pending" (the no-op default sink).
            return await sync_mirror(session=session, render_sink=GotenbergRenderSink())
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-mirror", description="Read-only mirror CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="incremental rebuild + atomic swap (cached renditions reused)")
    sub.add_parser(
        "rebuild", help="full regenerate — clears cached renditions + re-renders (doc 04 §10.4)"
    )
    args = parser.parse_args(argv)

    result = asyncio.run(_sync(force=args.command == "rebuild"))
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
