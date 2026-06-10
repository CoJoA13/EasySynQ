"""Operator CLI for the D1 blob integrity verify (S-drift-3) — runs inside the api/worker image.

    python -m easysynq_api.cli.blob verify                  # rolling sample (settings size)
    python -m easysynq_api.cli.blob verify --full           # the on-demand complete pass
    python -m easysynq_api.cli.blob verify --sample-size N  # override the sample size

Acquires the same ``LOCK_BLOB_VERIFY`` the Beat task uses, so a manual run and the scheduler
cannot race (skips if held). After restoring a corrupted object from backup, re-run ``verify``
to clear the alarm (stamp-on-OK-only: a finding re-alarms every run until the re-hash passes).
Exit 1 on a FAILED (infrastructure) scan.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.common.pg_locks import LOCK_BLOB_VERIFY, pg_advisory_lock
from ..services.vault.blob_verify import BlobVerifyReport, persist_blob_verify, verify_blobs


async def _verify(*, full: bool, sample_size: int | None) -> tuple[BlobVerifyReport, bool] | None:
    """The scan+persist pipeline under the advisory lock; ``None`` if another verify holds it.
    Returns the report AND whether persistence succeeded — an unpersisted pass recorded nothing
    (no stamps, no summary row), and the operator must know."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_BLOB_VERIFY) as held:
            if not held:
                return None
            report = await verify_blobs(session, sample_size=sample_size, full=full)
            persisted = await persist_blob_verify(session, report, triggered_by="cli")
            return report, persisted
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-blob", description="Vault blob integrity CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser(
        "verify",
        help="re-hash blobs against their sha256 identity (doc 03 §8.2, D1); rolling by default",
    )
    verify.add_argument(
        "--full", action="store_true", help="verify EVERY blob (the periodic full set)"
    )
    verify.add_argument(
        "--sample-size", type=int, default=None, help="override BLOB_VERIFY_SAMPLE_SIZE"
    )
    args = parser.parse_args(argv)

    result = asyncio.run(_verify(full=args.full, sample_size=args.sample_size))
    if result is None:
        print("blob verify skipped: another verify is already in progress")
        return 0
    report, persisted = result
    c = report.counts()
    print(
        f"blob verify: status={report.status} scanned={c['scanned']} ok={c['ok']} "
        f"mismatched={c['mismatched']} missing={c['missing']} read_errors={c['read_errors']} "
        f"total_blobs={c['total_blobs']} full={c['full']} persisted={persisted}"
    )
    if not persisted:
        print(
            "WARNING: persist failed — NOTHING was recorded (no stamps, no events, no summary "
            "row); the next run resamples. Check the worker/DB logs."
        )
    return 1 if report.status == "FAILED" or not persisted else 0


if __name__ == "__main__":
    raise SystemExit(main())
