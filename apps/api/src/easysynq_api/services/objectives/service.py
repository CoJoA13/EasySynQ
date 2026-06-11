"""Quality Objectives service (S-obj-1) — the txn owner. ``create_objective`` reuses the vault
``create_document`` (kind=DOCUMENT, type OBJ), then adds the satellite + a clause_mapping to 6.2.
``record_measurement`` (Task 6) captures a KPI_READING record + projection + rolls up
current_value."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._objective_enums import ObjectiveDirection
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.kpi_measurement import KpiMeasurement
from ...db.models.objective_plan import ObjectivePlan
from ...db.models.quality_objective import QualityObjective
from ...problems import ProblemException
from ..records import capture_record
from ..vault import VaultAuditSink, create_document


async def _obj_document_type_id(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    dt = (
        await session.execute(
            select(DocumentType).where(DocumentType.org_id == org_id, DocumentType.code == "OBJ")
        )
    ).scalar_one_or_none()
    if dt is None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="OBJ document_type is not seeded",
        )
    return dt.id


async def current_effective_policy(
    session: AsyncSession, org_id: uuid.UUID
) -> DocumentedInformation | None:
    """The single Effective Quality Policy (POL singleton, R25), or None."""
    return (
        await session.execute(
            select(DocumentedInformation)
            .join(
                DocumentType,
                DocumentedInformation.document_type_id == DocumentType.id,
            )
            .where(
                DocumentedInformation.org_id == org_id,
                DocumentType.code == "POL",
                DocumentedInformation.current_state == DocumentCurrentState.Effective,
            )
        )
    ).scalar_one_or_none()


async def create_objective(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    *,
    title: str,
    target_value: Decimal,
    unit: str,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    baseline_value: Decimal | None = None,
    at_risk_threshold: Decimal | None = None,
    process_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    area_code: str | None = None,
    folder_path: str | None = None,
    classification: str = "Internal",
) -> QualityObjective:
    """Create a Quality Objective as a kind=DOCUMENT subtype (type OBJ), auto-mapped to 6.2."""
    # Measurable-by-construction: title/target/unit/direction/due are required by the
    # caller/pydantic.
    if policy_id is not None:
        eff = await current_effective_policy(session, actor.org_id)
        if eff is None or eff.id != policy_id:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="policy_id must be the current Effective Quality Policy",
            )
    dt_id = await _obj_document_type_id(session, actor.org_id)
    # create_document commits the base doc (the form_template two-step precedent).
    doc = await create_document(
        session,
        sink,
        actor,
        title=title,
        document_type_id=dt_id,
        area_code=area_code,
        folder_path=folder_path,
        classification=classification,
    )
    qo = QualityObjective(
        id=doc.id,
        org_id=actor.org_id,
        target_value=target_value,
        unit=unit,
        baseline_value=baseline_value,
        current_value=None,
        direction=direction,
        at_risk_threshold=at_risk_threshold,
        due_date=due_date,
        process_id=process_id,
        policy_id=policy_id,
    )
    session.add(qo)
    # Auto-map to clause 6.2 so the ★ checklist resolves on release.
    clause_6_2 = (
        await session.execute(
            select(Clause).where(
                Clause.number == "6.2",
                Clause.framework_id == doc.framework_id,
            )
        )
    ).scalar_one_or_none()
    if clause_6_2 is not None:
        session.add(
            ClauseMapping(
                org_id=actor.org_id,
                framework_id=doc.framework_id,
                clause_id=clause_6_2.id,
                documented_information_id=doc.id,
                is_requirement_level=True,
                created_by=actor.id,
            )
        )
    await session.commit()
    await session.refresh(qo)
    return qo


async def record_measurement(
    session: AsyncSession,
    actor: AppUser,
    *,
    objective_id: uuid.UUID,
    period: datetime.date,
    value: Decimal,
    unit: str,
    source: str | None = None,
) -> KpiMeasurement:
    """Capture a KPI_READING record + kpi_measurement projection, then roll up current_value.

    One transaction: capture_record(_commit=False) → insert KpiMeasurement → flush → rollup
    → AuditEvent → session.commit().

    The KPI reading is an AD-HOC record (no source_document_id — the R21 trap: a non-FRM source
    triggers a 422 version-pin requirement, and a Draft objective has no version to pin). The
    objective linkage lives on kpi_measurement.objective_id + form_field_values.

    current_value = the value of the MAX-period reading (ORDER BY period DESC, created_at DESC
    LIMIT 1) so out-of-order inserts never clobber a later period (the S-drift-1 populate_existing
    pattern guards the identity-map staleness trap on the authz-pre-loaded objective row).
    """
    # Lock + freshen the objective (the authz resolver already session.get-loaded it;
    # without populate_existing the FOR UPDATE returns stale identity-map attributes —
    # the S-drift-1 trap).
    qo = (
        await session.execute(
            select(QualityObjective)
            .where(QualityObjective.id == objective_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if qo is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")

    target_at_capture = qo.target_value

    # Evidence-grade reading: an AD-HOC KPI_READING record (the capture_complaint precedent —
    # NO source_document_id so no R21 version-pin; a Draft objective has no version to pin).
    # _commit=False composes the record + projection + rollup in ONE transaction.
    record = await capture_record(
        session,
        actor,
        record_type="KPI_READING",
        title=f"KPI reading {period.isoformat()} ({qo.id})",
        form_field_values={
            "objective_id": str(objective_id),
            "period": period.isoformat(),
            "value": str(value),
            "target_at_capture": str(target_at_capture),
            "unit": unit,
            "source": source,
        },
        _commit=False,
    )

    measurement = KpiMeasurement(
        org_id=actor.org_id,
        record_id=record.id,
        objective_id=objective_id,
        process_id=qo.process_id,
        period=period,
        value=value,
        target_at_capture=target_at_capture,
        unit=unit,
        source=source,
    )
    session.add(measurement)
    await session.flush()

    # Roll up current_value = the value of the MAX-period reading (out-of-order safe).
    latest_value = (
        await session.execute(
            select(KpiMeasurement.value)
            .where(KpiMeasurement.objective_id == objective_id)
            .order_by(desc(KpiMeasurement.period), desc(KpiMeasurement.created_at))
            .limit(1)
        )
    ).scalar_one()
    qo.current_value = latest_value

    # Audit (object_type=document, scope_ref=identifier — R39). Mirror services/ack/sweep.py:67-82
    # field-for-field: occurred_at + actor_type are NOT NULL with no server default.
    base = await session.get(DocumentedInformation, objective_id)
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.OBJECTIVE_MEASUREMENT_RECORDED,
            object_type=AuditObjectType.document,
            object_id=objective_id,
            scope_ref=base.identifier if base else None,
            after={
                "period": period.isoformat(),
                "value": str(value),
                "current_value": str(latest_value),
            },
        )
    )
    await session.commit()
    await session.refresh(measurement)
    return measurement


async def add_objective_plan(
    session: AsyncSession,
    actor: AppUser,
    *,
    objective_id: uuid.UUID,
    action: str,
    resource: str | None = None,
    responsible_user_id: uuid.UUID | None = None,
    due_date: datetime.date | None = None,
) -> ObjectivePlan:
    """Add an action-plan row to a Quality Objective (ISO 6.2 'planning to achieve them')."""
    qo = await session.get(QualityObjective, objective_id)
    if qo is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    plan = ObjectivePlan(
        org_id=actor.org_id,
        objective_id=objective_id,
        action=action,
        resource=resource,
        responsible_user_id=responsible_user_id,
        due_date=due_date,
    )
    session.add(plan)
    await session.flush()
    base = await session.get(DocumentedInformation, objective_id)
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.OBJECTIVE_PLAN_ADDED,
            object_type=AuditObjectType.document,
            object_id=objective_id,
            scope_ref=base.identifier if base else None,
            after={"plan_id": str(plan.id), "action": action},
        )
    )
    await session.commit()
    await session.refresh(plan)
    return plan


async def remove_objective_plan(
    session: AsyncSession,
    actor: AppUser,
    *,
    objective_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> None:
    """Remove an action-plan row from a Quality Objective, emitting an audit event."""
    plan = await session.get(ObjectivePlan, plan_id)
    if plan is None or plan.objective_id != objective_id:
        raise ProblemException(status=404, code="not_found", title="Plan not found")
    await session.delete(plan)
    base = await session.get(DocumentedInformation, objective_id)
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.OBJECTIVE_PLAN_REMOVED,
            object_type=AuditObjectType.document,
            object_id=objective_id,
            scope_ref=base.identifier if base else None,
            after={"plan_id": str(plan_id)},
        )
    )
    await session.commit()
