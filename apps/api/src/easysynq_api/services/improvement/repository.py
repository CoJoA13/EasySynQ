"""Read helpers for the Improvement Initiatives family (slice S-improvement-1). Loads are
org-checked in the service (the dcr/capa/audits-repo precedent). The initiative is an own table with
a human ``IMP-{YYYY}-{NNNN}`` ``identifier`` column (not a record subtype), so no
``documented_information`` join is needed to surface it."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._improvement_enums import ImprovementSource, ImprovementStage
from ...db.models.improvement_initiative import ImprovementInitiative
from ...db.models.improvement_initiative_stage_event import ImprovementInitiativeStageEvent


async def get_initiative(
    session: AsyncSession, initiative_id: uuid.UUID, *, for_update: bool = False
) -> ImprovementInitiative | None:
    if for_update:
        # populate_existing: the authz resolver already session.get-loaded the row into the request
        # session's identity map, so a plain locked load returns the STALE cached attributes (the
        # S-drift-1 trap). Force a re-read under the lock.
        return (
            await session.execute(
                select(ImprovementInitiative)
                .where(ImprovementInitiative.id == initiative_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    return await session.get(ImprovementInitiative, initiative_id)


async def list_initiatives(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    stage: ImprovementStage | None = None,
    source: ImprovementSource | None = None,
    owner_user_id: uuid.UUID | None = None,
    process_id: uuid.UUID | None = None,
) -> Sequence[ImprovementInitiative]:
    """List initiatives (newest first), org-scoped + optionally filtered. The endpoint additionally
    row-filters by the caller's ``improvement.read`` grant scope (the records/CAPA precedent)."""
    stmt = select(ImprovementInitiative).where(ImprovementInitiative.org_id == org_id)
    if stage is not None:
        stmt = stmt.where(ImprovementInitiative.stage == stage)
    if source is not None:
        stmt = stmt.where(ImprovementInitiative.source == source)
    if owner_user_id is not None:
        stmt = stmt.where(ImprovementInitiative.owner_user_id == owner_user_id)
    if process_id is not None:
        stmt = stmt.where(ImprovementInitiative.process_id == process_id)
    stmt = stmt.order_by(ImprovementInitiative.created_at.desc())
    return (await session.execute(stmt)).scalars().all()


async def list_stage_events(
    session: AsyncSession, initiative_id: uuid.UUID
) -> Sequence[ImprovementInitiativeStageEvent]:
    """The append-only stage-event trail (oldest → newest)."""
    return (
        (
            await session.execute(
                select(ImprovementInitiativeStageEvent)
                .where(ImprovementInitiativeStageEvent.initiative_id == initiative_id)
                .order_by(ImprovementInitiativeStageEvent.occurred_at.asc())
            )
        )
        .scalars()
        .all()
    )
