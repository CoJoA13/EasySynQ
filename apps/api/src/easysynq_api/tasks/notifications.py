"""Celery/Beat tasks for notifications: outbox drain (S-notify-1) + digest sweep (S-notify-3a)."""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.notifications.digest import sweep_digests
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


async def _run_digest_sweep() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        summary = await sweep_digests(sm, settings, datetime.datetime.now(datetime.UTC))
        logger.info("notifications.digest_sweep", extra={"extra_fields": summary})
        return summary
    finally:
        await engine.dispose()


@task(name="easysynq.notifications.digest_sweep")
def digest_sweep() -> dict[str, int]:
    """Bundle each due user's pending notifications into ONE summary email (hourly)."""
    return asyncio.run(_run_digest_sweep())
