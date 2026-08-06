"""Operator-grade in-place upgrade (slice S11, doc 18 §7 / §2 line 128).

``easysynq upgrade`` enforces **pre-upgrade archive → migrate → readiness health-gate**, with an
honest rollback posture:

* **Pre-upgrade archive** — a durable archive is written FIRST (``build_durable_backup``); failure
  ABORTS the upgrade. It carries a database dump + blob manifest but no object bytes, so it is not a
  self-contained recovery set and cannot authorize production upgrade eligibility.
* **Migrate** — ``alembic upgrade head`` runs as the OWNER role (the env.py DSN = ``sync_dsn``). A
  single Alembic migration runs in one transaction that auto-rolls-back on error — that is the
  honest meaning of "rollback" for a failed migration step.
* **Health-gate** — ``readiness.check_all()`` must be green (esp. the alembic-at-head probe).

    RECOVERY LIMIT: a failed migration auto-rolls back its own transaction, but a readiness failure
    has no supported archive-to-cutover path. ``UPGRADE_FAILED.after`` preserves the exact archive
    pointer for investigation. Keep the service closed and preserve the source object store; do not
    treat that non-self-contained archive as a disaster safety net.

Runs on the worker (OWNER DSN + pg client). Audits via the app session like the backup service.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import get_settings
from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.audit_event import AuditEvent
from ..db.models.backup_policy import BackupPolicy
from ..logging import request_id_var
from ..readiness import MIGRATIONS_DIR, check_all
from .backup import build_durable_backup

logger = logging.getLogger("easysynq.upgrade")


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
    after: dict[str, Any],
) -> None:
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


def _alembic_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _run_alembic_upgrade() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    command.upgrade(cfg, "head")  # env.py resolves the owner DSN from settings.sync_dsn


async def _backup_destination(session: AsyncSession, org_id: uuid.UUID) -> str:
    from sqlalchemy import select

    policy = await session.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
    return policy.destination if policy is not None else get_settings().backup_path


async def _close_session_best_effort(session: AsyncSession, *, context: str) -> None:
    """Close an upgrade session without replacing its primary result or pending exception."""
    try:
        await session.close()
    except Exception:
        logger.exception("upgrade: session close failed while finalizing %s", context)


async def run_upgrade(org_id: uuid.UUID, actor_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Pre-upgrade archive → migrate → health-gate. Returns ``{result: OK|FAILED, ...}``.

    ``stage`` is one of ``pre_backup`` | ``migrate`` | ``health_gate`` | ``orchestration``; the last
    covers a failure outside the three guarded stages (see the outer handler). A ``pre_backup``
    failure includes an archive that was written but did NOT pass its own checksum verification —
    an invalid pre-upgrade artifact cannot authorize migration mechanics. Ordinary operational
    exceptions become a structured ``FAILED`` result; cancellation and process-exit signals still
    propagate.
    """
    # ⚠ Setup lives INSIDE the protected boundary. `get_settings()` (a malformed DSN or a missing
    # required field), `create_async_engine()` (an unparseable URL / bad driver) and the
    # sessionmaker can each raise, and above the `try:` they escaped exactly like the stage bodies
    # once did — the same defect one scope out. `sessionmaker` may therefore still be None in the
    # handler, in which case no audit row is possible (there is no database to write to) and the
    # structured FAILED dict is the whole of the honest answer.
    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None
    pre_backup_archive: str | None = None
    try:
        settings = get_settings()
        engine = create_async_engine(settings.database_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        target_head = _alembic_head()
        session = sessionmaker()
        try:
            destination = await _backup_destination(session, org_id)
            _emit(
                session,
                org_id=org_id,
                actor_id=actor_id,
                event_type="UPGRADE_STARTED",
                after={"target_head": target_head, "destination": destination},
            )
            await session.commit()

            # 1. required pre-upgrade archive check — abort the upgrade if it fails. This archive is
            # non-self-contained (no object bytes) and does not establish recovery eligibility.
            #
            # ⚠ The catch is deliberately BROAD. It was `except BackupError` and that is far too
            # narrow: the canonical pre-upgrade archive failures do not raise BackupError at all —
            # `dest_dir.mkdir()` (drill.py) and `dest_enc.write_bytes()` (crypto.py) raise OSError /
            # PermissionError on a full or read-only backup mount, `psycopg.connect()` raises
            # OperationalError, and BackupCryptoError is a SIBLING of BackupError, not a subclass.
            # Each of those escaped `run_upgrade` entirely, breaking its "never raises" contract and
            # leaving the UPGRADE_STARTED row above with no terminal event in an append-only chain.
            # `services/backup/service.py:212` already made this exact call for the nightly path.
            try:
                backup = await asyncio.to_thread(
                    build_durable_backup, settings, destination=destination
                )
            except Exception as exc:
                logger.exception("upgrade: pre-upgrade archive failed")
                reason = f"{type(exc).__name__}: {exc}"[:300]
                _emit(
                    session,
                    org_id=org_id,
                    actor_id=actor_id,
                    event_type="UPGRADE_FAILED",
                    after={"stage": "pre_backup", "error": reason},
                )
                await session.commit()
                return {"result": "FAILED", "stage": "pre_backup", "reason": reason}

            # 1b. the archive must have PASSED its own checksum verification.
            #
            # ⚠ `build_durable_backup` reports a checksum mismatch by RETURNING verified=False,
            # never by raising — so the handler above cannot see it, and without this check the
            # upgrade migrates the live database against an archive already known to be unusable,
            # then points UPGRADE_FAILED.pre_backup_archive (the operator's preserved investigation
            # pointer) at that same dead file. Fail CLOSED on a missing key: an unreportable check
            # is not a pass. This mirrors `services/backup/service.py:197-209`, which hardened the
            # nightly path against precisely this scenario; the guard was never propagated here.
            # (Folding both into one shared typed validator is deferred to the full S-upgrade-safety
            # slice — see docs/superpowers/plans/2026-08-04-audit-remediation-v2.md.)
            if not backup.get("verified", False):
                detail = (
                    "pre-upgrade archive written but FAILED checksum verification: "
                    f"{backup.get('archive')}"
                )
                logger.error("upgrade.pre_backup.unverified", extra={"extra_fields": backup})
                _emit(
                    session,
                    org_id=org_id,
                    actor_id=actor_id,
                    event_type="UPGRADE_FAILED",
                    after={"stage": "pre_backup", "error": detail[:300]},
                )
                await session.commit()
                return {"result": "FAILED", "stage": "pre_backup", "reason": detail[:300]}

            pre_backup_archive = str(backup["archive"])

            # 2. migrate (a failed migration auto-rolls-back its own txn)
            try:
                await asyncio.to_thread(_run_alembic_upgrade)
            except Exception as exc:
                logger.exception("upgrade: alembic upgrade failed")
                _emit(
                    session,
                    org_id=org_id,
                    actor_id=actor_id,
                    event_type="UPGRADE_FAILED",
                    after={
                        "stage": "migrate",
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                        "pre_backup_archive": pre_backup_archive,
                    },
                )
                await session.commit()
                return {
                    # Keep the exception-bearing response consistent with its audit row: the class
                    # is useful to an operator, and bare str() drops it for empty-message errors.
                    "result": "FAILED",
                    "stage": "migrate",
                    "reason": f"{type(exc).__name__}: {exc}"[:300],
                    "pre_backup_archive": pre_backup_archive,
                }

            # 3. readiness health-gate
            deps = await check_all()
            unhealthy = [d for d in deps if not d["ready"]]
            if unhealthy:
                _emit(
                    session,
                    org_id=org_id,
                    actor_id=actor_id,
                    event_type="UPGRADE_FAILED",
                    after={
                        "stage": "health_gate",
                        "unhealthy": unhealthy,
                        "pre_backup_archive": pre_backup_archive,
                    },
                )
                await session.commit()
                return {
                    "result": "FAILED",
                    "stage": "health_gate",
                    "unhealthy": unhealthy,
                    "pre_backup_archive": pre_backup_archive,
                }

            _emit(
                session,
                org_id=org_id,
                actor_id=actor_id,
                event_type="UPGRADE_COMPLETED",
                after={"head": target_head, "pre_backup_archive": pre_backup_archive},
            )
            await session.commit()
            return {
                "result": "OK",
                "head": target_head,
                "pre_backup_archive": pre_backup_archive,
            }
        finally:
            # SQLAlchemy's context-manager __aexit__ delegates to close(), whose exception would
            # otherwise replace either the return above or a pending CancelledError/SystemExit.
            await _close_session_best_effort(session, context="the upgrade body")
    except Exception as exc:
        # ⚠ The three guarded stages above are not the whole function. `_alembic_head()`, the
        # destination lookup, every `_emit`/`commit`, and `check_all()` all sit OUTSIDE them, and
        # this outer block previously carried only a `finally:` — so any of them escaped and the
        # docstring's "Never raises" was simply untrue. That matters concretely: `cli/upgrade.py`
        # does not wrap this call, so an escape hands the operator a traceback instead of a stage
        # and the preserved pre-upgrade artifact pointer, and strands UPGRADE_STARTED with no
        # terminal event.
        logger.exception("upgrade: unexpected failure outside a guarded stage")
        reason = f"{type(exc).__name__}: {exc}"[:300]
        if sessionmaker is not None:
            await _try_emit_orchestration_failure(
                sessionmaker,
                org_id=org_id,
                actor_id=actor_id,
                reason=reason,
                pre_backup_archive=pre_backup_archive,
            )
        out: dict[str, Any] = {"result": "FAILED", "stage": "orchestration", "reason": reason}
        if pre_backup_archive is not None:
            out["pre_backup_archive"] = pre_backup_archive
        return out
    finally:
        # ⚠ Cleanup must not become the result. A bare `await engine.dispose()` here raises straight
        # out of the function and REPLACES an already-computed OK/FAILED return — so a pool-teardown
        # hiccup could report a successful upgrade as an exception, which is the worst possible
        # direction for this command. Disposal failure is logged and otherwise ignored: by the time
        # it runs, the protected body has already determined the primary result or exception.
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:
                logger.exception("upgrade: engine disposal failed after the upgrade concluded")


async def _try_emit_orchestration_failure(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    reason: str,
    pre_backup_archive: str | None,
) -> None:
    """Best-effort terminal audit row for a failure outside the guarded stages.

    Opens its OWN session: the caller's may be mid-transaction or bound to a dead connection, and a
    session reused across a failure is the repo's documented ``MissingGreenlet``-at-pool-teardown
    trap. Swallows its own ordinary errors deliberately — if the database is the thing that broke,
    the caller must still receive an honest ``FAILED`` dict rather than a second exception. An audit
    UNDER-claim is the safe direction for an append-only chain (the R64 precedent). Cancellation and
    process-exit signals still propagate.
    """
    session: AsyncSession | None = None
    try:
        session = sessionmaker()
        after: dict[str, Any] = {"stage": "orchestration", "error": reason}
        if pre_backup_archive is not None:
            after["pre_backup_archive"] = pre_backup_archive
        _emit(
            session,
            org_id=org_id,
            actor_id=actor_id,
            event_type="UPGRADE_FAILED",
            after=after,
        )
        await session.commit()
    except Exception:
        logger.exception("upgrade: could not record the orchestration-failure audit row")
    finally:
        if session is not None:
            await _close_session_best_effort(session, context="orchestration-failure auditing")
