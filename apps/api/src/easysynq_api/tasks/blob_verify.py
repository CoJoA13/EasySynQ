"""Celery/Beat task for the D1 blob integrity verify (S-drift-3, doc 03 §8.2, doc 05 §9.1 D1).

Daily rolling re-hash of the K least-recently-verified vault blobs against their sha256 PK.
Stamp-on-OK-only: a finding re-alarms every run until the operator restores the object (there is
no auto-correction for blobs — restore-from-backup is the runbook action). Single-flight under
``LOCK_BLOB_VERIFY`` (skip-if-held); own disposed async engine per ``asyncio.run`` (the app's
non-owner role); the scan itself NEVER raises (an infra failure is an honest FAILED summary row).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.common.pg_locks import LOCK_BLOB_VERIFY, pg_advisory_lock
from ..services.vault.blob_verify import persist_blob_verify, verify_blobs
from .app import app

logger = logging.getLogger("easysynq.blob.tasks")


async def _run_blob_verify() -> dict[str, object]:
    """The rolling D1 pass under the advisory lock; returns the summary counts (or a skip marker
    when another verify holds the lock)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_BLOB_VERIFY) as held:
            if not held:
                logger.info("blob.verify: another verify holds the lock; skipping this tick")
                return {"skipped_lock_held": 1}
            report = await verify_blobs(session)
            persisted = await persist_blob_verify(session, report, triggered_by="beat")
            summary: dict[str, object] = {**report.counts(), "persisted": persisted}
            logger.info("blob.verify.done", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.blob.verify")  # type: ignore[untyped-decorator]
def blob_verify() -> dict[str, object]:
    """Daily D1 rolling blob re-hash (doc 03 §8.2): stamps verified_at on OK, alarms on findings."""
    return asyncio.run(_run_blob_verify())
