"""Celery task for the S-dcr-3b visual page-image diff (doc 05 §8.1).

``easysynq.visual_diff`` renders (the worker's ``GotenbergRenderSink`` — the API can't render) +
rasterizes + diffs two versions and caches the page comparisons, flipping the ``visual_diff`` row
Pending → Ready / Unavailable / Failed. Idempotent: ``build_visual_diff`` takes the row ``FOR
UPDATE`` and early-returns on a terminal status, so ``task_acks_late`` re-delivery (or a re-POST)
is safe. A transient renderer outage propagates and the row stays Pending (a raising task is
still acked under ``task_acks_late``, so no broker retry follows) — a re-POST re-enqueues it, and
the daily ``easysynq.visual_diff.reap_stalled`` Beat reaper times it out to Failed.
Its own disposed async engine per ``asyncio.run`` (the ``mirror_sync`` / ``release_due`` idiom)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.diff.visual import build_visual_diff, reap_stalled_visual_diffs
from ..services.vault.render_gotenberg import GotenbergRenderSink
from .app import task

logger = logging.getLogger("easysynq.visual_diff.tasks")


async def _run(visual_diff_id: uuid.UUID) -> None:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            await build_visual_diff(session, visual_diff_id, GotenbergRenderSink())
    finally:
        await engine.dispose()


async def _run_reaper() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await reap_stalled_visual_diffs(session)
            logger.info("visual_diff.reap_stalled", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@task(name="easysynq.visual_diff")
def visual_diff(visual_diff_id: str) -> None:
    """Build the cached page-image comparison for a ``visual_diff`` row."""
    asyncio.run(_run(uuid.UUID(visual_diff_id)))


@task(name="easysynq.visual_diff.reap_stalled")
def reap_stalled_visual_diffs_task() -> dict[str, int]:
    """Flip visual_diff rows stuck Pending past the stall window → Failed; returns ``{reaped}``."""
    return asyncio.run(_run_reaper())
