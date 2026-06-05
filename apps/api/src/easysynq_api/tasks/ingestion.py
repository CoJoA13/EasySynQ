"""Celery tasks for the ingestion pipeline (slices S-ing-1/2/3, doc 09 §3-8).

The pipeline auto-chains via ``.delay``-triggered tasks (NOT Beat-scheduled): ``scan_source`` →
``extract_source`` → ``classify_source`` → ``dedup_source`` → ``propose_source`` (each enqueues the
next after its commit). All are idempotent under ``task_acks_late`` re-delivery (status-guarded +
per-(run,file) upsert / whole-run replace) and fail-closed (the packs build/reaper discipline).
``reap_stalled_runs`` is the Beat job that recovers a run wedged in any in-progress stage (a hard
worker kill strands it) → FAILED, freeing the source-root lock. Each uses a fresh disposed async
engine per ``asyncio.run`` on the app DSN (the non-owner ``easysynq_app`` role — the import_* tables
are granted to it in 0029/0030/0031)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.ingestion import (
    reap_stalled_runs,
    run_classify,
    run_dedup,
    run_extract,
    run_propose,
    run_scan,
)
from .app import app

logger = logging.getLogger("easysynq.ingestion.tasks")


async def _with_session[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run ``fn`` against a fresh, disposed engine/session (safe inside the worker's ``asyncio.run``
    — a singleton engine would bind to a closed loop)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            return await fn(session)
    finally:
        await engine.dispose()


@app.task(name="easysynq.ingestion.scan_source")  # type: ignore[untyped-decorator]
def scan_source(run_id: str) -> None:
    """Walk + inventory one run's source tree, then chain to extract (idempotent; fail-closed)."""
    asyncio.run(_with_session(lambda s: run_scan(s, uuid.UUID(run_id))))


@app.task(name="easysynq.ingestion.extract_source")  # type: ignore[untyped-decorator]
def extract_source(run_id: str) -> None:
    """Extract text/metadata/OCR for one run's files, then chain to classify (idempotent)."""
    asyncio.run(_with_session(lambda s: run_extract(s, uuid.UUID(run_id))))


@app.task(name="easysynq.ingestion.classify_source")  # type: ignore[untyped-decorator]
def classify_source(run_id: str) -> None:
    """Score the four classification dimensions, then chain to dedup (idempotent)."""
    asyncio.run(_with_session(lambda s: run_classify(s, uuid.UUID(run_id))))


@app.task(name="easysynq.ingestion.dedup_source")  # type: ignore[untyped-decorator]
def dedup_source(run_id: str) -> None:
    """Detect exact/near dups + version families, then chain to propose (idempotent)."""
    asyncio.run(_with_session(lambda s: run_dedup(s, uuid.UUID(run_id))))


@app.task(name="easysynq.ingestion.propose_source")  # type: ignore[untyped-decorator]
def propose_source(run_id: str) -> None:
    """Build the per-keep-item proposal, then rest at Proposed (idempotent; releases the lock)."""
    asyncio.run(_with_session(lambda s: run_propose(s, uuid.UUID(run_id))))


@app.task(name="easysynq.ingestion.reap_stalled_runs")  # type: ignore[untyped-decorator]
def reap_stalled_runs_task() -> dict[str, int]:
    """Flip runs wedged in any in-progress stage (dead lock / past the backstop) → FAILED + free the
    source-root lock; returns ``{reaped}``."""

    async def _reap(session: AsyncSession) -> dict[str, int]:
        summary = await reap_stalled_runs(session)
        logger.info("ingestion.reap_stalled_runs", extra={"extra_fields": summary})
        return summary

    return asyncio.run(_with_session(_reap))
