"""Read helpers for the CAPA / NCR / Complaint family (S-capa-1). Loads are org-checked in the
service (the audits-repo precedent). CAPA + complaint identifiers live on the base
``documented_information`` row (they are record subtypes), so list/get join it to surface the
human ``REC-{AREA}-{SEQ}`` identifier; NCR carries its own ``identifier`` column."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.capa import Capa
from ...db.models.capa_stage import CapaStage
from ...db.models.complaint import Complaint
from ...db.models.documented_information import DocumentedInformation
from ...db.models.ncr import Ncr


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


async def list_capas(session: AsyncSession, org_id: uuid.UUID) -> Sequence[tuple[Capa, str]]:
    rows = await session.execute(
        select(Capa, DocumentedInformation.identifier)
        .join(DocumentedInformation, DocumentedInformation.id == Capa.id)
        .where(Capa.org_id == org_id)
        .order_by(DocumentedInformation.created_at.desc())
    )
    return [(c, ident) for c, ident in rows.all()]


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
