"""Celery task for the S-dcr-3b visual page-image diff (doc 05 §8.1).

``easysynq.visual_diff`` renders (the worker's ``GotenbergRenderSink`` — the API can't render) +
rasterizes + diffs two versions and caches the page comparisons, flipping the ``visual_diff`` row
Pending → Ready / Unavailable / Failed. Idempotent: ``build_visual_diff`` takes the row ``FOR
UPDATE`` and early-returns on a terminal status, so ``task_acks_late`` re-delivery (or a re-POST)
is safe — a transient renderer outage propagates (the row stays Pending) and the redelivery /
re-POST retries.
Its own disposed async engine per ``asyncio.run`` (the ``mirror_sync`` / ``release_due`` idiom)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.diff.visual import build_visual_diff
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


@task(name="easysynq.visual_diff")
def visual_diff(visual_diff_id: str) -> None:
    """Build the cached page-image comparison for a ``visual_diff`` row."""
    asyncio.run(_run(uuid.UUID(visual_diff_id)))
