"""Operator CLI for the in-place upgrade (slice S11) — runs inside the worker image (OWNER
``DATABASE_URL_SYNC`` + pg client + the alembic migrations).

    python -m easysynq_api.cli.upgrade --confirm

Enforces pre-backup → ``alembic upgrade head`` → readiness health-gate. The pre-backup archive is
the disaster safety net (a failed migration auto-rolls-back its own txn; full auto-restore is a
documented operator step / hardening TODO). Exit 0 = UPGRADE_COMPLETED, 1 = UPGRADE_FAILED.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Sequence
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models.organization import Organization
from ..services.upgrade import run_upgrade


async def _resolve_org_id() -> uuid.UUID | None:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            return cast("uuid.UUID | None", await session.scalar(select(Organization.id).limit(1)))
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="easysynq-upgrade", description="Pre-backup → migrate → health-gate upgrade."
    )
    parser.add_argument(
        "--confirm", action="store_true", help="required: runs migrations on the live database"
    )
    args = parser.parse_args(argv)
    if not args.confirm:
        print("upgrade: refusing to run without --confirm (it migrates the live database)")
        return 2

    org_id = asyncio.run(_resolve_org_id())
    if org_id is None:
        print("upgrade: no organization found")
        return 1

    out = asyncio.run(run_upgrade(org_id))
    if out.get("result") == "OK":
        print(
            f"upgrade: OK — at head {out.get('head')} (pre-backup {out.get('pre_backup_archive')})"
        )
        return 0
    print(f"upgrade: FAILED at '{out.get('stage')}' — {out.get('reason') or out.get('unhealthy')}")
    if out.get("pre_backup_archive"):
        print(f"  pre-backup safety net: {out.get('pre_backup_archive')}")
        print("  to recover: easysynq restore <pre-backup> then cut over (runbook)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
