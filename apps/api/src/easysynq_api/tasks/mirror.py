"""Celery/Beat task for the read-only filesystem mirror (slice S7, AC#2).

``mirror_sync`` is a full rebuild + atomic swap of the Effective-only mirror, run under a PG
session-level advisory lock so two overlapping syncs cannot race on the temp-tree → swap. It is the
target of three triggers: the **nightly Beat reconcile** (doc 04 §10.4), the **post-commit enqueue**
from release/obsolete (``mirror_sink``), and the ``easysynq mirror sync`` CLI. Reuses the S4
``release_due`` / S6 audit-task idiom — its own disposed async engine per ``asyncio.run`` (the app's
non-owner ``easysynq_app`` role; SELECT on ``document_version``/``blob`` is all it needs).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.common.pg_locks import LOCK_MIRROR_SYNC, pg_advisory_lock
from ..services.vault.mirror import sync_mirror
from ..services.vault.render_gotenberg import GotenbergRenderSink
from .app import app

logger = logging.getLogger("easysynq.mirror.tasks")


async def _run_mirror_sync() -> int:
    """Rebuild the mirror under the advisory lock; returns the document count written (0 if another
    sync holds the lock and this tick is skipped)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                logger.info("mirror.sync: another sync holds the lock; skipping this tick")
                return 0
            # The worker renders for real (S7b); the api never renders (it presigns the cache).
            result = await sync_mirror(session=session, render_sink=GotenbergRenderSink())
            logger.info(
                "mirror.sync.done",
                extra={
                    "extra_fields": {
                        "documents": result.documents,
                        "files": result.files,
                        "pending_renditions": result.pending_renditions,
                    }
                },
            )
            return result.documents
    finally:
        await engine.dispose()


@app.task(name="easysynq.mirror.sync")  # type: ignore[untyped-decorator]
def mirror_sync() -> int:
    """Full rebuild + atomic swap of the read-only Effective-only mirror; returns the doc count."""
    return asyncio.run(_run_mirror_sync())
