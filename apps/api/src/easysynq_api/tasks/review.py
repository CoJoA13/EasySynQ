"""Celery/Beat task for the D5 periodic re-review sweep (S-drift-1, doc 04 §9.1)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.vault.review import sweep_reviews
from .app import app

logger = logging.getLogger("easysynq.documents.tasks")


async def _run_review_sweep() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await sweep_reviews(session)
            logger.info("documents.review_sweep", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.documents.review_sweep")  # type: ignore[untyped-decorator]
def review_sweep() -> dict[str, int]:
    """Daily D5 sweep; returns ``{tasks_created, escalated}``."""
    return asyncio.run(_run_review_sweep())
