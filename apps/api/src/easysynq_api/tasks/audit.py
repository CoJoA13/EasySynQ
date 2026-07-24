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
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.audit_event import AuditEvent
from ..db.models.organization import Organization
from ..services.audit.checkpoint import (
    anchor_checkpoint,
    load_signing_key,
    load_verify_key,
    verify_offhost_checkpoint,
)
from ..services.audit.linker import link_all
from ..services.audit.partitions import ensure_partitions
from ..services.audit.verify import VerifyResult, verify_chain
from ..services.common.pg_locks import LOCK_CHAIN_LINK, pg_advisory_lock
from .app import task

logger = logging.getLogger("easysynq.audit.tasks")


def _emit_chain_verify_fail(
    session: AsyncSession, org_id: object, result: VerifyResult, offhost_reasons: list[str]
) -> None:
    """Append a CHAIN_VERIFY_FAIL audit row (system actor, object_type ``audit``) so a detected
    tamper — whether from the in-DB walk/checkpoint or the INDEPENDENT off-host read — leaves a
    durable in-DB alarm alongside the structured log. The high-severity operator NOTIFICATION
    (``integrity.alarm``) + the out-of-band channel are wired in Batch 11 on top of this signal;
    hashes stay NULL until the chain-linker fills them (R12)."""
    reasons = sorted({b.reason for b in result.breaks} | set(offhost_reasons))
    session.add(
        AuditEvent(
            org_id=org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=None,
            actor_type=ActorType.system,
            event_type=EventType.CHAIN_VERIFY_FAIL,
            object_type=AuditObjectType.audit,
            object_id=org_id,
            after={
                "first_break_at_id": result.breaks[0].at_id if result.breaks else None,
                "break_count": len(result.breaks),
                "reasons": reasons,
                "checkpoint_reason": (
                    result.checkpoint.reason if result.checkpoint is not None else None
                ),
                "offhost_reasons": offhost_reasons,
            },
        )
    )


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
    # The nightly verify is the AUTHORITATIVE detection control (doc 12 §4.4): it holds the
    # beat-only signing key, so load_verify_key() always resolves (derives the public key) here and
    # the signed-checkpoint attestation runs — unlike the api/CLI, which may only walk.
    verify_key = load_verify_key()
    total_breaks = 0
    emitted = False
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                result = await verify_chain(session, org_id, verify_key=verify_key)
                total_breaks += len(result.breaks)
                # The INDEPENDENT off-host read-back runs whenever the chain has any verified rows
                # (checked > 0) — deliberately NOT gated on an in-DB checkpoint existing, since a
                # privileged DB owner can DELETE every in-DB audit_checkpoint row and the off-host
                # copy is then the only surviving witness. A CONFIGURED-but-failing witness alarms;
                # a MISSING witness is the R13 soft-gate's persistent 'NOT tamper-evident' warning
                # (surfaced in the UI), never a nightly CHAIN_VERIFY_FAIL.
                offhost_reasons: list[str] = []
                alarm_offhost = False
                if verify_key is not None and result.checked > 0:
                    offhost = await verify_offhost_checkpoint(
                        session, org_id, verify_key=verify_key
                    )
                    if offhost.offhost_configured:
                        offhost_reasons = offhost.reasons
                        alarm_offhost = not offhost.verified
                if not result.verified or alarm_offhost:
                    logger.error(
                        "audit.verify_chain.broken",
                        extra={
                            "extra_fields": {
                                "org_id": str(org_id),
                                "first_break_at_id": (
                                    result.breaks[0].at_id if result.breaks else None
                                ),
                                "break_count": len(result.breaks),
                                "checkpoint_reason": (
                                    result.checkpoint.reason
                                    if result.checkpoint is not None
                                    else None
                                ),
                                "offhost_reasons": offhost_reasons,
                            }
                        },
                    )
                    _emit_chain_verify_fail(session, org_id, result, offhost_reasons)
                    emitted = True
            if emitted:
                await session.commit()
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


@task(name="easysynq.audit.chain_link")
def chain_link() -> int:
    """Link unchained audit rows; returns the count linked this run."""
    return asyncio.run(_run_chain_link())


@task(name="easysynq.audit.verify_chain")
def verify_chain_task() -> int:
    """Re-walk + verify the chain; returns the number of broken links (0 = intact)."""
    return asyncio.run(_run_verify_chain())


@task(name="easysynq.audit.roll_partitions")
def roll_partitions() -> list[str]:
    """Ensure the rolling monthly-partition runway; returns the month labels ensured."""
    return asyncio.run(_run_roll_partitions())


@task(name="easysynq.audit.checkpoint_anchor")
def checkpoint_anchor() -> int:
    """Write + mirror a signed checkpoint per org; returns the count anchored."""
    return asyncio.run(_run_checkpoint_anchor())
