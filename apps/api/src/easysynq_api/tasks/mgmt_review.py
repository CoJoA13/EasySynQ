"""Celery/Beat task for the S-mr-1 management-review cadence sweep (clause 9.3 §s6)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.mgmt_review.cadence import sweep_mgmt_reviews
from .app import task

logger = logging.getLogger("easysynq.mgmt_review.tasks")


async def _run_mgmt_review_sweep() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await sweep_mgmt_reviews(session)
            logger.info("mgmt_review.cadence_sweep", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@task(name="easysynq.documents.mgmt_review_sweep")
def mgmt_review_sweep() -> dict[str, int]:
    """Daily cadence sweep; mints the next Scheduled Management Review when the horizon is reached.
    Returns ``{mgmt_reviews_opened, skipped_open, skipped_lock_held}``."""
    return asyncio.run(_run_mgmt_review_sweep())
