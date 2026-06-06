"""Read helpers for the DCR family (slice S-dcr-1). Loads are org-checked in the service (the
capa/audits-repo precedent). The DCR is an own table with a human ``DCR-{YYYY}-{SEQ}``
``identifier``
column (not a record subtype), so no ``documented_information`` join is needed to surface it."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._dcr_enums import DcrChangeType, DcrReasonClass, DcrState
from ...db.models.capa import Capa
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.dcr import Dcr
from ...db.models.dcr_stage_event import DcrStageEvent
from ...db.models.document_link import DocumentLink
from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.impact_assessment import ImpactAssessment
from ...db.models.record import Record

# Cap the records-produced-under id list surfaced in where-used (§7.2: a count + a sample;
# historical records are immutable + read-only — the full set is not needed in the panel).
_RECORDS_SAMPLE_CAP = 50


async def get_dcr(
    session: AsyncSession, dcr_id: uuid.UUID, *, for_update: bool = False
) -> Dcr | None:
    if for_update:
        return (
            await session.execute(select(Dcr).where(Dcr.id == dcr_id).with_for_update())
        ).scalar_one_or_none()
    return await session.get(Dcr, dcr_id)


async def list_dcrs(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    state: DcrState | None = None,
    change_type: DcrChangeType | None = None,
    target_document_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    reason_class: DcrReasonClass | None = None,
) -> Sequence[Dcr]:
    stmt = select(Dcr).where(Dcr.org_id == org_id)
    if state is not None:
        stmt = stmt.where(Dcr.state == state)
    if change_type is not None:
        stmt = stmt.where(Dcr.change_type == change_type)
    if target_document_id is not None:
        stmt = stmt.where(Dcr.target_document_id == target_document_id)
    if created_by is not None:
        stmt = stmt.where(Dcr.created_by == created_by)
    if reason_class is not None:
        stmt = stmt.where(Dcr.reason_class == reason_class)
    stmt = stmt.order_by(Dcr.created_at.desc())
    return (await session.execute(stmt)).scalars().all()


async def list_dcr_stage_events(
    session: AsyncSession, dcr_id: uuid.UUID
) -> Sequence[DcrStageEvent]:
    return (
        (
            await session.execute(
                select(DcrStageEvent)
                .where(DcrStageEvent.dcr_id == dcr_id)
                .order_by(DcrStageEvent.occurred_at.asc())
            )
        )
        .scalars()
        .all()
    )


# --- where-used / impact reads (S-dcr-2) ------------------------------------------------------


async def _links_in_direction(
    session: AsyncSession, doc_id: uuid.UUID, *, outbound: bool
) -> list[dict[str, Any]]:
    """document_link rows touching ``doc_id`` in one direction, joined with the OTHER document + its
    level. ``outbound`` = links where from=doc (the ``to`` doc is the neighbour); else to=doc."""
    near = DocumentLink.from_document_id if outbound else DocumentLink.to_document_id
    far = DocumentLink.to_document_id if outbound else DocumentLink.from_document_id
    rows = await session.execute(
        select(
            DocumentLink.id,
            DocumentLink.link_type,
            DocumentedInformation.id,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            DocumentedInformation.current_state,
            DocumentType.document_level,
        )
        .join(DocumentedInformation, DocumentedInformation.id == far)
        .outerjoin(DocumentType, DocumentType.id == DocumentedInformation.document_type_id)
        .where(near == doc_id)
        .order_by(DocumentedInformation.identifier.asc())
    )
    return [
        {
            "link_id": str(lid),
            "link_type": lt.value,
            "direction": "outbound" if outbound else "inbound",
            "document_id": str(did),
            "identifier": ident,
            "title": title,
            "current_state": state.value,
            "document_level": level.value if level is not None else None,
        }
        for lid, lt, did, ident, title, state, level in rows.all()
    ]


async def list_document_links(session: AsyncSession, doc_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every document_link touching ``doc_id`` (outbound + inbound), each joined with the neighbour
    document + its level + the direction — the raw rows the where-used projection buckets."""
    out = await _links_in_direction(session, doc_id, outbound=True)
    inbound = await _links_in_direction(session, doc_id, outbound=False)
    return out + inbound


async def records_produced_under(
    session: AsyncSession, doc_id: uuid.UUID
) -> tuple[int, list[dict[str, str]]]:
    """(count, capped sample) of Records whose ``source_document_id`` is ``doc_id`` (§7.2
    produced_under; historical records are immutable). The count is exact; the sample is capped."""
    total = (
        await session.execute(
            select(func.count()).select_from(Record).where(Record.source_document_id == doc_id)
        )
    ).scalar_one()
    sample = await session.execute(
        select(Record.id, DocumentedInformation.identifier)
        .join(DocumentedInformation, DocumentedInformation.id == Record.id)
        .where(Record.source_document_id == doc_id)
        .order_by(DocumentedInformation.created_at.desc())
        .limit(_RECORDS_SAMPLE_CAP)
    )
    return total, [{"id": str(rid), "identifier": ident} for rid, ident in sample.all()]


async def sole_star_coverage(
    session: AsyncSession, org_id: uuid.UUID, doc_id: uuid.UUID
) -> list[dict[str, str]]:
    """The ★ mandatory clauses for which ``doc_id`` is the SOLE Effective coverer — i.e. removing it
    leaves the clause with no Effective document (the doc 05 §7.3 'no replacement' leg). Counts
    any OTHER document with an Effective version mapped to the same ★ clause (the checklist
    coverage
    semantics: Effective = current_effective_version_id IS NOT NULL)."""
    # The ★ clauses this doc maps to.
    doc_star = (
        select(ClauseMapping.clause_id)
        .join(Clause, Clause.id == ClauseMapping.clause_id)
        .where(
            ClauseMapping.documented_information_id == doc_id, Clause.is_mandatory_star.is_(True)
        )
        .subquery()
    )
    # Other Effective docs mapped to each of those clauses.
    other_effective = (
        select(
            ClauseMapping.clause_id, func.count(func.distinct(DocumentedInformation.id)).label("n")
        )
        .join(
            DocumentedInformation,
            DocumentedInformation.id == ClauseMapping.documented_information_id,
        )
        .where(
            ClauseMapping.clause_id.in_(select(doc_star.c.clause_id)),
            ClauseMapping.documented_information_id != doc_id,
            DocumentedInformation.org_id == org_id,
            DocumentedInformation.current_effective_version_id.isnot(None),
        )
        .group_by(ClauseMapping.clause_id)
        .subquery()
    )
    rows = await session.execute(
        select(Clause.number, Clause.title)
        .join(doc_star, doc_star.c.clause_id == Clause.id)
        .outerjoin(other_effective, other_effective.c.clause_id == Clause.id)
        .where(func.coalesce(other_effective.c.n, 0) == 0)
        .order_by(Clause.number.asc())
    )
    return [{"number": num, "title": title} for num, title in rows.all()]


async def caused_by_links(session: AsyncSession, doc_id: uuid.UUID) -> list[dict[str, Any]]:
    """The CAPAs/findings that drove DCRs targeting ``doc_id`` (the §7.2 'Open CAPAs/findings' /
    caused_by edge): each DCR with a non-null ``source_link`` whose target is this doc. For a
    capa-typed link the CAPA's ``close_state`` is joined so the panel can flag still-open ones."""
    rows = await session.execute(
        select(
            Dcr.id,
            Dcr.identifier,
            Dcr.source_link_type,
            Dcr.source_link_id,
            Capa.close_state,
        )
        .outerjoin(Capa, Capa.id == Dcr.source_link_id)
        .where(Dcr.target_document_id == doc_id, Dcr.source_link_type.is_not(None))
        .order_by(Dcr.created_at.desc())
    )
    out: list[dict[str, Any]] = []
    for dcr_id, ident, slt, slid, close_state in rows.all():
        out.append(
            {
                "dcr_id": str(dcr_id),
                "dcr_identifier": ident,
                "source_link_type": slt.value if slt else None,
                "source_link_id": str(slid) if slid else None,
                # close_state only resolves for capa-typed links; None for finding/others.
                "capa_close_state": close_state.value if close_state is not None else None,
            }
        )
    return out


async def list_impact_assessments(
    session: AsyncSession, dcr_id: uuid.UUID
) -> Sequence[ImpactAssessment]:
    return (
        (
            await session.execute(
                select(ImpactAssessment)
                .where(ImpactAssessment.dcr_id == dcr_id)
                .order_by(ImpactAssessment.dimension.asc())
            )
        )
        .scalars()
        .all()
    )
