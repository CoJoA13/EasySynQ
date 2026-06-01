"""Operator CLI for the audit trail (slice S6) — runs inside the api image (DB reachable there).

    python -m easysynq_api.cli.audit ensure-partitions   # Beat-down fallback (doc 18 §4 line 213)
    python -m easysynq_api.cli.audit verify-chain         # on-demand tamper check (AC#6b)

``ensure-partitions`` is the manual fallback for the daily ``roll_partitions`` Beat job if the
scheduler was down near a month boundary. ``verify-chain`` re-walks the linked chain and prints the
first broken link (exit code 1 if the chain is broken), so it can gate a backup/restore drill.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models.organization import Organization
from ..services.audit.partitions import ensure_partitions
from ..services.audit.verify import verify_chain


async def _ensure_partitions() -> list[str]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            return await ensure_partitions(session)
    finally:
        await engine.dispose()


async def _verify_chain() -> tuple[bool, int, int, list[tuple[int, str]]]:
    """Verify every org's chain (the chain is per-org). Aggregates the result."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    verified, checked, pending = True, 0, 0
    breaks: list[tuple[int, str]] = []
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                result = await verify_chain(session, org_id)
                verified = verified and result.verified
                checked += result.checked
                pending += result.pending
                breaks.extend((b.at_id, b.reason) for b in result.breaks)
        return verified, checked, pending, breaks
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-audit", description="Audit-trail operator CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure-partitions", help="create the rolling monthly audit_event partitions")
    sub.add_parser("verify-chain", help="re-walk + verify the hash chain")
    args = parser.parse_args(argv)

    if args.command == "ensure-partitions":
        ensured = asyncio.run(_ensure_partitions())
        print(f"ensured partitions: {', '.join(ensured)}")
        return 0

    verified, checked, pending, breaks = asyncio.run(_verify_chain())
    print(f"verified={verified} checked={checked} pending={pending}")
    for at_id, reason in breaks:
        print(f"  BREAK at id={at_id}: {reason}")
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
