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
from .backup.archive import BackupError

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
    """Pre-backup → migrate → health-gate. Never raises — returns ``{result: OK|FAILED, ...}``."""
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
            try:
                backup = await asyncio.to_thread(
                    build_durable_backup, settings, destination=destination
                )
            except BackupError as exc:
                _emit(
                    session,
                    org_id=org_id,
                    actor_id=actor_id,
                    event_type="UPGRADE_FAILED",
                    after={"stage": "pre_backup", "error": str(exc)[:300]},
                )
                await session.commit()
                return {"result": "FAILED", "stage": "pre_backup", "reason": str(exc)[:300]}

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
    finally:
        await engine.dispose()
