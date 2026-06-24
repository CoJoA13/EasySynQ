"""Celery/Beat tasks for notifications: outbox drain (S-notify-1), digest sweep (S-notify-3a),
timer sweep (S-notify-4 — reminders + overdue + escalate-to-manager), and awareness fan-out
(S-notify-5a — read-scoped doc.released fan-out)."""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..redis_client import redis_client
from ..services.notifications.digest import sweep_digests
from ..services.notifications.drain import drain_once
from ..services.notifications.escalation import sweep_task_timers
from ..services.notifications.fanout import fan_out_awareness
from ..services.notifications.mail import SmtpMailSender
from ..services.notifications.pubsub import sweep_and_publish
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


async def _run_timer_sweep() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        summary = await sweep_task_timers(sm, datetime.datetime.now(datetime.UTC))
        logger.info("notifications.timer_sweep", extra={"extra_fields": summary})
        return summary
    finally:
        await engine.dispose()


@task(name="easysynq.notifications.timer_sweep")
def timer_sweep() -> dict[str, int]:
    """Reminders + overdue + escalate-to-manager off task due dates (every ~5 min)."""
    return asyncio.run(_run_timer_sweep())


async def _run_awareness_fanout() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        summary = await fan_out_awareness(sm, datetime.datetime.now(datetime.UTC))
        logger.info("notifications.awareness_fanout", extra={"extra_fields": summary})
        return summary
    finally:
        await engine.dispose()


@task(name="easysynq.notifications.awareness_fanout")
def awareness_fanout() -> dict[str, int]:
    """Fan out pending awareness events (doc.released) to read-scoped audiences (every ~120 s)."""
    return asyncio.run(_run_awareness_fanout())


async def _run_pubsub_sweep() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    redis = redis_client(decode_responses=True)
    try:
        async with sm() as session:
            nudged = await sweep_and_publish(session, redis)
        summary = {"nudged": nudged}
        logger.info("notifications.pubsub_sweep", extra={"extra_fields": summary})
        return summary
    finally:
        await redis.aclose()
        await engine.dispose()


@task(name="easysynq.notifications.pubsub_sweep")
def pubsub_sweep() -> dict[str, int]:
    """PUBLISH a per-user SSE nudge for each freshly-committed notification (every ~10 s)."""
    return asyncio.run(_run_pubsub_sweep())
