"""Read helpers for the DCR family (slice S-dcr-1). Loads are org-checked in the service (the
capa/audits-repo precedent). The DCR is an own table with a human ``DCR-{YYYY}-{SEQ}``
``identifier``
column (not a record subtype), so no ``documented_information`` join is needed to surface it."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._dcr_enums import DcrChangeType, DcrReasonClass, DcrState
from ...db.models.dcr import Dcr
from ...db.models.dcr_stage_event import DcrStageEvent


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
