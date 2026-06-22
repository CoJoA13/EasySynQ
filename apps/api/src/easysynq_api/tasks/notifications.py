"""Celery/Beat task for the S-notify-1 email outbox drain (doc 10 §9, R53)."""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.notifications.drain import drain_once
from ..services.notifications.mail import SmtpMailSender
from .app import task

logger = logging.getLogger("easysynq.notifications.tasks")


async def _run_drain() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    sender = SmtpMailSender(settings)
    try:
        async with sessionmaker() as session:
            summary = await drain_once(
                session, sender, settings, now=datetime.datetime.now(datetime.UTC)
            )
            logger.info("notifications.outbox_drain", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@task(name="easysynq.notifications.outbox_drain")
def outbox_drain() -> dict[str, int]:
    """Send queued notification emails (every ~120 s)."""
    return asyncio.run(_run_drain())
