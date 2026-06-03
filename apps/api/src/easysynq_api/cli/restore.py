"""Operator CLI for the WORM-aware live restore (slice S11, R37) — runs inside the worker image
(postgresql-client + the OWNER ``DATABASE_URL_SYNC``).

    python -m easysynq_api.cli.restore <archive> --confirm
    python -m easysynq_api.cli.restore <archive> --confirm --audit-checkpoint-ack
    python -m easysynq_api.cli.restore --discard <scratch_db>

Restores to a VERIFIED TARGET (a fresh scratch DB + fresh non-WORM bucket) and LEAVES IT STANDING
for the documented operator cutover — it never mutates the live locked vault, never auto-cuts-over
(see docs/runbooks/backup-restore.md). Exit 0 = verified target ready, 3 = FLAGGED
(checkpoint ahead — re-run with --audit-checkpoint-ack), 1 = FAIL.
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
from ..services.backup import run_restore
from ..services.backup.restore import discard_target


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
        prog="easysynq-restore", description="WORM-aware live restore to a verified target (R37)."
    )
    parser.add_argument("archive", nargs="?", help="path to the backup archive (.tar.enc or .tar)")
    parser.add_argument(
        "--confirm", action="store_true", help="required: stand up a scratch target + copy blobs"
    )
    parser.add_argument(
        "--audit-checkpoint-ack",
        action="store_true",
        help="acknowledge a checkpoint-ahead flag and proceed (the acknowledgement is audited)",
    )
    parser.add_argument(
        "--discard",
        metavar="SCRATCH_DB",
        help="tear down a previously left-standing verified target",
    )
    args = parser.parse_args(argv)

    if args.discard:
        discard_target(get_settings(), args.discard)
        print(f"restore: discarded verified target {args.discard}")
        return 0

    if not args.archive:
        parser.error("an archive path is required (or use --discard SCRATCH_DB)")
    if not args.confirm:
        print(
            "restore: refusing to run without --confirm (it stands up a scratch DB + copies blobs)"
        )
        return 2

    org_id = asyncio.run(_resolve_org_id())
    if org_id is None:
        print("restore: no organization found")
        return 1

    out = asyncio.run(
        run_restore(
            org_id,
            archive_path=args.archive,
            audit_checkpoint_ack=args.audit_checkpoint_ack,
        )
    )
    print(f"restore: {out.get('result')} — {out.get('reason')}")
    if out.get("result") == "PASS":
        print(f"  verified target: db={out.get('scratch_db')} bucket={out.get('scratch_bucket')}")
        print('  next: cut over per docs/runbooks/backup-restore.md ("Cut over")')
        return 0
    if out.get("result") == "FLAGGED":
        print("  re-run with --audit-checkpoint-ack to proceed (the acknowledgement is audited)")
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
