"""Data access for ingestion runs + the scan inventory (slice S-ing-1).

SQL only — no orchestration, no audit (the service owns those). The inventory summary is computed as
**SQL aggregates** over ``import_file`` (never by loading rows into RAM) and assembled by the pure
``domain.ingestion.summary.build_summary``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._clause_enums import PdcaPhase
from ...db.models._ingestion_enums import ImportExtractStatus, ImportRunStatus
from ...db.models.clause import Clause
from ...db.models.framework import Framework
from ...db.models.import_classification import ImportClassification
from ...db.models.import_extract import ImportExtract
from ...db.models.import_file import ImportFile
from ...db.models.import_run import ImportRun
from ...db.models.process import Process
from ...domain.ingestion.classifier import ScanFlags
from ...domain.ingestion.source import FileMeta
from ...domain.ingestion.summary import build_summary

# The run states that count as "active" for the one-run-per-root surface (the 409 detail + the
# duplicate-create guard). The S-ing-2 pipeline holds the source-root lock continuously through
# these, so Scanned/Extracting/Classifying are all active (only the terminals free the root).
_ACTIVE_STATES = (
    ImportRunStatus.CREATED,
    ImportRunStatus.SCANNING,
    ImportRunStatus.SCANNED,
    ImportRunStatus.EXTRACTING,
    ImportRunStatus.CLASSIFYING,
)


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


async def list_files_with_classification(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    classifier_version: str | None,
    disposition: str | None = None,
    kind: Any | None = None,
    band: Any | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[tuple[ImportFile, ImportClassification | None]]:
    """The inventory joined to each file's classification proposal (S-ing-2), with optional
    disposition / kind / band filters. The join is pinned to ``classifier_version`` (the run's
    version) so a future re-classify never duplicates a file row (the UNIQUE is run,file,version);
    a NULL version (not yet classified) matches no rows → classification is null for every file."""
    stmt = (
        select(ImportFile, ImportClassification)
        .outerjoin(
            ImportClassification,
            and_(
                ImportClassification.run_id == run_id,
                ImportClassification.file_id == ImportFile.id,
                ImportClassification.classifier_version == classifier_version,
            ),
        )
        .where(ImportFile.run_id == run_id)
    )
    if disposition is not None:
        stmt = stmt.where(
            func.jsonb_extract_path_text(ImportFile.scan_flags, "disposition") == disposition
        )
    if kind is not None:
        stmt = stmt.where(ImportClassification.kind == kind)
    if band is not None:
        stmt = stmt.where(ImportClassification.band == band)
    stmt = stmt.order_by(ImportFile.rel_path).limit(limit).offset(offset)
    return [(f, c) for f, c in (await session.execute(stmt)).all()]


async def get_file_detail(
    session: AsyncSession, run_id: uuid.UUID, file_id: uuid.UUID, *, classifier_version: str | None
) -> tuple[ImportFile, ImportExtract | None, ImportClassification | None] | None:
    """One file + its extract + its classification (S-ing-2 per-file review detail). The
    classification is pinned to the run's ``classifier_version`` + ``LIMIT 1`` (newest) so multiple
    versions never raise MultipleResultsFound."""
    f = (
        await session.execute(
            select(ImportFile).where(ImportFile.id == file_id, ImportFile.run_id == run_id)
        )
    ).scalar_one_or_none()
    if f is None:
        return None
    ext = (
        await session.execute(
            select(ImportExtract).where(
                ImportExtract.run_id == run_id, ImportExtract.file_id == file_id
            )
        )
    ).scalar_one_or_none()
    cls = (
        await session.execute(
            select(ImportClassification)
            .where(
                ImportClassification.run_id == run_id,
                ImportClassification.file_id == file_id,
                ImportClassification.classifier_version == classifier_version,
            )
            .order_by(ImportClassification.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return f, ext, cls


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


# ------------------------------------------------------------------- S-ing-2: extract + classify


async def clause_pdca_map(session: AsyncSession, org_id: uuid.UUID) -> dict[str, str]:
    """``{clause_number: pdca_phase}`` for the org's REQUIREMENT-NODE clauses only — the authority
    the classifier derives PDCA from (bare section headers are excluded, so they never drive a
    phase). Fetched once per run (doc 09 §6.1)."""
    rows = (
        await session.execute(
            select(Clause.number, Clause.pdca_phase)
            .join(Framework, Framework.id == Clause.framework_id)
            .where(Framework.org_id == org_id, Clause.requirement_node.is_(True))
        )
    ).all()
    return {
        number: (phase.value if isinstance(phase, PdcaPhase) else str(phase))
        for number, phase in rows
    }


async def process_names(session: AsyncSession, org_id: uuid.UUID) -> list[str]:
    """The org's existing process names — the classifier scores a process link when one appears as a
    folder/header token. Empty on a fresh install (process_conf then 0)."""
    rows = (
        (await session.execute(select(Process.name).where(Process.org_id == org_id)))
        .scalars()
        .all()
    )
    return list(rows)


async def files_pending_extract(
    session: AsyncSession, run_id: uuid.UUID, *, limit: int
) -> Sequence[ImportFile]:
    """Included files with no ``import_extract`` row yet — the resume batch (idempotent re-delivery
    converges via the (run_id, file_id) upsert)."""
    pending = ~(
        select(ImportExtract.id)
        .where(ImportExtract.run_id == run_id, ImportExtract.file_id == ImportFile.id)
        .exists()
    )
    stmt = (
        select(ImportFile)
        .where(
            ImportFile.run_id == run_id,
            ImportFile.included_candidate.is_(True),
            pending,
        )
        .order_by(ImportFile.rel_path)
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def files_pending_classify(
    session: AsyncSession, run_id: uuid.UUID, classifier_version: str, *, limit: int
) -> Sequence[tuple[ImportFile, ImportExtract | None]]:
    """Included files with no ``import_classification`` row for this version yet, joined to their
    extract (NULL if extraction failed/absent → the classifier falls back to filename/path)."""
    pending = ~(
        select(ImportClassification.id)
        .where(
            ImportClassification.run_id == run_id,
            ImportClassification.file_id == ImportFile.id,
            ImportClassification.classifier_version == classifier_version,
        )
        .exists()
    )
    stmt = (
        select(ImportFile, ImportExtract)
        .outerjoin(
            ImportExtract,
            and_(ImportExtract.run_id == run_id, ImportExtract.file_id == ImportFile.id),
        )
        .where(
            ImportFile.run_id == run_id,
            ImportFile.included_candidate.is_(True),
            pending,
        )
        .order_by(ImportFile.rel_path)
        .limit(limit)
    )
    return [(f, e) for f, e in (await session.execute(stmt)).all()]


async def upsert_extract(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    file_id: uuid.UUID,
    full_text: str | None,
    text_truncated: bool,
    header_block: str | None,
    embedded_props: dict[str, Any] | None,
    language: str | None,
    structure_hints: dict[str, Any] | None,
    ocr_used: bool,
    ocr_confidence: float | None,
    char_count: int | None,
    page_count: int | None,
    status: ImportExtractStatus,
    error: str | None,
    extractor_version: str | None,
) -> None:
    """Upsert the Stage-2 extraction keyed on ``(run_id, file_id)`` — the §3.1 idempotency key."""
    values = {
        "org_id": org_id,
        "run_id": run_id,
        "file_id": file_id,
        "full_text": full_text,
        "text_truncated": text_truncated,
        "header_block": header_block,
        "embedded_props": embedded_props,
        "language": language,
        "structure_hints": structure_hints,
        "ocr_used": ocr_used,
        "ocr_confidence": ocr_confidence,
        "char_count": char_count,
        "page_count": page_count,
        "status": status,
        "error": error,
        "extractor_version": extractor_version,
    }
    stmt = pg_insert(ImportExtract).values(**values)
    update = {k: stmt.excluded[k] for k in values if k not in ("org_id", "run_id", "file_id")}
    await session.execute(
        stmt.on_conflict_do_update(index_elements=["run_id", "file_id"], set_=update)
    )


async def upsert_classification(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    file_id: uuid.UUID,
    classifier_version: str,
    values: dict[str, Any],
) -> None:
    """Upsert the Stage-3 classification on ``(run_id, file_id, classifier_version)`` (§3.1 key)."""
    row = {
        "org_id": org_id,
        "run_id": run_id,
        "file_id": file_id,
        "classifier_version": classifier_version,
        **values,
    }
    stmt = pg_insert(ImportClassification).values(**row)
    update = {
        k: stmt.excluded[k]
        for k in row
        if k not in ("org_id", "run_id", "file_id", "classifier_version")
    }
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["run_id", "file_id", "classifier_version"], set_=update
        )
    )


def _enum_key(value: Any) -> str:
    """Group-by on a native-enum column yields the Python enum member — key the histogram on its
    string value (robust if a driver hands back the raw string instead)."""
    return value.value if hasattr(value, "value") else str(value)


async def compute_classify_counts(
    session: AsyncSession, run_id: uuid.UUID, classifier_version: str
) -> dict[str, Any]:
    """The §4.3 classify summary as SQL aggregates: by_kind / by_band / extract-by-status histograms
    (merged into the run's existing scan counts at the Classified checkpoint). The classification
    aggregates are pinned to ``classifier_version`` so a re-classify never double-counts (§6.6)."""
    by_kind = {
        _enum_key(k): int(c)
        for k, c in (
            await session.execute(
                select(ImportClassification.kind, func.count())
                .where(
                    ImportClassification.run_id == run_id,
                    ImportClassification.classifier_version == classifier_version,
                )
                .group_by(ImportClassification.kind)
            )
        ).all()
    }
    by_band = {
        _enum_key(b): int(c)
        for b, c in (
            await session.execute(
                select(ImportClassification.band, func.count())
                .where(
                    ImportClassification.run_id == run_id,
                    ImportClassification.classifier_version == classifier_version,
                )
                .group_by(ImportClassification.band)
            )
        ).all()
    }
    extract_by_status = {
        _enum_key(s): int(c)
        for s, c in (
            await session.execute(
                select(ImportExtract.status, func.count())
                .where(ImportExtract.run_id == run_id)
                .group_by(ImportExtract.status)
            )
        ).all()
    }
    classified = sum(by_kind.values())
    return {
        "classified": classified,
        "by_kind": by_kind,
        "by_band": by_band,
        "extract": extract_by_status,
    }
