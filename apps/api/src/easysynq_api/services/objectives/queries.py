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
from ...domain.objectives.commitment import resolve_commitment
from ...domain.objectives.rules import rag_status

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


async def compute_scorecard(
    session: AsyncSession, org_id: uuid.UUID, *, process_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Grade every objective off its GOVERNING frozen commitment (resolve_commitment) → tally by
    RAG. Returns {total, on_target, by_rag:{green,amber,red,unmeasured}, rows:[ObjectiveRow]}.

    AUTHZ-AGNOSTIC: the caller MUST gate the read (the endpoint via require('objective.read'); the
    MR compiler via _owner_holds). This fn performs NO authz — keep require/enforce/gather_grants
    out of it (the S-mr-2 #1 risk)."""
    rows = await list_objectives(session, org_id, process_id=process_id)
    by_rag: dict[str, int] = {"green": 0, "amber": 0, "red": 0, "unmeasured": 0}
    for qo, _ident, _title, _state, governing in rows:
        commitment = resolve_commitment(
            governing,
            target_value=qo.target_value,
            unit=qo.unit,
            direction=qo.direction,
            due_date=qo.due_date,
            at_risk_threshold=qo.at_risk_threshold,
            baseline_value=qo.baseline_value,
            policy_id=qo.policy_id,
        )
        rag = rag_status(
            current=qo.current_value,
            target=commitment.target_value,
            direction=commitment.direction,
            at_risk_threshold=commitment.at_risk_threshold,
        )
        by_rag[rag] += 1  # rag is always one of the 4 keys (rules.py); KeyError loudly if it drifts
    return {
        "total": sum(by_rag.values()),
        "on_target": by_rag["green"],
        "by_rag": by_rag,
        "rows": rows,
    }
