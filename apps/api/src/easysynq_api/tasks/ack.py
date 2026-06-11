"""Celery/Beat task for the S-ack-1 acknowledgement sweep (doc 04 §8.2/§8.3, R15/R43)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.ack.sweep import sweep_acks
from .app import app

logger = logging.getLogger("easysynq.ack.tasks")


async def _run_ack_sweep(document_id: str | None, trigger: str | None) -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await sweep_acks(
                session,
                document_id=uuid.UUID(document_id) if document_id else None,
                trigger=trigger,
            )
            logger.info("ack.sweep", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.ack.sweep")  # type: ignore[untyped-decorator]
def ack_sweep(document_id: str | None = None, trigger: str | None = None) -> dict[str, int]:
    """The daily (or doc-scoped, post-release/post-distribution) acknowledgement sweep."""
    return asyncio.run(_run_ack_sweep(document_id, trigger))
