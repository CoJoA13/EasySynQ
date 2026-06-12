"""Quality Objectives read queries (S-obj-1/S-obj-4). Returns rows + the joined base identity +
the GOVERNING frozen commitment (the current Effective version's snapshot fold — a per-row PK
probe via current_effective_version_id, the drift_report outerjoin precedent; NULL
pre-first-release, where the serializer falls back to the working row). RAG/pct are computed in
the serializer from the pure rule over the RESOLVED commitment."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.kpi_measurement import KpiMeasurement
from ...db.models.objective_plan import ObjectivePlan
from ...db.models.quality_objective import QualityObjective

# (qo, identifier, title, current_state, governing_commitment | None)
ObjectiveRow = tuple[QualityObjective, str, str, DocumentCurrentState, Any]


def _row_select() -> Select[Any]:
    return (
        select(
            QualityObjective,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            DocumentedInformation.current_state,
            DocumentVersion.metadata_snapshot["objective_commitment"].label("governing_commitment"),
        )
        .join(DocumentedInformation, QualityObjective.id == DocumentedInformation.id)
        .outerjoin(
            DocumentVersion,
            DocumentVersion.id == DocumentedInformation.current_effective_version_id,
        )
    )


async def list_objectives(
    session: AsyncSession, org_id: uuid.UUID, *, process_id: uuid.UUID | None = None
) -> list[ObjectiveRow]:
    stmt = (
        _row_select()
        .where(QualityObjective.org_id == org_id)
        .order_by(DocumentedInformation.identifier)
    )
    if process_id is not None:
        stmt = stmt.where(QualityObjective.process_id == process_id)
    return [tuple(r) for r in (await session.execute(stmt)).all()]


async def get_objective(session: AsyncSession, objective_id: uuid.UUID) -> ObjectiveRow | None:
    row = (await session.execute(_row_select().where(QualityObjective.id == objective_id))).first()
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
