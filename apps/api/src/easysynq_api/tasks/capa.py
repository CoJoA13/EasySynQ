"""Celery/Beat task for the capa.overdue sweep (S-capa-overdue)."""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.capa.overdue import sweep_capa_overdue
from .app import task

logger = logging.getLogger("easysynq.capa.tasks")


async def _run() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        summary = await sweep_capa_overdue(sessionmaker, datetime.datetime.now(datetime.UTC))
        logger.info("capa.overdue_sweep", extra={"extra_fields": summary})
        return summary
    finally:
        await engine.dispose()


@task(name="easysynq.capa.overdue_sweep")
def capa_overdue_sweep() -> dict[str, int]:
    """Daily sweep; returns {capas, notifications, skipped_non_working}."""
    return asyncio.run(_run())
