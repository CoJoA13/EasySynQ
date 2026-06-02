"""Operator CLI for backup + the restore-test drill (slice S8b2) — runs inside the worker image
(which carries postgresql-client + the OWNER ``DATABASE_URL_SYNC``).

    python -m easysynq_api.cli.backup run            # durable archive of every configured policy
    python -m easysynq_api.cli.backup restore-test   # the backup→restore-into-scratch drill (G-C)

``restore-test`` persists PASS/FAIL to ``backup_policy.last_restore_test_result`` (the signal the
G-C setup gate reads) and exits non-zero on FAIL. The operator-grade WORM-aware *live* restore
(R37) + ``easysynq restore``/``upgrade`` stay S11 (see ``scripts/easysynq``).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models.organization import Organization
from ..services.backup import run_restore_test, run_scheduled_backups


async def _restore_test() -> dict[str, object]:
    """Resolve the single-org install (D1) and run the drill (operator-triggered → system actor)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            org_id = await session.scalar(select(Organization.id).limit(1))
    finally:
        await engine.dispose()
    if org_id is None:
        return {"result": "FAIL", "reason": "no organization found"}
    return await run_restore_test(org_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-backup", description="Backup + restore-test.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="write a durable backup archive of every configured policy")
    sub.add_parser("restore-test", help="run the backup→restore-into-scratch drill (gate G-C)")
    args = parser.parse_args(argv)

    if args.command == "run":
        out = asyncio.run(run_scheduled_backups())
        backups = out.get("backups", [])
        print(f"backup run: {len(backups)} policy/policies processed")
        for b in backups:
            print(f"  - {b}")
        return 0 if all("error" not in b for b in backups) else 1

    out = asyncio.run(_restore_test())
    print(f"restore-test: {out.get('result')} — {out.get('reason')}")
    return 0 if out.get("result") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
