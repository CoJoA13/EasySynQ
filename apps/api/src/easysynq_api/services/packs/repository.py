"""Evidence-pack DB access: scope resolution + pack/item CRUD (slice S-pack-1, doc 06 §7).

The resolution queries are the heart of the slice. Records reach clauses ONLY via
``evidence_for_link`` (capture adds no ``clause_mapping``), so each scope is a **UNION of 2 legs**:
the explicit evidence-for links AND records under a clause-mapped/process-linked source document.
Everything is ``org_id``-scoped; ``Record`` rows are intrinsically ``kind=RECORD`` (the shared-PK
subtype) so no extra kind filter is needed on the record side.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, asc, cast, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models._retention_enums import DispositionAction
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.disposition_event import DispositionEvent
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.evidence_pack import EvidencePack
from ...db.models.pack_item import PackItem
from ...db.models.pack_share_link import PackShareLink
from ...db.models.process_link import ProcessLink
from ...db.models.record import Record

# --- pack header CRUD --------------------------------------------------------------------


async def get_pack(
    session: AsyncSession, pack_id: uuid.UUID, *, for_update: bool = False
) -> EvidencePack | None:
    if for_update:
        return (
            await session.execute(
                select(EvidencePack).where(EvidencePack.id == pack_id).with_for_update()
            )
        ).scalar_one_or_none()
    return await session.get(EvidencePack, pack_id)


async def list_packs(session: AsyncSession, org_id: uuid.UUID, *, limit: int) -> list[EvidencePack]:
    return list(
        (
            await session.execute(
                select(EvidencePack)
                .where(EvidencePack.org_id == org_id)
                .order_by(desc(EvidencePack.created_at))
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def list_pack_items(session: AsyncSession, pack_id: uuid.UUID) -> list[PackItem]:
    return list(
        (
            await session.execute(
                select(PackItem)
                .where(PackItem.pack_id == pack_id)
                .order_by(asc(PackItem.created_at))
            )
        )
        .scalars()
        .all()
    )


async def delete_pack_items(session: AsyncSession, pack_id: uuid.UUID) -> None:
    """Drop all membership rows for a pack — preview and build both rebuild from scratch so the seal
    is over one coherent set (the TOCTOU fix: build never merges stale preview rows)."""
    await session.execute(delete(PackItem).where(PackItem.pack_id == pack_id))


# --- content resolution ------------------------------------------------------------------


async def _clause_candidate_ids(
    session: AsyncSession, org_id: uuid.UUID, clause_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """CLAUSE scope: UNION of (records evidence-for the clause) AND (records under a clause-mapped
    source document — records do NOT auto-inherit their source-doc's clause mappings)."""
    if not clause_ids:
        return set()
    leg_a = (
        await session.scalars(
            select(EvidenceForLink.record_id).where(
                EvidenceForLink.org_id == org_id,
                EvidenceForLink.target_type == EvidenceForTargetType.CLAUSE,
                EvidenceForLink.target_id.in_(clause_ids),
            )
        )
    ).all()
    leg_b = (
        await session.scalars(
            select(Record.id)
            .join(
                ClauseMapping,
                ClauseMapping.documented_information_id == Record.source_document_id,
            )
            .where(Record.org_id == org_id, ClauseMapping.clause_id.in_(clause_ids))
        )
    ).all()
    return set(leg_a) | set(leg_b)


async def _process_candidate_ids(
    session: AsyncSession, org_id: uuid.UUID, process_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """PROCESS scope: UNION of (records evidence-for the process) AND (records under a
    process-linked source document)."""
    if not process_ids:
        return set()
    leg_a = (
        await session.scalars(
            select(EvidenceForLink.record_id).where(
                EvidenceForLink.org_id == org_id,
                EvidenceForLink.target_type == EvidenceForTargetType.PROCESS,
                EvidenceForLink.target_id.in_(process_ids),
            )
        )
    ).all()
    leg_b = (
        await session.scalars(
            select(Record.id)
            .join(ProcessLink, ProcessLink.documented_information_id == Record.source_document_id)
            .where(Record.org_id == org_id, ProcessLink.process_id.in_(process_ids))
        )
    ).all()
    return set(leg_a) | set(leg_b)


async def resolve_candidates(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    scope_kind: str,
    scope_ids: list[uuid.UUID],
    period_start: datetime.date | None,
    period_end: datetime.date | None,
) -> list[tuple[Record, DocumentedInformation]]:
    """Resolve the in-scope record candidates (Record ⨝ base), DATE overlay applied on
    ``captured_at`` (the cast-to-date window is inclusive on both ends)."""
    if scope_kind == "CLAUSE":
        ids = await _clause_candidate_ids(session, org_id, scope_ids)
    else:  # PROCESS
        ids = await _process_candidate_ids(session, org_id, scope_ids)
    if not ids:
        return []
    stmt = (
        select(Record, DocumentedInformation)
        .join(DocumentedInformation, Record.id == DocumentedInformation.id)
        .where(Record.org_id == org_id, Record.id.in_(ids))
    )
    if period_start is not None:
        stmt = stmt.where(cast(Record.captured_at, Date) >= period_start)
    if period_end is not None:
        stmt = stmt.where(cast(Record.captured_at, Date) <= period_end)
    rows = (await session.execute(stmt.order_by(asc(Record.captured_at)))).all()
    return [(r, d) for r, d in rows]


async def record_process_ids(session: AsyncSession, record: Record) -> set[str]:
    """The processes a record is bound to — for the PDP ``ResourceContext`` (so a PROCESS-scoped
    ``record.read`` grant is honored): its evidence-for PROCESS links + its source-doc links."""
    via_link = (
        await session.scalars(
            select(EvidenceForLink.target_id).where(
                EvidenceForLink.record_id == record.id,
                EvidenceForLink.target_type == EvidenceForTargetType.PROCESS,
            )
        )
    ).all()
    via_doc: list[uuid.UUID] = []
    if record.source_document_id is not None:
        via_doc = list(
            (
                await session.scalars(
                    select(ProcessLink.process_id).where(
                        ProcessLink.documented_information_id == record.source_document_id
                    )
                )
            ).all()
        )
    return {str(x) for x in (*via_link, *via_doc)}


async def has_destroy_tombstone(session: AsyncSession, record_id: uuid.UUID) -> bool:
    """``True`` if the record's evidence was physically destroyed (a DESTROY / WORM-destroy
    disposition tombstone) — the R28 genuine-absence signal (NOT "no evidence_blob rows", which a
    valid form-only record also has)."""
    count = await session.scalar(
        select(func.count())
        .select_from(DispositionEvent)
        .where(
            DispositionEvent.record_id == record_id,
            or_(
                DispositionEvent.is_worm_destroy.is_(True),
                DispositionEvent.action == DispositionAction.DESTROY,
            ),
        )
    )
    return bool(count)


async def process_clause_ids(
    session: AsyncSession, org_id: uuid.UUID, process_ids: list[uuid.UUID]
) -> set[str]:
    """The clauses transitively in scope for a PROCESS pack: process → process_link documents →
    those documents' clause_mappings. Used to scope the gap report (there is no direct
    process→clause edge in the model)."""
    if not process_ids:
        return set()
    doc_subq = select(ProcessLink.documented_information_id).where(
        ProcessLink.org_id == org_id, ProcessLink.process_id.in_(process_ids)
    )
    clause_ids = (
        await session.scalars(
            select(ClauseMapping.clause_id).where(
                ClauseMapping.org_id == org_id,
                ClauseMapping.documented_information_id.in_(doc_subq),
            )
        )
    ).all()
    return {str(c) for c in clause_ids}


async def get_document_versions(
    session: AsyncSession, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, DocumentVersion]:
    if not version_ids:
        return {}
    rows = (
        await session.scalars(select(DocumentVersion).where(DocumentVersion.id.in_(version_ids)))
    ).all()
    return {v.id: v for v in rows}


async def get_records_with_base(
    session: AsyncSession, record_ids: list[uuid.UUID]
) -> list[tuple[Record, DocumentedInformation]]:
    """Record ⨝ its shared-PK base, for the portfolio's traceability index (Stage 2)."""
    if not record_ids:
        return []
    rows = (
        await session.execute(
            select(Record, DocumentedInformation)
            .join(DocumentedInformation, Record.id == DocumentedInformation.id)
            .where(Record.id.in_(record_ids))
            .order_by(asc(Record.captured_at))
        )
    ).all()
    return [(r, d) for r, d in rows]


async def get_base_docs(
    session: AsyncSession, doc_ids: list[uuid.UUID]
) -> dict[uuid.UUID, DocumentedInformation]:
    """The governing documents of a set of pinned versions (for the portfolio section headers)."""
    if not doc_ids:
        return {}
    rows = (
        await session.scalars(
            select(DocumentedInformation).where(DocumentedInformation.id.in_(doc_ids))
        )
    ).all()
    return {d.id: d for d in rows}


# --- share-link CRUD (S-pack-2 external delivery, doc 06 §7.4) ---------------------------


async def get_share_link(
    session: AsyncSession, link_id: uuid.UUID, *, for_update: bool = False
) -> PackShareLink | None:
    """Load a share link by its id (the id rides in the verified token payload). ``for_update``
    re-checks the revocation state under a row lock for the revoke transition."""
    if for_update:
        return (
            await session.execute(
                select(PackShareLink).where(PackShareLink.id == link_id).with_for_update()
            )
        ).scalar_one_or_none()
    return await session.get(PackShareLink, link_id)


async def list_share_links(session: AsyncSession, pack_id: uuid.UUID) -> list[PackShareLink]:
    return list(
        (
            await session.execute(
                select(PackShareLink)
                .where(PackShareLink.pack_id == pack_id)
                .order_by(desc(PackShareLink.created_at))
            )
        )
        .scalars()
        .all()
    )
