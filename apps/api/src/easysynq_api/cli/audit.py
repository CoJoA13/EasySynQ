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
from ..services.audit.checkpoint import load_verify_key, verify_offhost_checkpoint
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
    # Attest the signed checkpoint too when the verify (public) key is available to this process;
    # else the chain is walked only (a checkpoint/signature break folds into ``breaks``).
    verify_key = load_verify_key()
    verified, checked, pending = True, 0, 0
    breaks: list[tuple[int, str]] = []
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                result = await verify_chain(session, org_id, verify_key=verify_key)
                verified = verified and result.verified
                checked += result.checked
                pending += result.pending
                breaks.extend((b.at_id, b.reason) for b in result.breaks)
        return verified, checked, pending, breaks
    finally:
        await engine.dispose()


async def _verify_offhost() -> tuple[bool, int, list[tuple[str, list[str]]]]:
    """The INDEPENDENT off-host read-back (doc 12 §4.4) — run this OUT-OF-BAND from a separate host
    with the read creds + the public key to attest that the off-host anchor still matches the live
    chain. Returns (ok, sinks_read, [(org_id, reasons)])."""
    verify_key = load_verify_key()
    if verify_key is None:
        return (
            False,
            0,
            [("", ["no verify (public) key available — cannot attest the off-host copy"])],
        )
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    ok, read = True, 0
    org_reasons: list[tuple[str, list[str]]] = []
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                res = await verify_offhost_checkpoint(session, org_id, verify_key=verify_key)
                read += res.sinks_read
                ok = ok and res.verified
                if res.reasons:
                    org_reasons.append((str(org_id), res.reasons))
        return ok, read, org_reasons
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-audit", description="Audit-trail operator CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure-partitions", help="create the rolling monthly audit_event partitions")
    sub.add_parser("verify-chain", help="re-walk + verify the hash chain (+ signed checkpoint)")
    sub.add_parser("verify-offhost", help="independent off-host checkpoint read-back (out-of-band)")
    args = parser.parse_args(argv)

    if args.command == "ensure-partitions":
        ensured = asyncio.run(_ensure_partitions())
        print(f"ensured partitions: {', '.join(ensured)}")
        return 0

    if args.command == "verify-offhost":
        ok, read, org_reasons = asyncio.run(_verify_offhost())
        print(f"offhost_verified={ok} sinks_read={read}")
        for org_id, reasons in org_reasons:
            for reason in reasons:
                print(f"  MISMATCH org={org_id}: {reason}")
        return 0 if ok else 1

    verified, checked, pending, breaks = asyncio.run(_verify_chain())
    print(f"verified={verified} checked={checked} pending={pending}")
    for at_id, reason in breaks:
        print(f"  BREAK at id={at_id}: {reason}")
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
