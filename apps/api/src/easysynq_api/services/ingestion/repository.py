"""Data access for ingestion runs + the scan inventory (slice S-ing-1).

SQL only — no orchestration, no audit (the service owns those). The inventory summary is computed as
**SQL aggregates** over ``import_file`` (never by loading rows into RAM) and assembled by the pure
``domain.ingestion.summary.build_summary``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Text, and_, cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._clause_enums import PdcaPhase
from ...db.models._ingestion_enums import (
    ImportCommitResultStatus,
    ImportDecisionAction,
    ImportExtractStatus,
    ImportRunStatus,
)
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.clause import Clause
from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.framework import Framework
from ...db.models.import_classification import ImportClassification
from ...db.models.import_commit_result import ImportCommitResult
from ...db.models.import_decision import ImportDecision
from ...db.models.import_dupe_cluster import ImportDupeCluster
from ...db.models.import_extract import ImportExtract
from ...db.models.import_file import ImportFile
from ...db.models.import_proposal_node import ImportProposalNode
from ...db.models.import_run import ImportRun
from ...db.models.import_version_family import ImportVersionFamily
from ...db.models.process import Process
from ...domain.ingestion.classifier import ScanFlags
from ...domain.ingestion.source import FileMeta
from ...domain.ingestion.summary import build_summary

# The run states that count as "active" for the one-run-per-root surface (the 409 detail + the
# duplicate-create guard). The pipeline holds the source-root lock continuously through ALL of
# these,
# so they are all active (only PROPOSED/FAILED/CANCELLED free the root). S-ing-3: Classified is no
# longer terminal (it chains to dedup), so it + Deduping/Proposing are now active too.
_ACTIVE_STATES = (
    ImportRunStatus.CREATED,
    ImportRunStatus.SCANNING,
    ImportRunStatus.SCANNED,
    ImportRunStatus.EXTRACTING,
    ImportRunStatus.CLASSIFYING,
    ImportRunStatus.CLASSIFIED,
    ImportRunStatus.DEDUPING,
    ImportRunStatus.PROPOSING,
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


# ------------------------------------------------------------------- S-ing-3: dedup + propose


async def included_files_with_context(
    session: AsyncSession, run_id: uuid.UUID, classifier_version: str | None
) -> Sequence[tuple[ImportFile, ImportExtract | None, ImportClassification | None]]:
    """Every INCLUDED file + its extract + its classification, ordered by ``(rel_path, id)`` (a
    stable total order so the in-memory dedup/propose iteration is itself deterministic). The
    classification join is pinned to the run's ``classifier_version`` (the S-ing-2 §6.6 rule)."""
    stmt = (
        select(ImportFile, ImportExtract, ImportClassification)
        .outerjoin(
            ImportExtract,
            and_(ImportExtract.run_id == run_id, ImportExtract.file_id == ImportFile.id),
        )
        .outerjoin(
            ImportClassification,
            and_(
                ImportClassification.run_id == run_id,
                ImportClassification.file_id == ImportFile.id,
                ImportClassification.classifier_version == classifier_version,
            ),
        )
        .where(ImportFile.run_id == run_id, ImportFile.included_candidate.is_(True))
        .order_by(ImportFile.rel_path, ImportFile.id)
    )
    return [(f, e, c) for f, e, c in (await session.execute(stmt)).all()]


async def replace_dedup(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    org_id: uuid.UUID,
    clusters: Sequence[dict[str, Any]],
    families: Sequence[dict[str, Any]],
) -> None:
    """Atomically REPLACE a run's dedup output (doc 09 §7) — DELETE the existing clusters + families
    for the run, then bulk-INSERT the freshly computed set, in ONE transaction (the caller commits).
    The UNIQUE keys backstop a racing-twin re-delivery; the single-active-run lock prevents one."""
    await session.execute(delete(ImportDupeCluster).where(ImportDupeCluster.run_id == run_id))
    await session.execute(delete(ImportVersionFamily).where(ImportVersionFamily.run_id == run_id))
    if clusters:
        stmt = pg_insert(ImportDupeCluster).values(
            [{"org_id": org_id, "run_id": run_id, **c} for c in clusters]
        )
        await session.execute(
            stmt.on_conflict_do_nothing(constraint="uq_import_dupe_cluster_run_method_canon")
        )
    if families:
        stmt = pg_insert(ImportVersionFamily).values(
            [{"org_id": org_id, "run_id": run_id, **f} for f in families]
        )
        await session.execute(
            stmt.on_conflict_do_nothing(constraint="uq_import_version_family_run_family_key")
        )


async def replace_proposals(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    org_id: uuid.UUID,
    nodes: Sequence[dict[str, Any]],
) -> None:
    """Atomically REPLACE a run's proposal nodes (doc 09 §8) — DELETE then bulk-INSERT, one txn."""
    await session.execute(delete(ImportProposalNode).where(ImportProposalNode.run_id == run_id))
    if nodes:
        stmt = pg_insert(ImportProposalNode).values(
            [{"org_id": org_id, "run_id": run_id, **n} for n in nodes]
        )
        await session.execute(
            stmt.on_conflict_do_nothing(constraint="uq_import_proposal_node_run_file")
        )


async def compute_dedup_counts(session: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    """The §10 dedup summary as SQL aggregates over the persisted clusters/families. Namespaced
    a ``dedup`` block so it never clobbers the scan-stage ``exact_dup_clusters``/``exact_dup_files``
    (the raw sha256 pre-flight) — both are intentionally present and mean different things."""
    by_method = {
        _enum_key(m): int(c)
        for m, c in (
            await session.execute(
                select(ImportDupeCluster.method, func.count())
                .where(ImportDupeCluster.run_id == run_id)
                .group_by(ImportDupeCluster.method)
            )
        ).all()
    }
    # redundant = non-canonical members = Σ(len(member_file_ids) - 1) over every cluster.
    redundant_files = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(func.array_length(ImportDupeCluster.member_file_ids, 1) - 1), 0
                )
            ).where(ImportDupeCluster.run_id == run_id)
        )
    ).scalar_one()
    version_families = (
        await session.execute(select(func.count()).where(ImportVersionFamily.run_id == run_id))
    ).scalar_one()
    superseded_files = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(func.array_length(ImportVersionFamily.ordered_member_file_ids, 1) - 1),
                    0,
                )
            ).where(ImportVersionFamily.run_id == run_id)
        )
    ).scalar_one()
    return {
        "dedup": {
            "by_method": by_method,
            "redundant_files": int(redundant_files),
            "version_families": int(version_families),
            "superseded_files": int(superseded_files),
        }
    }


async def compute_proposal_counts(session: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    """The §10 proposal summary as SQL aggregates over the persisted nodes (the keep-items)."""
    keep_items = (
        await session.execute(select(func.count()).where(ImportProposalNode.run_id == run_id))
    ).scalar_one()
    conflicts = (
        await session.execute(
            select(func.count()).where(
                ImportProposalNode.run_id == run_id,
                cast(ImportProposalNode.conflict_flags, Text) != "{}",
            )
        )
    ).scalar_one()
    needs_identifier = (
        await session.execute(
            select(func.count()).where(
                ImportProposalNode.run_id == run_id,
                ImportProposalNode.conflict_flags.has_key("needs_identifier"),
            )
        )
    ).scalar_one()
    return {
        "proposal": {
            "keep_items": int(keep_items),
            "conflicts": int(conflicts),
            "needs_identifier": int(needs_identifier),
        }
    }


async def list_dupe_clusters(
    session: AsyncSession, run_id: uuid.UUID
) -> Sequence[ImportDupeCluster]:
    """All dedup clusters for a run (the review read surface), by method then canonical."""
    return (
        (
            await session.execute(
                select(ImportDupeCluster)
                .where(ImportDupeCluster.run_id == run_id)
                .order_by(ImportDupeCluster.method, ImportDupeCluster.canonical_file_id)
            )
        )
        .scalars()
        .all()
    )


async def list_version_families(
    session: AsyncSession, run_id: uuid.UUID
) -> Sequence[ImportVersionFamily]:
    """All version families for a run (the review read surface), ordered by family_key."""
    return (
        (
            await session.execute(
                select(ImportVersionFamily)
                .where(ImportVersionFamily.run_id == run_id)
                .order_by(ImportVersionFamily.family_key)
            )
        )
        .scalars()
        .all()
    )


async def get_file_membership(
    session: AsyncSession, run_id: uuid.UUID, file_id: uuid.UUID
) -> tuple[Sequence[ImportDupeCluster], ImportVersionFamily | None, ImportProposalNode | None]:
    """A file's dedup/family/proposal context for the per-file review detail: the cluster(s) it
    belongs to, the family it belongs to, and its proposal node (if it is a keep-item)."""
    clusters = (
        (
            await session.execute(
                select(ImportDupeCluster).where(
                    ImportDupeCluster.run_id == run_id,
                    ImportDupeCluster.member_file_ids.contains([file_id]),
                )
            )
        )
        .scalars()
        .all()
    )
    family = (
        await session.execute(
            select(ImportVersionFamily).where(
                ImportVersionFamily.run_id == run_id,
                ImportVersionFamily.ordered_member_file_ids.contains([file_id]),
            )
        )
    ).scalar_one_or_none()
    node = (
        await session.execute(
            select(ImportProposalNode).where(
                ImportProposalNode.run_id == run_id, ImportProposalNode.file_id == file_id
            )
        )
    ).scalar_one_or_none()
    return clusters, family, node


# --------------------------------------------------------------------------- S-ing-4 review


async def get_file(
    session: AsyncSession, run_id: uuid.UUID, file_id: uuid.UUID
) -> ImportFile | None:
    """A single inventory row scoped to its run (validate a decision target ∈ the run)."""
    return (
        await session.execute(
            select(ImportFile).where(ImportFile.run_id == run_id, ImportFile.id == file_id)
        )
    ).scalar_one_or_none()


async def get_dupe_cluster(
    session: AsyncSession, run_id: uuid.UUID, cluster_id: uuid.UUID
) -> ImportDupeCluster | None:
    return (
        await session.execute(
            select(ImportDupeCluster).where(
                ImportDupeCluster.run_id == run_id, ImportDupeCluster.id == cluster_id
            )
        )
    ).scalar_one_or_none()


async def get_version_family(
    session: AsyncSession, run_id: uuid.UUID, family_id: uuid.UUID
) -> ImportVersionFamily | None:
    return (
        await session.execute(
            select(ImportVersionFamily).where(
                ImportVersionFamily.run_id == run_id, ImportVersionFamily.id == family_id
            )
        )
    ).scalar_one_or_none()


async def list_proposal_nodes(
    session: AsyncSession, run_id: uuid.UUID
) -> Sequence[ImportProposalNode]:
    """All keep-item proposal nodes for a run (the checklist + review fold read them at once)."""
    return (
        (
            await session.execute(
                select(ImportProposalNode).where(ImportProposalNode.run_id == run_id)
            )
        )
        .scalars()
        .all()
    )


async def insert_decision(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    action: ImportDecisionAction,
    decided_by: uuid.UUID,
    file_id: uuid.UUID | None = None,
    cluster_id: uuid.UUID | None = None,
    target_kind: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> ImportDecision:
    """Append one ``import_decision`` row (the caller commits). Append-only — never updated."""
    row = ImportDecision(
        org_id=org_id,
        run_id=run_id,
        file_id=file_id,
        cluster_id=cluster_id,
        target_kind=target_kind,
        action=action,
        before=before,
        after=after,
        idempotency_key=idempotency_key,
        decided_by=decided_by,
    )
    session.add(row)
    await session.flush()
    return row


async def find_decision_by_idem(
    session: AsyncSession, run_id: uuid.UUID, idempotency_key: str
) -> ImportDecision | None:
    """The existing decision for a replayed ``Idempotency-Key`` (the partial-UNIQUE replay)."""
    return (
        await session.execute(
            select(ImportDecision).where(
                ImportDecision.run_id == run_id,
                ImportDecision.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def list_decisions(session: AsyncSession, run_id: uuid.UUID) -> Sequence[ImportDecision]:
    """The run's full decision log, newest-first (the audit/review history + the fold source)."""
    return (
        (
            await session.execute(
                select(ImportDecision)
                .where(ImportDecision.run_id == run_id)
                .order_by(ImportDecision.decided_at.desc(), ImportDecision.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def decisions_for_file(
    session: AsyncSession, run_id: uuid.UUID, file_id: uuid.UUID
) -> Sequence[ImportDecision]:
    """One file's decisions, newest-first (the per-file effective-state fold)."""
    return (
        (
            await session.execute(
                select(ImportDecision)
                .where(ImportDecision.run_id == run_id, ImportDecision.file_id == file_id)
                .order_by(ImportDecision.decided_at.desc(), ImportDecision.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def vault_identifier_collisions(
    session: AsyncSession, org_id: uuid.UUID, identifiers: Sequence[str]
) -> dict[str, str]:
    """``{identifier: documented_information_id}`` for a proposed identifier that already exists in
    the vault (as ``identifier`` or ``legacy_identifier``) — the §11.3 ``collides_with_vault_doc``
    check over the EFFECTIVE (folded) identifiers (the ``propose._detect_conflicts`` query, reused
    for the pre-commit checklist)."""
    wanted = [i for i in {x for x in identifiers if x}]
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(
                DocumentedInformation.id,
                DocumentedInformation.identifier,
                DocumentedInformation.legacy_identifier,
            ).where(
                DocumentedInformation.org_id == org_id,
                or_(
                    DocumentedInformation.identifier.in_(wanted),
                    DocumentedInformation.legacy_identifier.in_(wanted),
                ),
            )
        )
    ).all()
    hits: dict[str, str] = {}
    wanted_set = set(wanted)
    for doc_id, ident, legacy in rows:
        if ident in wanted_set:
            hits[ident] = str(doc_id)
        if legacy in wanted_set:
            hits[legacy] = str(doc_id)
    return hits


# --------------------------------------------------------------------------- S-ing-5 commit helpers


async def get_document_types_by_codes(
    session: AsyncSession, org_id: uuid.UUID, codes: set[str]
) -> dict[str, DocumentType]:
    """``{code: DocumentType}`` for the org's resolvable type codes (uq_document_type_org_id_code).
    Unresolvable codes are simply absent — the commit DOCUMENT branch fails those items honestly."""
    if not codes:
        return {}
    rows = (
        (
            await session.execute(
                select(DocumentType).where(
                    DocumentType.org_id == org_id, DocumentType.code.in_(codes)
                )
            )
        )
        .scalars()
        .all()
    )
    return {dt.code: dt for dt in rows}


async def vault_effective_singleton_type_ids(
    session: AsyncSession, org_id: uuid.UUID, type_ids: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Of ``type_ids``, the singleton document-types that ALREADY have an Effective instance in the
    vault (the R25 pre-commit guard — a 2nd Effective singleton of the same type would 23505)."""
    if not type_ids:
        return set()
    rows = (
        (
            await session.execute(
                select(DocumentedInformation.document_type_id)
                .where(
                    DocumentedInformation.org_id == org_id,
                    DocumentedInformation.document_type_id.in_(type_ids),
                    DocumentedInformation.current_state == DocumentCurrentState.Effective,
                    DocumentedInformation.is_singleton.is_(True),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return {tid for tid in rows if tid is not None}


async def get_clauses_by_numbers(
    session: AsyncSession, framework_id: uuid.UUID, numbers: set[str]
) -> dict[str, uuid.UUID]:
    """``{clause_number: clause_id}`` for a framework (uq_clause_framework_id_number) — resolves the
    folded clause_numbers ("8.4") to clause ids for the commit clause_mapping rows. Unmatched
    numbers are absent (the commit skips + reports them)."""
    if not numbers:
        return {}
    rows = (
        await session.execute(
            select(Clause.number, Clause.id).where(
                Clause.framework_id == framework_id, Clause.number.in_(numbers)
            )
        )
    ).all()
    return {number: cid for number, cid in rows}


async def get_processes_by_names(
    session: AsyncSession, org_id: uuid.UUID, names: set[str]
) -> dict[str, uuid.UUID]:
    """``{process_name: process_id}`` for the org (uq_process_org_id_name) — resolves the folded
    process_names to ids for the commit process_link rows. Unmatched names are absent (skipped)."""
    if not names:
        return {}
    rows = (
        await session.execute(
            select(Process.name, Process.id).where(
                Process.org_id == org_id, Process.name.in_(names)
            )
        )
    ).all()
    return {name: pid for name, pid in rows}


async def get_base(session: AsyncSession, di_id: uuid.UUID) -> DocumentedInformation | None:
    """The ``documented_information`` base row by id (the commit RECORD branch sets
    import_provenance / legacy_identifier on it after capture_record returns the Record subtype)."""
    return await session.get(DocumentedInformation, di_id)


async def get_commit_result(
    session: AsyncSession, run_id: uuid.UUID, file_id: uuid.UUID
) -> ImportCommitResult | None:
    """The ledger row for a (run, file), if any — the per-item idempotency check (skip done)."""
    return (
        await session.execute(
            select(ImportCommitResult).where(
                ImportCommitResult.run_id == run_id, ImportCommitResult.file_id == file_id
            )
        )
    ).scalar_one_or_none()


async def claim_commit_result(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    file_id: uuid.UUID,
    vault_document_id: uuid.UUID | None,
    vault_version_id: uuid.UUID | None,
) -> bool:
    """The per-item single-flight + idempotency CLAIM (S-ing-5): atomically record SUCCESS iff no
    row yet OR the existing row is ``failed`` (a resume retrying it). Returns True if THIS call won
    (a row was inserted/updated → commit the item), False if a peer already committed it
    (success/noop → the caller must roll back its half-built vault rows; doc 09 §10.2). This makes
    concurrent commit workers exactly-once WITHOUT an advisory lock: the loser's INSERT blocks on
    the winner's uncommitted row, then the ``WHERE result='failed'`` guard makes its DO UPDATE a
    no-op (no row returned). The claim is the LAST write in the per-item txn, after the doc/etc."""
    stmt = (
        pg_insert(ImportCommitResult)
        .values(
            org_id=org_id,
            run_id=run_id,
            file_id=file_id,
            result=ImportCommitResultStatus.SUCCESS,
            vault_document_id=vault_document_id,
            vault_version_id=vault_version_id,
            error=None,
        )
        .on_conflict_do_update(
            constraint="uq_import_commit_result_run_file",
            set_={
                "result": ImportCommitResultStatus.SUCCESS,
                "vault_document_id": vault_document_id,
                "vault_version_id": vault_version_id,
                "error": None,
                "committed_at": func.now(),
            },
            where=ImportCommitResult.result == ImportCommitResultStatus.FAILED,
        )
        .returning(ImportCommitResult.id)
    )
    return (await session.execute(stmt)).first() is not None


async def record_failed_result(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    file_id: uuid.UUID,
    error: str,
) -> None:
    """Record an isolated per-item FAILURE (ON CONFLICT DO UPDATE — re-fail on a resume); never
    clobbers a peer-committed SUCCESS (the ``WHERE result != 'success'`` guard). Caller commits."""
    stmt = (
        pg_insert(ImportCommitResult)
        .values(
            org_id=org_id,
            run_id=run_id,
            file_id=file_id,
            result=ImportCommitResultStatus.FAILED,
            error=error,
        )
        .on_conflict_do_update(
            constraint="uq_import_commit_result_run_file",
            set_={
                "result": ImportCommitResultStatus.FAILED,
                "error": error,
                "committed_at": func.now(),
            },
            where=ImportCommitResult.result != ImportCommitResultStatus.SUCCESS,
        )
    )
    await session.execute(stmt)


async def list_commit_results(
    session: AsyncSession, run_id: uuid.UUID
) -> Sequence[ImportCommitResult]:
    """All ledger rows for a run (the terminal tally + the Import Report disposition table)."""
    return (
        (
            await session.execute(
                select(ImportCommitResult).where(ImportCommitResult.run_id == run_id)
            )
        )
        .scalars()
        .all()
    )


async def max_commit_progress(session: AsyncSession, run_id: uuid.UUID) -> Any:
    """``MAX(import_commit_result.committed_at)`` for a run, or None — the commit reaper's
    progress-liveness signal (a Committing run with no recent ledger row is wedged)."""
    return (
        await session.execute(
            select(func.max(ImportCommitResult.committed_at)).where(
                ImportCommitResult.run_id == run_id
            )
        )
    ).scalar_one_or_none()
