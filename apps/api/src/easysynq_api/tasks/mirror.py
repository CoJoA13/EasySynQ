"""Celery/Beat tasks for the read-only filesystem mirror (S7 sync + the S-drift-2 D2+D3 scan).

``mirror_sync`` is the scan-first full rebuild (R11's per-sync detection leg: scan the outgoing
tree, quarantine + audit divergence, THEN rebuild + swap — the rebuild prunes the old tree, so
scan-first is what preserves forensic evidence). Triggers: the nightly Beat reconcile, the
post-commit release/obsolete enqueue (``mirror_sink``), and the ``easysynq mirror sync`` CLI.
``mirror_scan`` is the hourly Beat integrity scan (doc 05 §9.2.1 — the accepted drift window =
the configured interval): same pipeline, but rebuilds only when divergent / behind-vault /
baseline-less (a CLEAN tick does no tree churn) and NOT on a FAILED scan. Both serialize under
``LOCK_MIRROR_SYNC`` (skip-if-held). Own disposed async engine per ``asyncio.run`` (the app's
non-owner ``easysynq_app`` role).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.common.pg_locks import LOCK_MIRROR_SYNC, pg_advisory_lock
from ..services.vault.mirror_scan import scan_and_sync
from ..services.vault.render_gotenberg import GotenbergRenderSink
from .app import task

logger = logging.getLogger("easysynq.mirror.tasks")


async def _run_mirror_sync() -> int:
    """Scan-first rebuild under the advisory lock; returns the document count written (0 if
    another sync/scan holds the lock and this tick is skipped)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                logger.info("mirror.sync: another sync/scan holds the lock; skipping this tick")
                return 0
            # The worker renders for real (S7b); the api never renders (it presigns the cache).
            report, result = await scan_and_sync(
                session, rebuild="always", triggered_by="sync", render_sink=GotenbergRenderSink()
            )
            logger.info(
                "mirror.sync.done",
                extra={
                    "extra_fields": {
                        "documents": result.documents if result is not None else 0,
                        "files": result.files if result is not None else 0,
                        "symlinks": result.symlinks if result is not None else 0,
                        "pending_renditions": (
                            result.pending_renditions if result is not None else 0
                        ),
                        "scan_status": report.status,
                        "scan_findings": len(report.findings),
                    }
                },
            )
            return result.documents if result is not None else 0
    finally:
        await engine.dispose()


async def _run_mirror_scan() -> dict[str, object]:
    """The hourly D2+D3 integrity scan; rebuilds only when needed (never on FAILED)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                logger.info("mirror.scan: another sync/scan holds the lock; skipping this tick")
                return {"skipped_lock_held": 1}
            report, result = await scan_and_sync(
                session,
                rebuild="if_needed",
                triggered_by="beat",
                render_sink=GotenbergRenderSink(),
            )
            summary: dict[str, object] = {
                **report.counts(),
                "rebuild_triggered": result is not None,
            }
            logger.info("mirror.scan.done", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@task(name="easysynq.mirror.sync")
def mirror_sync() -> int:
    """Scan-first full rebuild + atomic swap of the read-only mirror; returns the doc count."""
    return asyncio.run(_run_mirror_sync())


@task(name="easysynq.mirror.scan")
def mirror_scan() -> dict[str, object]:
    """Hourly D2+D3 integrity scan (R11); rebuilds only on divergence/staleness/no-baseline."""
    return asyncio.run(_run_mirror_scan())
