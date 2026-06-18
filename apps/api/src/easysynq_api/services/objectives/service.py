"""Quality Objectives service (S-obj-1) — the txn owner. ``create_objective`` reuses the vault
``create_document`` (kind=DOCUMENT, type OBJ), then adds the satellite + a clause_mapping to 6.2.
``record_measurement`` (Task 6) captures a KPI_READING record + projection + rolls up
current_value."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

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
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.kpi_measurement import KpiMeasurement
from ...db.models.objective_plan import ObjectivePlan
from ...db.models.process import Process
from ...db.models.quality_objective import QualityObjective
from ...domain.objectives.commitment import parse_commitment
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
    # Validate process_id BEFORE create_document (which commits the base doc): a bad/foreign
    # process_id would otherwise FK-abort the satellite insert AFTER the base OBJ document was
    # already committed, orphaning a base documented_information row with no quality_objective.
    if process_id is not None:
        proc = await session.get(Process, process_id)
        if proc is None or proc.org_id != actor.org_id:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="Unknown process_id (must be a process in your organization)",
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
    # S-obj-4 (O-2): the unit gate + target_at_capture read the GOVERNING frozen commitment when
    # one exists — a mid-revision working-row edit must never leak an unapproved unit/target into
    # evidence-grade KPI_READING records (R44: target_at_capture is frozen at capture, never
    # rewritten). Working-row fallback pre-first-release (today's behavior). Fresh reads
    # (populate_existing — a cutover may have committed while we waited on the qo lock); NO
    # doc-row lock: it would invert _load_objective_doc's doc→satellite lock order against a
    # concurrent submit.
    doc_row = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == objective_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    governing: dict[str, Any] | None = None
    if doc_row is not None and doc_row.current_effective_version_id is not None:
        ver = (
            await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.id == doc_row.current_effective_version_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        raw = (ver.metadata_snapshot or {}).get("objective_commitment") if ver is not None else None
        # A non-dict fold is treated as absent (the working-row fallback) — deliberately softer
        # than the read path's strict parse_commitment: unconstructible at this HEAD (the O-5
        # guard + build_commitment are the only mints), and a capture pipeline must not 500 on a
        # drift-class snapshot the drift scanners, not KPI intake, are responsible for surfacing.
        governing = raw if isinstance(raw, dict) else None
    if governing is not None:
        gc = parse_commitment(governing)
        effective_unit, effective_target = gc.unit, gc.target_value
        effective_direction, effective_threshold = gc.direction, gc.at_risk_threshold
    else:
        effective_unit, effective_target = qo.unit, qo.target_value
        effective_direction, effective_threshold = qo.direction, qo.at_risk_threshold
    # The reading must be in the objective's GOVERNING unit — current_value/RAG compare the
    # raw value against the governing target with no conversion (a mismatch would corrupt the
    # scorecard).
    if unit != effective_unit:
        raise ProblemException(
            status=422,
            code="validation_error",
            title=f"Measurement unit '{unit}' must match the objective unit '{effective_unit}'",
        )

    target_at_capture = effective_target

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

    # S-obj-freeze: snapshot the FULL grading basis (target + direction + amber threshold) from the
    # SAME governing/working resolution as the target, so a later commitment revision cannot
    # re-grade this reading. effective_direction is never None; the threshold may be None.
    measurement = KpiMeasurement(
        org_id=actor.org_id,
        record_id=record.id,
        objective_id=objective_id,
        process_id=qo.process_id,
        period=period,
        value=value,
        target_at_capture=target_at_capture,
        direction_at_capture=effective_direction,
        at_risk_threshold_at_capture=effective_threshold,
        unit=unit,
        source=source,
    )
    session.add(measurement)
    await session.flush()

    # Roll up current_value = the value of the MAX-period SAME-UNIT reading (out-of-order safe; an
    # old-unit backfill can never re-grade a new-unit target — the micro-call B conditional's
    # other half). scalar_one() stays safe: the just-inserted reading always matches effective_unit.
    latest_value = (
        await session.execute(
            select(KpiMeasurement.value)
            .where(
                KpiMeasurement.objective_id == objective_id,
                KpiMeasurement.unit == effective_unit,
            )
            .order_by(desc(KpiMeasurement.period), desc(KpiMeasurement.created_at))
            .limit(1)
        )
    ).scalar_one()
    qo.current_value = latest_value

    # Audit (object_type=document, scope_ref=identifier — R39). Mirror services/ack/sweep.py:67-82
    # field-for-field: occurred_at + actor_type are NOT NULL with no server default.
    base = doc_row
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
