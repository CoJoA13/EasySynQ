"""Operator CLI for the read-only filesystem mirror (slice S7) — runs inside the api/worker image.

    python -m easysynq_api.cli.mirror sync      # full reconcile: rebuild + atomic swap
    python -m easysynq_api.cli.mirror rebuild   # alias (doc 04 §10.4 "easysynq mirror rebuild")
    python -m easysynq_api.cli.mirror scan      # integrity scan only (R11) — no rebuild

Both sync/rebuild subcommands now run the scan-first pipeline (scan → quarantine → audit → rebuild)
— the manual fallback for the nightly Beat reconcile and the way an operator forces the mirror back
into agreement on demand. ``scan`` detects/quarantines/audits without triggering a rebuild;
follow with ``sync`` to correct. All subcommands acquire the same ``LOCK_MIRROR_SYNC`` advisory
lock the Beat task uses, so a manual run and the scheduler cannot race (it skips if already held).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models._vault_enums import VersionState
from ..db.models.document_version import DocumentVersion
from ..services.common.pg_locks import LOCK_MIRROR_SYNC, pg_advisory_lock
from ..services.vault.mirror import MirrorSyncResult
from ..services.vault.mirror_scan import (
    ScanReport,
    persist_scan_results,
    scan_and_sync,
    scan_mirror,
)
from ..services.vault.render_gotenberg import GotenbergRenderSink


async def _sync(*, force: bool) -> MirrorSyncResult | None:
    """Scan-first rebuild under the advisory lock; ``None`` if another sync/scan holds the lock
    (skip). ``force`` clears every cached rendition first (``rebuild``) so each doc re-renders —
    used after a template change (e.g. the S7c verify QR) where the content-addressed cache would
    otherwise be a hit. (The clear nulls ``rendition_blob_sha256`` BEFORE the scan, so a
    tampered-with-an-old-rendition file classifies TAMPER rather than STALE on this manual path —
    run ``scan`` first if forensic classification matters.)"""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                return None
            if force:
                # Scoped to Effective (spec §11.6): only Effective versions ever re-render, so a
                # blanket null would PERMANENTLY destroy superseded-rendition digests and
                # mis-classify every future rendition-rollback tamper as TAMPER instead of STALE.
                # Committed BEFORE the pipeline — persist_scan_results rolls back on a FAILED
                # scan and would otherwise silently undo the forced re-render.
                await session.execute(
                    update(DocumentVersion)
                    .where(DocumentVersion.version_state == VersionState.Effective)
                    .values(rendition_blob_sha256=None)
                )
                await session.commit()
            # Render for real (S7b) — like the Beat task; without this the CLI rebuild would write
            # every doc as render_status="pending" (the no-op default sink).
            _report, result = await scan_and_sync(
                session, rebuild="always", triggered_by="cli", render_sink=GotenbergRenderSink()
            )
            return result
    finally:
        await engine.dispose()


async def _scan() -> ScanReport | None:
    """Detect/quarantine/audit only — NO rebuild (the operator follows with ``sync`` to correct).
    ``None`` if another sync/scan holds the lock."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                return None
            report = await scan_mirror(session)
            await persist_scan_results(session, report, rebuild_triggered=False, triggered_by="cli")
            return report
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-mirror", description="Read-only mirror CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="incremental rebuild + atomic swap (cached renditions reused)")
    sub.add_parser(
        "rebuild", help="full regenerate — clears cached renditions + re-renders (doc 04 §10.4)"
    )
    sub.add_parser(
        "scan", help="integrity scan only — detect/quarantine/audit, no rebuild (doc 05 §9.2, R11)"
    )
    args = parser.parse_args(argv)

    if args.command == "scan":
        report = asyncio.run(_scan())
        if report is None:
            print("mirror scan skipped: another sync/scan is already in progress")
            return 0
        c = report.counts()
        print(
            f"mirror scan: status={report.status} baseline={report.baseline} "
            f"scanned={c['scanned']} findings={len(report.findings)} "
            f"quarantined={c['quarantined']} is_current={report.is_current}"
        )
        return 1 if report.status == "FAILED" else 0

    result = asyncio.run(_sync(force=args.command == "rebuild"))
    if result is None:
        print("mirror sync skipped: another sync is already in progress")
        return 0
    print(
        f"mirror synced: documents={result.documents} files={result.files} "
        f"symlinks={result.symlinks} pending_renditions={result.pending_renditions}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
