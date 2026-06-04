"""Data access for ingestion runs + the scan inventory (slice S-ing-1).

SQL only — no orchestration, no audit (the service owns those). The inventory summary is computed as
**SQL aggregates** over ``import_file`` (never by loading rows into RAM) and assembled by the pure
``domain.ingestion.summary.build_summary``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._ingestion_enums import ImportRunStatus
from ...db.models.import_file import ImportFile
from ...db.models.import_run import ImportRun
from ...domain.ingestion.classifier import ScanFlags
from ...domain.ingestion.source import FileMeta
from ...domain.ingestion.summary import build_summary

# The run states that count as "active" for the one-run-per-root surface (the 409 detail).
_ACTIVE_STATES = (ImportRunStatus.CREATED, ImportRunStatus.SCANNING)


async def get_run(
    session: AsyncSession, run_id: uuid.UUID, *, for_update: bool = False
) -> ImportRun | None:
    stmt = select(ImportRun).where(ImportRun.id == run_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_status(session: AsyncSession, run_id: uuid.UUID) -> ImportRunStatus | None:
    return (
        await session.execute(select(ImportRun.status).where(ImportRun.id == run_id))
    ).scalar_one_or_none()


async def active_run_for_hash(
    session: AsyncSession, org_id: uuid.UUID, source_root_hash: str
) -> ImportRun | None:
    """The run currently holding a source root (for the duplicate-active-run 409 detail)."""
    return (
        await session.execute(
            select(ImportRun)
            .where(
                ImportRun.org_id == org_id,
                ImportRun.source_root_hash == source_root_hash,
                ImportRun.status.in_(_ACTIVE_STATES),
            )
            .order_by(ImportRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_runs(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    status: ImportRunStatus | None = None,
    limit: int = 50,
) -> Sequence[ImportRun]:
    stmt = select(ImportRun).where(ImportRun.org_id == org_id)
    if status is not None:
        stmt = stmt.where(ImportRun.status == status)
    stmt = stmt.order_by(ImportRun.created_at.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()


async def list_files(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    disposition: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[ImportFile]:
    stmt = select(ImportFile).where(ImportFile.run_id == run_id)
    if disposition is not None:
        stmt = stmt.where(
            func.jsonb_extract_path_text(ImportFile.scan_flags, "disposition") == disposition
        )
    stmt = stmt.order_by(ImportFile.rel_path).limit(limit).offset(offset)
    return (await session.execute(stmt)).scalars().all()


async def upsert_file(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    meta: FileMeta,
    flags: ScanFlags,
    sha256: str | None,
    staged_blob_uri: str | None,
    mime_type: str | None,
) -> None:
    """Insert/update the inventory row keyed on ``(run_id, rel_path)`` — the §11.1 idempotency key,
    so a
    re-delivered / resumed scan converges on the same row instead of duplicating it."""
    stmt = pg_insert(ImportFile).values(
        org_id=org_id,
        run_id=run_id,
        rel_path=meta.rel_path,
        filename=meta.filename,
        ext=meta.ext,
        size_bytes=meta.size_bytes,
        mtime=meta.mtime,
        ctime=meta.ctime,
        mime_type=mime_type,
        sha256=sha256,
        staged_blob_uri=staged_blob_uri,
        scan_flags=flags.to_dict(),
        included_candidate=flags.included_candidate,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["run_id", "rel_path"],
        set_={
            "filename": stmt.excluded.filename,
            "ext": stmt.excluded.ext,
            "size_bytes": stmt.excluded.size_bytes,
            "mtime": stmt.excluded.mtime,
            "ctime": stmt.excluded.ctime,
            "mime_type": stmt.excluded.mime_type,
            "sha256": stmt.excluded.sha256,
            "staged_blob_uri": stmt.excluded.staged_blob_uri,
            "scan_flags": stmt.excluded.scan_flags,
            "included_candidate": stmt.excluded.included_candidate,
        },
    )
    await session.execute(stmt)


async def compute_counts(session: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    """The §4.3 inventory summary as SQL aggregates over the run's files (no rows materialised)."""
    total_files, total_bytes = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(ImportFile.size_bytes), 0)).where(
                ImportFile.run_id == run_id
            )
        )
    ).one()

    disp_col = func.jsonb_extract_path_text(ImportFile.scan_flags, "disposition")
    disposition_counts = {
        (d or ""): c
        for d, c in (
            await session.execute(
                select(disp_col, func.count()).where(ImportFile.run_id == run_id).group_by(disp_col)
            )
        ).all()
    }

    ext_histogram = {
        (e or ""): c
        for e, c in (
            await session.execute(
                select(func.coalesce(ImportFile.ext, ""), func.count())
                .where(ImportFile.run_id == run_id)
                .group_by(ImportFile.ext)
            )
        ).all()
    }

    dup_groups = (
        select(func.count().label("c"))
        .where(ImportFile.run_id == run_id, ImportFile.sha256.is_not(None))
        .group_by(ImportFile.sha256)
        .having(func.count() > 1)
        .subquery()
    )
    exact_dup_clusters = (
        await session.execute(select(func.count()).select_from(dup_groups))
    ).scalar_one()
    exact_dup_files = (
        await session.execute(select(func.coalesce(func.sum(dup_groups.c.c), 0)))
    ).scalar_one()

    return build_summary(
        total_files=int(total_files),
        total_bytes=int(total_bytes),
        disposition_counts={k: int(v) for k, v in disposition_counts.items()},
        ext_histogram={k: int(v) for k, v in ext_histogram.items()},
        exact_dup_clusters=int(exact_dup_clusters),
        exact_dup_files=int(exact_dup_files),
    )
