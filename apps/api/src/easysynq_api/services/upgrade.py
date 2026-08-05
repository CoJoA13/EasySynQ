"""Operator-grade in-place upgrade (slice S11, doc 18 §7 / §2 line 128).

``easysynq upgrade`` enforces **pre-backup → migrate → readiness health-gate**, with an honest
rollback posture:

* **Pre-backup** — a durable archive is written FIRST (``build_durable_backup``); a pre-backup
  failure ABORTS the upgrade (never migrate without a safety net).
* **Migrate** — ``alembic upgrade head`` runs as the OWNER role (the env.py DSN = ``sync_dsn``). A
  single Alembic migration runs in one transaction that auto-rolls-back on error — that is the
  honest meaning of "rollback" for a failed migration step.
* **Health-gate** — ``readiness.check_all()`` must be green (esp. the alembic-at-head probe).

    HARDENING TODO (S11+): full automated rollback = restore-and-cut-over from the pre-upgrade
    archive. The MVP does NOT auto-restore: a failed migration auto-rolls-back its own txn, and the
    operator runs ``easysynq restore <pre-backup>`` (restore-to-verified-target) + the documented
    cutover if needed. ``UPGRADE_FAILED.after`` names the pre-backup archive.

Runs on the worker (OWNER DSN + pg client). Audits via the app session like the backup service.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


async def run_upgrade(org_id: uuid.UUID, actor_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Pre-backup → migrate → health-gate. Never raises — returns ``{result: OK|FAILED, ...}``.

    ``stage`` is one of ``pre_backup`` | ``migrate`` | ``health_gate`` | ``orchestration``; the last
    covers a failure outside the three guarded stages (see the outer handler). A ``pre_backup``
    failure includes an archive that was written but did NOT pass its own checksum verification —
    an unusable safety net is not a safety net.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        target_head = _alembic_head()
        async with sessionmaker() as session:
            destination = await _backup_destination(session, org_id)
            _emit(
                session,
                org_id=org_id,
                actor_id=actor_id,
                event_type="UPGRADE_STARTED",
                after={"target_head": target_head, "destination": destination},
            )
            await session.commit()

            # 1. pre-backup (the disaster safety net) — abort the upgrade if it fails
            #
            # ⚠ The catch is deliberately BROAD. It was `except BackupError` and that is far too
            # narrow: the canonical pre-backup failures do not raise BackupError at all —
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
                logger.exception("upgrade: pre-backup failed")
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
            # then points UPGRADE_FAILED.pre_backup_archive (the operator's only recovery pointer)
            # at that same dead file. Fail CLOSED on a missing key: an unreportable check is not
            # a pass. This mirrors `services/backup/service.py:197-209`, which hardened the nightly
            # path against precisely this scenario; the guard was never propagated here.
            # (Folding both into one shared typed validator is deferred to the full S-upgrade-safety
            # slice — see docs/superpowers/plans/2026-08-04-audit-remediation-v2.md.)
            if not backup.get("verified", False):
                detail = (
                    "pre-backup archive written but FAILED checksum verification: "
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
                        "pre_backup_archive": backup["archive"],
                    },
                )
                await session.commit()
                return {
                    "result": "FAILED",
                    "stage": "migrate",
                    "reason": str(exc)[:300],
                    "pre_backup_archive": backup["archive"],
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
                        "pre_backup_archive": backup["archive"],
                    },
                )
                await session.commit()
                return {
                    "result": "FAILED",
                    "stage": "health_gate",
                    "unhealthy": unhealthy,
                    "pre_backup_archive": backup["archive"],
                }

            _emit(
                session,
                org_id=org_id,
                actor_id=actor_id,
                event_type="UPGRADE_COMPLETED",
                after={"head": target_head, "pre_backup_archive": backup["archive"]},
            )
            await session.commit()
            return {
                "result": "OK",
                "head": target_head,
                "pre_backup_archive": backup["archive"],
            }
    except Exception as exc:
        # ⚠ The three guarded stages above are not the whole function. `_alembic_head()`, the
        # destination lookup, every `_emit`/`commit`, and `check_all()` all sit OUTSIDE them, and
        # this outer block previously carried only a `finally:` — so any of them escaped and the
        # docstring's "Never raises" was simply untrue. That matters concretely: `cli/upgrade.py`
        # does not wrap this call, so an escape hands the operator a traceback instead of a stage
        # and a recovery pointer, and strands UPGRADE_STARTED with no terminal event.
        logger.exception("upgrade: unexpected failure outside a guarded stage")
        reason = f"{type(exc).__name__}: {exc}"[:300]
        await _try_emit_orchestration_failure(
            sessionmaker, org_id=org_id, actor_id=actor_id, reason=reason
        )
        return {"result": "FAILED", "stage": "orchestration", "reason": reason}
    finally:
        await engine.dispose()


async def _try_emit_orchestration_failure(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    reason: str,
) -> None:
    """Best-effort terminal audit row for a failure outside the guarded stages.

    Opens its OWN session: the caller's may be mid-transaction or bound to a dead connection, and a
    session reused across a failure is the repo's documented ``MissingGreenlet``-at-pool-teardown
    trap. Swallows its own errors deliberately — if the database is the thing that broke, the caller
    must still receive an honest ``FAILED`` dict rather than a second exception. An audit
    UNDER-claim is the safe direction for an append-only chain (the R64 precedent).
    """
    try:
        async with sessionmaker() as session:
            _emit(
                session,
                org_id=org_id,
                actor_id=actor_id,
                event_type="UPGRADE_FAILED",
                after={"stage": "orchestration", "error": reason},
            )
            await session.commit()
    except Exception:
        logger.exception("upgrade: could not record the orchestration-failure audit row")
