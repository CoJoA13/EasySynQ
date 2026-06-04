"""Celery tasks for the ingestion scan (slice S-ing-1, doc 09 §3-4).

``scan_source`` is ``.delay``-triggered by ``create_import_run`` (NOT Beat-scheduled): the worker
walks
the read-only source tree + inventories it (``services.ingestion.run_scan``).
``reap_stalled_scans`` is a
Beat job that recovers runs wedged in SCANNING (a hard worker kill strands them) → FAILED, and
frees the
source-root lock. Both use a fresh disposed async engine per ``asyncio.run`` on the **app DSN** (the
non-owner ``easysynq_app`` role — import_run/import_file are granted to it in 0029)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.ingestion import reap_stalled_scans, run_scan
from .app import app

logger = logging.getLogger("easysynq.ingestion.tasks")


async def _run_scan(run_id: str) -> None:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    rid = uuid.UUID(run_id)
    try:
        async with sessionmaker() as session:
            await run_scan(session, rid)
    finally:
        await engine.dispose()


async def _run_reaper() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await reap_stalled_scans(session)
            logger.info("ingestion.reap_stalled_scans", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.ingestion.scan_source")  # type: ignore[untyped-decorator]
def scan_source(run_id: str) -> None:
    """Walk + inventory one import run's source tree (idempotent on retry; fail-closed)."""
    asyncio.run(_run_scan(run_id))


@app.task(name="easysynq.ingestion.reap_stalled_scans")  # type: ignore[untyped-decorator]
def reap_stalled_scans_task() -> dict[str, int]:
    """Flip scans wedged in SCANNING past the stall window → FAILED + free the lock; returns
    ``{reaped}``."""
    return asyncio.run(_run_reaper())
