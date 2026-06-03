"""Async orchestration for the restore-test drill (slice S8b2): take the drill advisory lock, run
the (sync, owner-role) drill off the event loop, persist the result to ``backup_policy``, emit the
RESTORE_TEST_PASSED/_FAILED audit row, and commit — all on the runtime ``easysynq_app`` session.

Finalize/G-C read the persisted ``last_restore_test_result``; this is the only writer of it. The
worker task wraps ``run_restore_test`` in ``asyncio.run``; the integration test awaits it directly
(no broker). The drill's heavy lifting (pg_dump/pg_restore + scratch DB + blob copy) runs as the
OWNER role inside ``run_drill`` — this session only persists + audits.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.audit_event import AuditEvent
from ...db.models.backup_policy import BackupPolicy
from ...logging import request_id_var
from ..common.pg_locks import LOCK_RESTORE_DRILL, LOCK_RESTORE_LIVE, pg_advisory_lock
from . import drill, restore
from .drill import ScratchHandle

logger = logging.getLogger("easysynq.backup")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _maybe_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _emit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    event_type: str,
    after: dict[str, Any] | None,
) -> None:
    """Append a RESTORE_TEST_* audit row (object_type ``config``), committing atomically with the
    persisted result. A drill triggered by an admin records that actor; the nightly/CLI path records
    a system actor. Hashes stay NULL until the chain-linker fills them (R12)."""
    session.add(
        AuditEvent(
            org_id=org_id,
            occurred_at=_now(),
            actor_id=actor_id,
            actor_type=ActorType.user if actor_id is not None else ActorType.system,
            event_type=EventType(event_type),
            object_type=AuditObjectType.config,
            object_id=org_id,
            after=after,
            request_id=_maybe_uuid(request_id_var.get()),
        )
    )


def configure_backup_destination_check(destination: str) -> tuple[bool, str]:
    """Sync reachability/writability probe for a filesystem backup destination (doc 08 §8.1).
    Creates the directory if absent, writes + removes a probe file. Returns (ok, detail)."""
    try:
        os.makedirs(destination, exist_ok=True)
        probe = os.path.join(destination, f".easysynq-write-probe-{uuid.uuid4().hex}")
        with open(probe, "wb") as f:
            f.write(b"easysynq")
        os.remove(probe)
    except OSError as exc:
        return False, f"destination not writable: {exc}"[:200]
    return True, "destination reachable and writable"


async def run_scheduled_backups() -> dict[str, Any]:
    """Write a durable backup archive for every configured ``backup_policy`` (one per org;
    single-org in MVP, D1). The nightly Beat job + ``easysynq backup run`` target. Best-effort +
    logged: one org's failure does not abort the others (the drill, not this, is the gating)."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    results: list[dict[str, Any]] = []
    try:
        async with sessionmaker() as session:
            policies = (await session.scalars(select(BackupPolicy))).all()
        for policy in policies:
            try:
                out = await asyncio.to_thread(
                    drill.build_durable_backup, settings, destination=policy.destination
                )
                logger.info("backup.run.done", extra={"extra_fields": out})
                results.append({"org_id": str(policy.org_id), **out})
            except Exception as exc:
                logger.exception("backup.run failed for org %s", policy.org_id)
                results.append({"org_id": str(policy.org_id), "error": str(exc)[:200]})
        return {"backups": results}
    finally:
        await engine.dispose()


async def run_restore_test(
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    *,
    after_restore: Callable[[ScratchHandle], None] | None = None,
) -> dict[str, Any]:
    """Run the backup→restore-into-scratch drill (gate G-C / AC#5) and persist the result.
    Serialized on ``LOCK_RESTORE_DRILL`` (a second concurrent drill skips — so the stale-scratch
    sweep is safe). Returns ``{result, reason, details}``; only PASS satisfies G-C.
    ``after_restore`` is a TEST-ONLY fault injector forwarded to the drill."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_RESTORE_DRILL) as held:
            if not held:
                logger.info("restore-test: another drill holds the lock; skipping")
                return {"result": "SKIPPED", "reason": "another restore-test is in progress"}
            policy = await session.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
            if policy is None:
                logger.warning("restore-test: no backup policy for org %s", org_id)
                return {"result": "FAIL", "reason": "no backup policy configured"}

            result = await asyncio.to_thread(
                drill.run_drill,
                settings,
                destination=policy.destination,
                after_restore=after_restore,
            )
            if result.result == "SKIPPED":  # pragma: no cover - run_drill never returns SKIPPED
                return {"result": result.result, "reason": result.reason}

            policy.last_restore_test_at = _now()
            policy.last_restore_test_result = result.result
            _emit(
                session,
                org_id=org_id,
                actor_id=actor_id,
                event_type=(
                    "RESTORE_TEST_PASSED" if result.result == "PASS" else "RESTORE_TEST_FAILED"
                ),
                after={"reason": result.reason, **result.details},
            )
            await session.commit()
            logger.info("restore-test.done", extra={"extra_fields": {"result": result.result}})
            return {"result": result.result, "reason": result.reason, "details": result.details}
    finally:
        await engine.dispose()


async def run_restore(
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    *,
    archive_path: str,
    audit_checkpoint_ack: bool = False,
    fetch_off_host: restore.FetchOffHost | None = None,
    after_restore: Callable[[ScratchHandle], None] | None = None,
) -> dict[str, Any]:
    """Operator-grade live WORM-aware restore-to-verified-target (S11, R37). Serialized on
    ``LOCK_RESTORE_LIVE`` (distinct from the drill lock). Emits RESTORE_STARTED then one of
    RESTORE_VERIFIED / RESTORE_CHECKPOINT_AHEAD / RESTORE_FAILED (+ an audited
    RESTORE_CHECKPOINT_ACK when a flagged restore proceeds under operator ack). The pg/blob work
    runs as the
    OWNER role inside ``restore.run_restore``; this session only audits + commits. Returns
    ``{result, reason, scratch_db, ...}`` — only PASS leaves a standing, ready-to-cutover target."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_RESTORE_LIVE) as held:
            if not held:
                return {"result": "SKIPPED", "reason": "another restore is in progress"}
            _emit(
                session,
                org_id=org_id,
                actor_id=actor_id,
                event_type="RESTORE_STARTED",
                after={"archive": archive_path},
            )
            await session.commit()

            result = await asyncio.to_thread(
                restore.run_restore,
                settings,
                archive_path=archive_path,
                audit_checkpoint_ack=audit_checkpoint_ack,
                fetch_off_host=fetch_off_host,
                after_restore=after_restore,
            )

            after = {
                "reason": result.reason,
                "scratch_db": result.scratch_db,
                "checkpoint": result.checkpoint_check,
                "chain": result.chain_verify,
                "triad": result.triad,
                **result.details,
            }
            if result.result == "PASS":
                if result.checkpoint_check.get("acknowledged"):
                    _emit(
                        session,
                        org_id=org_id,
                        actor_id=actor_id,
                        event_type="RESTORE_CHECKPOINT_ACK",
                        after={"checkpoint": result.checkpoint_check},
                    )
                _emit(
                    session,
                    org_id=org_id,
                    actor_id=actor_id,
                    event_type="RESTORE_VERIFIED",
                    after=after,
                )
            elif result.result == "FLAGGED":
                _emit(
                    session,
                    org_id=org_id,
                    actor_id=actor_id,
                    event_type="RESTORE_CHECKPOINT_AHEAD",
                    after=after,
                )
            else:
                _emit(
                    session,
                    org_id=org_id,
                    actor_id=actor_id,
                    event_type="RESTORE_FAILED",
                    after=after,
                )
            await session.commit()
            logger.info("restore.done", extra={"extra_fields": {"result": result.result}})
            return {
                "result": result.result,
                "reason": result.reason,
                "scratch_db": result.scratch_db,
                "scratch_bucket": result.scratch_bucket,
                "object_prefix": result.object_prefix,
                "checkpoint": result.checkpoint_check,
                "chain": result.chain_verify,
                "triad": result.triad,
                "details": result.details,
            }
    finally:
        await engine.dispose()
