"""Read helpers for the CAPA / NCR / Complaint family (S-capa-1). Loads are org-checked in the
service (the audits-repo precedent). CAPA + complaint identifiers live on the base
``documented_information`` row (they are record subtypes), so list/get join it to surface the
human ``REC-{AREA}-{SEQ}`` identifier; NCR carries its own ``identifier`` column."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models.capa import Capa
from ...db.models.capa_stage import CapaStage
from ...db.models.complaint import Complaint
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.ncr import Ncr
from ...db.models.system_config import SystemConfig


async def get_capa(
    session: AsyncSession, capa_id: uuid.UUID, *, for_update: bool = False
) -> Capa | None:
    if for_update:
        return (
            await session.execute(select(Capa).where(Capa.id == capa_id).with_for_update())
        ).scalar_one_or_none()
    return await session.get(Capa, capa_id)


async def get_complaint(
    session: AsyncSession, complaint_id: uuid.UUID, *, for_update: bool = False
) -> Complaint | None:
    if for_update:
        return (
            await session.execute(
                select(Complaint).where(Complaint.id == complaint_id).with_for_update()
            )
        ).scalar_one_or_none()
    return await session.get(Complaint, complaint_id)


async def get_ncr(
    session: AsyncSession, ncr_id: uuid.UUID, *, for_update: bool = False
) -> Ncr | None:
    if for_update:
        return (
            await session.execute(select(Ncr).where(Ncr.id == ncr_id).with_for_update())
        ).scalar_one_or_none()
    return await session.get(Ncr, ncr_id)


async def get_identifier(session: AsyncSession, record_id: uuid.UUID) -> str | None:
    """The human identifier of a record subtype (CAPA / complaint), read off the base row."""
    base = await session.get(DocumentedInformation, record_id)
    return base.identifier if base is not None else None


async def list_capa_stages(session: AsyncSession, capa_id: uuid.UUID) -> Sequence[CapaStage]:
    return (
        (
            await session.execute(
                select(CapaStage)
                .where(CapaStage.capa_id == capa_id)
                .order_by(CapaStage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )


async def stages_with_evidence(
    session: AsyncSession, stage_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """The subset of ``stage_ids`` that carry ≥1 ``evidence_for_link(target_type=CAPA_STAGE)`` row —
    the M4 "implemented-action-with-evidence" / "effectiveness-evidence" gate (S-capa-3). A record's
    correction/supersession does NOT auto-remove its evidence links, so a present link counts (the
    capture is immutable; the auditor's link is the deliberate promotion)."""
    if not stage_ids:
        return set()
    rows = await session.execute(
        select(EvidenceForLink.target_id).where(
            EvidenceForLink.target_type == EvidenceForTargetType.CAPA_STAGE,
            EvidenceForLink.target_id.in_(list(stage_ids)),
        )
    )
    return {tid for (tid,) in rows.all()}


async def list_stage_evidence(
    session: AsyncSession, stage_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Evidence links pointing AT each capa_stage (target_type=capa_stage), joined to the linking
    record's identifier — one query for all of a CAPA's stages. The M4 close gate needs ≥1 link
    on the current-cycle Implement + Verify stages; the drawer renders the list per stage."""
    if not stage_ids:
        return {}
    rows = (
        await session.execute(
            select(EvidenceForLink, DocumentedInformation.identifier)
            .join(DocumentedInformation, DocumentedInformation.id == EvidenceForLink.record_id)
            .where(
                EvidenceForLink.target_type == EvidenceForTargetType.CAPA_STAGE,
                EvidenceForLink.target_id.in_(stage_ids),
            )
            .order_by(EvidenceForLink.created_at)
        )
    ).all()
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for link, identifier in rows:
        out.setdefault(link.target_id, []).append(
            {
                "id": str(link.id),
                "record_id": str(link.record_id),
                "record_identifier": identifier,
                "link_reason": link.link_reason,
                "created_at": link.created_at.isoformat() if link.created_at else None,
            }
        )
    return out


async def allow_capa_self_verify(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """The org's severity-aware SoD-4 relaxation flag (S-capa-3). ``False`` (STRICT — verifier ≠
    implementer enforced) when no ``system_config`` row exists yet, so the default fails closed (the
    ``allow_self_disposition`` precedent). Only Minor CAPAs honour a True value — Critical / Major
    always hard-enforce."""
    value = await session.scalar(
        select(SystemConfig.allow_capa_self_verify).where(SystemConfig.org_id == org_id)
    )
    return bool(value)


async def list_capas(
    session: AsyncSession, org_id: uuid.UUID
) -> Sequence[tuple[Capa, str | None, str | None, datetime | None]]:
    rows = await session.execute(
        select(
            Capa,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            DocumentedInformation.created_at,
        )
        .join(DocumentedInformation, DocumentedInformation.id == Capa.id)
        .where(Capa.org_id == org_id)
        .order_by(DocumentedInformation.created_at.desc())
    )
    return [(c, ident, title, created) for c, ident, title, created in rows.all()]


async def get_capa_header(
    session: AsyncSession, capa_id: uuid.UUID
) -> tuple[str | None, str | None, datetime | None] | None:
    """(identifier, title, created_at) for a CAPA's record — one row, for the detail serializer."""
    row = (
        await session.execute(
            select(
                DocumentedInformation.identifier,
                DocumentedInformation.title,
                DocumentedInformation.created_at,
            ).where(DocumentedInformation.id == capa_id)
        )
    ).first()
    return (row[0], row[1], row[2]) if row else None


async def list_complaints(
    session: AsyncSession, org_id: uuid.UUID
) -> Sequence[tuple[Complaint, str]]:
    rows = await session.execute(
        select(Complaint, DocumentedInformation.identifier)
        .join(DocumentedInformation, DocumentedInformation.id == Complaint.id)
        .where(Complaint.org_id == org_id)
        .order_by(DocumentedInformation.created_at.desc())
    )
    return [(c, ident) for c, ident in rows.all()]


async def list_ncrs(session: AsyncSession, org_id: uuid.UUID) -> Sequence[Ncr]:
    return (
        (
            await session.execute(
                select(Ncr).where(Ncr.org_id == org_id).order_by(Ncr.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
