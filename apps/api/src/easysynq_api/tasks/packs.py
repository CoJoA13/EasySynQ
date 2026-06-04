"""Celery tasks for evidence-pack builds (slice S-pack-1, doc 06 §7).

``build_evidence_pack`` is ``.delay``-triggered by ``generate_pack`` (NOT Beat-scheduled): the
worker assembles + seals the pack (``services.packs.build``). ``reap_stalled_builds`` is a daily
Beat job that recovers packs stuck in BUILDING (a hard worker kill between the BUILDING commit and
build's own error handler strands them; ``task_acks_late`` re-delivery is best-effort).

Like the other worker tasks they use a fresh disposed async engine per ``asyncio.run`` and connect
with the **app DSN** (the non-owner ``easysynq_app`` role) — the build SELECTs/INSERTs/UPDATEs the
pack/record/blob/evidence/audit tables, all granted to the app role (0010/0023/0024/0025).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.packs import build, build_and_cache_portfolio, reap_stalled_builds
from .app import app

logger = logging.getLogger("easysynq.packs.tasks")


async def _run_build(pack_id: str) -> None:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    pid = uuid.UUID(pack_id)
    try:
        # Stage 1: seal the canonical ZIP (re-raises on failure → no portfolio attempt).
        async with sessionmaker() as session:
            await build(session, pid)
        # Stage 2: the PDF portfolio variant (S-pack-2) — a SEPARATE transaction after the seal
        # commits, so a Gotenberg/assembly hiccup can never block or fail the canonical pack. Best
        # effort + idempotent on portfolio_blob_sha256 (a hard kill re-runs it on redelivery).
        async with sessionmaker() as session:
            await build_and_cache_portfolio(session, pid)
    finally:
        await engine.dispose()


async def _run_reaper() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await reap_stalled_builds(session)
            logger.info("packs.reap_stalled_builds", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.packs.build_evidence_pack")  # type: ignore[untyped-decorator]
def build_evidence_pack(pack_id: str) -> None:
    """Assemble + seal one evidence pack (idempotent on retry; fail-closed)."""
    asyncio.run(_run_build(pack_id))


@app.task(name="easysynq.packs.reap_stalled_builds")  # type: ignore[untyped-decorator]
def reap_stalled_builds_task() -> dict[str, int]:
    """Flip packs stuck in BUILDING past the stall window → FAILED; returns ``{reaped}``."""
    return asyncio.run(_run_reaper())
