"""Celery/Beat tasks for the tamper-evident audit trail (slice S6, R12/R13, doc 12 §4).

* ``chain_link`` — the decoupled chain-linker; runs continuously (~30 s) under a PG advisory lock,
  as the dedicated ``easysynq_linker`` role (the only role that may stamp the hash columns).
* ``verify_chain`` — nightly re-walk; alarms on the first broken link.
* ``roll_partitions`` — daily; keeps the rolling monthly-partition runway ≥2 months ahead.

Each task uses its own disposed async engine so a fresh event loop per ``asyncio.run`` is safe
(reusing the S4 ``release_due`` idiom). The linker connects with the LINKER DSN; verify/rotation use
the app DSN.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models.organization import Organization
from ..services.audit.checkpoint import anchor_checkpoint, load_signing_key
from ..services.audit.linker import link_all
from ..services.audit.partitions import ensure_partitions
from ..services.audit.verify import verify_chain
from ..services.common.pg_locks import LOCK_CHAIN_LINK, pg_advisory_lock
from .app import app

logger = logging.getLogger("easysynq.audit.tasks")


async def _run_chain_link() -> int:
    engine = create_async_engine(get_settings().audit_linker_database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_CHAIN_LINK) as held:
            if not held:
                logger.debug("audit.chain_link: another linker holds the lock; skipping")
                return 0
            result = await link_all(session)
            return result.linked
    finally:
        await engine.dispose()


async def _run_verify_chain() -> int:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    total_breaks = 0
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                result = await verify_chain(session, org_id)
                total_breaks += len(result.breaks)
                if not result.verified:
                    logger.error(
                        "audit.verify_chain.broken",
                        extra={
                            "extra_fields": {
                                "org_id": str(org_id),
                                "first_break_at_id": result.breaks[0].at_id,
                                "break_count": len(result.breaks),
                            }
                        },
                    )
        return total_breaks
    finally:
        await engine.dispose()


async def _run_roll_partitions() -> list[str]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            return await ensure_partitions(session)
    finally:
        await engine.dispose()


async def _run_checkpoint_anchor() -> int:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    signing_key = load_signing_key()
    anchored = 0
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                if await anchor_checkpoint(session, org_id, signing_key=signing_key) is not None:
                    anchored += 1
    finally:
        await engine.dispose()
    return anchored


@app.task(name="easysynq.audit.chain_link")  # type: ignore[untyped-decorator]
def chain_link() -> int:
    """Link unchained audit rows; returns the count linked this run."""
    return asyncio.run(_run_chain_link())


@app.task(name="easysynq.audit.verify_chain")  # type: ignore[untyped-decorator]
def verify_chain_task() -> int:
    """Re-walk + verify the chain; returns the number of broken links (0 = intact)."""
    return asyncio.run(_run_verify_chain())


@app.task(name="easysynq.audit.roll_partitions")  # type: ignore[untyped-decorator]
def roll_partitions() -> list[str]:
    """Ensure the rolling monthly-partition runway; returns the month labels ensured."""
    return asyncio.run(_run_roll_partitions())


@app.task(name="easysynq.audit.checkpoint_anchor")  # type: ignore[untyped-decorator]
def checkpoint_anchor() -> int:
    """Write + mirror a signed checkpoint per org; returns the count anchored."""
    return asyncio.run(_run_checkpoint_anchor())
