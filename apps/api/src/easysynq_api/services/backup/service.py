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
import posixpath
import uuid
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.audit_event import AuditEvent
from ...db.models.backup_policy import BackupPolicy
from ...logging import request_id_var
from ..common.pg_locks import LOCK_RESTORE_DRILL, LOCK_RESTORE_LIVE, pg_advisory_lock
from ..notifications.constants import EVENT_BACKUP_FAILED
from ..notifications.ops_channel import OperatorAlert, send_operator_alert
from ..notifications.ops_events import emit_backup_failed
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
    """Preliminary API-process probe for an absolute non-root POSIX filesystem destination.

    This rejects relative and URI-looking values before any filesystem call. A success proves only
    that the API process can create/write/remove at this path; it does not prove that the worker
    sees the same mount or that the path is backed by persistent/off-host storage.
    """
    if (
        destination.strip("/") == ""
        or posixpath.normpath(destination) == "/"
        or "://" in destination
        or not PurePosixPath(destination).is_absolute()
    ):
        return False, "destination must be an absolute non-root POSIX filesystem path"
    try:
        os.makedirs(destination, exist_ok=True)
        probe = os.path.join(destination, f".easysynq-write-probe-{uuid.uuid4().hex}")
        with open(probe, "wb") as f:
            f.write(b"easysynq")
        os.remove(probe)
    except (OSError, ValueError) as exc:
        return False, f"destination not writable: {exc}"[:200]
    return True, "preliminary API-context create/write/remove probe passed"


async def _report_backup_failure(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    destination: str,
    error: str,
) -> None:
    """Batch 11 (review 2026-07-22 finding 2): make a failed nightly backup *visible*.

    Before this, the only trace of "every nightly backup has failed for three weeks" was a worker
    log line nobody reads — discovered when a restore was needed and the newest archive was weeks
    old. Three carriers now, deliberately independent:

    1. a durable ``BACKUP_FAILED`` audit row (the in-DB record an auditor can find later),
    2. ``system.backup_failed`` to the org's System Administrators (in-app + email),
    3. the OUT-OF-BAND operator channel, which needs no database at all.

    Never raises: this runs inside the per-org failure handler, and a reporting problem must not
    abort the remaining orgs' backups or mask the original ``BackupError``. A fresh session per
    call (the S-ing-5 rule) so a failed report cannot poison the next org's.
    """
    settings = get_settings()
    now = _now()
    try:
        async with sessionmaker() as session:
            session.add(
                AuditEvent(
                    org_id=org_id,
                    occurred_at=now,
                    actor_id=None,
                    actor_type=ActorType.system,
                    event_type=EventType.BACKUP_FAILED,
                    object_type=AuditObjectType.config,
                    object_id=org_id,
                    after={"destination": destination, "error": error},
                    request_id=_maybe_uuid(request_id_var.get()),
                )
            )
            await emit_backup_failed(
                session, org_id=org_id, destination=destination, error=error, now=now
            )
            await session.commit()
    except Exception:  # reporting must never abort the sweep or mask the real error
        logger.warning(
            "backup.failure_report_failed",
            exc_info=True,
            extra={"extra_fields": {"org": str(org_id)}},
        )
    # Fired unconditionally, and OUTSIDE the session block: if the in-DB half above just failed
    # because PostgreSQL is unreachable, this is the only carrier left.
    await send_operator_alert(
        settings,
        OperatorAlert(
            event=EVENT_BACKUP_FAILED,
            severity="error",
            summary="scheduled EasySynQ backup failed",
            detail={"destination": destination, "error": error},
            org_id=str(org_id),
        ),
    )


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
        try:
            async with sessionmaker() as session:
                policies = (await session.scalars(select(BackupPolicy))).all()
        except Exception as exc:
            # THE mode the in-DB path structurally cannot report (the finding's core point): with
            # PostgreSQL down there is no policy list, no admin to resolve, no notification to
            # insert and no audit row to append — the nightly job simply dies into a log. The
            # out-of-band channel is the only carrier. Re-raise afterwards so Celery still records
            # a failed task rather than a silent no-op run.
            logger.exception("backup.run could not read backup_policy")
            await send_operator_alert(
                settings,
                OperatorAlert(
                    event=EVENT_BACKUP_FAILED,
                    severity="critical",
                    summary="scheduled backup could not start — the EasySynQ database is "
                    "unreachable, so NO backup ran and no in-app alert could be raised",
                    detail={"error": str(exc)[:500]},
                ),
            )
            raise
        for policy in policies:
            try:
                out = await asyncio.to_thread(
                    drill.build_durable_backup, settings, destination=policy.destination
                )
                # An archive that does not match its own .sha256 sidecar is exactly as useless for a
                # restore as one that was never written — and build_durable_backup reports that by
                # RETURNING verified=False, not by raising, so the exception handler below never
                # sees it. Without this check the sweep logs backup.run.done and reports success
                # over an unusable archive: the finding's whole scenario, one layer down.
                # Fail-CLOSED on a missing key: an unreportable verification is not a pass.
                if not out.get("verified", False):
                    detail = (
                        f"archive written but FAILED checksum verification: {out.get('archive')}"
                    )
                    logger.error("backup.run.unverified", extra={"extra_fields": out})
                    await _report_backup_failure(
                        sessionmaker,
                        org_id=policy.org_id,
                        destination=policy.destination,
                        error=detail[:200],
                    )
                    results.append({"org_id": str(policy.org_id), **out, "error": detail[:200]})
                    continue
                logger.info("backup.run.done", extra={"extra_fields": out})
                results.append({"org_id": str(policy.org_id), **out})
            except Exception as exc:
                logger.exception("backup.run failed for org %s", policy.org_id)
                await _report_backup_failure(
                    sessionmaker,
                    org_id=policy.org_id,
                    destination=policy.destination,
                    error=str(exc)[:200],
                )
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


async def verify_latest_retained_backup(
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    *,
    after_restore: Callable[[ScratchHandle], None] | None = None,
) -> dict[str, Any]:
    """Verify the NEWEST RETAINED archive for ``org_id`` is scratch-verifiable and intact relative
    to the configured source object store, then persist the result. This scheduled (Phase-1 I-7)
    check opens the actual stored ``easysynq-backup-*.tar[.enc]`` instead of the fresh transient
    archive built by the on-demand G-C drill, so silent archive rot is caught. It does not prove
    source-independent recovery (Codex P2, #155).

    Serialized on ``LOCK_RESTORE_DRILL`` (shared with the on-demand drill — they contend for the
    same pg_restore/scratch resource family, so a concurrent drill or verify SKIPs). No policy →
    FAIL (matches ``run_restore_test``). The verify itself runs off the event loop as the OWNER role
    inside ``drill.verify_retained_archive``; this session only persists + audits.

    Reuses ``last_restore_test_result`` + the RESTORE_TEST_PASSED/_FAILED audit (no new column),
    distinguished by ``source: "scheduled_retained_verify"`` (+ the archive filename) in the audit
    ``after`` so an auditor can tell a scheduled retained-verify from the on-demand G-C drill. A
    SKIPPED result (no archive yet, or the lock is held) persists + audits NOTHING (just logs) — a
    fresh install with no nightly run must not flap red. ``after_restore`` is a TEST-ONLY fault
    injector forwarded to the verifier."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_RESTORE_DRILL) as held:
            if not held:
                logger.info("retained-verify: another drill holds the lock; skipping")
                return {"result": "SKIPPED", "reason": "another restore-test is in progress"}
            policy = await session.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
            if policy is None:
                logger.warning("retained-verify: no backup policy for org %s", org_id)
                return {"result": "FAIL", "reason": "no backup policy configured"}

            result = await asyncio.to_thread(
                drill.verify_retained_archive,
                settings,
                destination=policy.destination,
                after_restore=after_restore,
            )
            if result.result == "SKIPPED":
                logger.info(
                    "retained-verify: skipped",
                    extra={"extra_fields": {"reason": result.reason}},
                )
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
                after={
                    "reason": result.reason,
                    "source": "scheduled_retained_verify",
                    **result.details,
                },
            )
            await session.commit()
            logger.info("retained-verify.done", extra={"extra_fields": {"result": result.result}})
            return {"result": result.result, "reason": result.reason, "details": result.details}
    finally:
        await engine.dispose()


async def run_scheduled_restore_tests() -> dict[str, Any]:
    """Verify the newest retained archive for every configured ``backup_policy`` (one per org;
    single-org in MVP, D1) on the Beat cadence. The check decrypts and restores the database into
    scratch, then validates manifested locators and hashes against the configured source object
    store. It catches archive and current-source corruption between manual G-C drills, but does not
    prove source-independent recovery. Delegates per org to ``verify_latest_retained_backup`` (its
    ``LOCK_RESTORE_DRILL`` serialization + persisted ``last_restore_test_result`` +
    RESTORE_TEST_PASSED/_FAILED audit + never-raise contract) with a SYSTEM actor. Best-effort: one
    org's failure never aborts the others, and the verify itself never raises (an honest FAIL is
    persisted + audited)."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            org_ids = list((await session.scalars(select(BackupPolicy.org_id))).all())
    finally:
        await engine.dispose()
    # Each verify_latest_retained_backup opens + disposes its own engine/session (a fresh unit per
    # org), so the org-id read above is closed first — never one session reused across the per-org
    # verifies.
    results: list[dict[str, Any]] = []
    for org_id in org_ids:
        try:
            out = await verify_latest_retained_backup(org_id, actor_id=None)
            results.append({"org_id": str(org_id), **out})
        except Exception as exc:
            logger.exception("scheduled backup-verify errored for org %s", org_id)
            results.append({"org_id": str(org_id), "result": "FAIL", "error": str(exc)[:200]})
    logger.info("backup-verify.scheduled.done", extra={"extra_fields": {"orgs": len(results)}})
    return {"restore_tests": results}


async def run_restore(
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    *,
    archive_path: str,
    audit_checkpoint_ack: bool = False,
    fetch_off_host: restore.FetchOffHost | None = None,
    after_restore: Callable[[ScratchHandle], None] | None = None,
) -> dict[str, Any]:
    """Operator restore integrity verification (S11, R37). Serialized on
    ``LOCK_RESTORE_LIVE`` (distinct from the drill lock). Emits RESTORE_STARTED then one of
    RESTORE_VERIFIED / RESTORE_CHECKPOINT_AHEAD / RESTORE_FAILED (+ an audited
    RESTORE_CHECKPOINT_ACK when a flagged restore proceeds under operator ack). The pg/blob work
    runs as the
    OWNER role inside ``restore.run_restore``; this session only audits + commits. Returns
    ``{result, reason, scratch_db, ...}`` — only PASS leaves a standing verification target. PASS
    still depends on the configured source object store and never authorizes cutover."""
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
