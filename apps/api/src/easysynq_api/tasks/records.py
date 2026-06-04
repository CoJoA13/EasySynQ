"""Celery/Beat task for records retention (slice S-rec-2, doc 06 §5.3).

``retention_sweep`` runs daily: it flips ``ACTIVE`` records whose retention has elapsed to
``DUE_FOR_REVIEW`` (emitting the ``RECORD_DISPOSITION_DUE`` system audit event — the v1 'notify
owning org_role' surrogate until doc-10 notifications land) and auto-executes disposition for
low-risk (``review_required=false``) policies once the WORM lock allows; ``review_required=true``
records stop at DUE_FOR_REVIEW for human approval under ``record.dispose``.

Like the other Beat tasks it uses its own disposed async engine (a fresh event loop per
``asyncio.run`` is safe). It connects with the **app DSN** (``database_url``, the non-owner
``easysynq_app`` role) — the sweep only SELECTs/UPDATEs ``record`` + INSERTs ``disposition_event``/
``audit_event`` (all granted to the app role in 0010/0024); it needs no owner DDL (unlike backup).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.records import build_structured_pdf as _build_structured_pdf
from ..services.records import sweep_due_records
from .app import app

logger = logging.getLogger("easysynq.records.tasks")


async def _run_retention_sweep() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await sweep_due_records(session)
            logger.info("records.retention_sweep", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.records.retention_sweep")  # type: ignore[untyped-decorator]
def retention_sweep() -> dict[str, int]:
    """Sweep due records; returns ``{flipped, disposed, skipped}`` counts for this run."""
    return asyncio.run(_run_retention_sweep())


async def _run_build_structured_pdf(record_id: str) -> None:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            await _build_structured_pdf(session, uuid.UUID(record_id))
    finally:
        await engine.dispose()


@app.task(name="easysynq.records.build_structured_pdf")  # type: ignore[untyped-decorator]
def build_structured_pdf(record_id: str) -> None:
    """Build the Stage-2 structured-record PDF rendition (S-rec-3; idempotent, best-effort)."""
    asyncio.run(_run_build_structured_pdf(record_id))
