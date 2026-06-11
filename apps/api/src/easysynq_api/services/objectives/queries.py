"""Quality Objectives read queries (S-obj-1). Returns rows + the joined base identity; RAG/pct are
computed in the serializer from the pure rule."""

from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.documented_information import DocumentedInformation
from ...db.models.kpi_measurement import KpiMeasurement
from ...db.models.objective_plan import ObjectivePlan
from ...db.models.quality_objective import QualityObjective

# (qo, identifier, title, current_state)
ObjectiveRow = tuple[QualityObjective, str, str, DocumentCurrentState]


async def list_objectives(
    session: AsyncSession, org_id: uuid.UUID, *, process_id: uuid.UUID | None = None
) -> list[ObjectiveRow]:
    stmt = (
        select(
            QualityObjective,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            DocumentedInformation.current_state,
        )
        .join(DocumentedInformation, QualityObjective.id == DocumentedInformation.id)
        .where(QualityObjective.org_id == org_id)
        .order_by(DocumentedInformation.identifier)
    )
    if process_id is not None:
        stmt = stmt.where(QualityObjective.process_id == process_id)
    return [tuple(r) for r in (await session.execute(stmt)).all()]


async def get_objective(session: AsyncSession, objective_id: uuid.UUID) -> ObjectiveRow | None:
    row = (
        await session.execute(
            select(
                QualityObjective,
                DocumentedInformation.identifier,
                DocumentedInformation.title,
                DocumentedInformation.current_state,
            )
            .join(DocumentedInformation, QualityObjective.id == DocumentedInformation.id)
            .where(QualityObjective.id == objective_id)
        )
    ).first()
    return tuple(row) if row is not None else None


async def list_plans(session: AsyncSession, objective_id: uuid.UUID) -> list[ObjectivePlan]:
    return list(
        (
            await session.execute(
                select(ObjectivePlan)
                .where(ObjectivePlan.objective_id == objective_id)
                .order_by(ObjectivePlan.created_at)
            )
        ).scalars()
    )


async def list_measurements(session: AsyncSession, objective_id: uuid.UUID) -> list[KpiMeasurement]:
    return list(
        (
            await session.execute(
                select(KpiMeasurement)
                .where(KpiMeasurement.objective_id == objective_id)
                .order_by(desc(KpiMeasurement.period), desc(KpiMeasurement.created_at))
            )
        ).scalars()
    )
