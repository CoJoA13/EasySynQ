"""The Quality Objectives surface (S-obj-1; clause 6.2).

Rides the seeded objective.*/kpi.* keys (PROCESS-scoped). create = in-handler enforce on the body
process_id (the raise_capa precedent); path-id writes use the _objective_scope resolver (the
_capa_scope precedent). Reads gate at the key + an org-scoped query. RAG/pct/attainment are
computed in the serializer from the pure rule.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._objective_enums import ObjectiveDirection
from ..db.models.app_user import AppUser
from ..db.models.document_type import DocumentType
from ..db.models.documented_information import DocumentedInformation
from ..db.models.kpi_measurement import KpiMeasurement
from ..db.models.objective_plan import ObjectivePlan
from ..db.models.quality_objective import QualityObjective
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..domain.objectives.rules import attainment, pct_toward_target, rag_status
from ..problems import ProblemException
from ..services.authz import AuthzAuditSink, enforce, get_authz_audit_sink, require
from ..services.objectives import (
    add_objective_plan,
    create_objective,
    get_objective,
    list_measurements,
    list_objectives,
    list_plans,
    record_measurement,
    remove_objective_plan,
    submit_objective_for_review,
)
from ..services.objectives import queries as obj_queries  # noqa: F401 — available for future use
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    get_vault_audit_sink,
    get_vault_signature_sink,
    release,
)
from ..services.vault.release_scope import enrich_release_sod_scope

router = APIRouter(prefix="/api/v1", tags=["objectives"])


# --- request bodies ---
class ObjectiveCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    target_value: Decimal
    unit: str = Field(min_length=1, max_length=50)
    direction: ObjectiveDirection
    due_date: datetime.date
    baseline_value: Decimal | None = None
    at_risk_threshold: Decimal | None = None
    process_id: uuid.UUID | None = None
    policy_id: uuid.UUID | None = None


class MeasurementCreate(BaseModel):
    period: datetime.date
    value: Decimal
    unit: str = Field(min_length=1, max_length=50)
    source: str | None = Field(default=None, max_length=300)


class PlanCreate(BaseModel):
    action: str = Field(min_length=1, max_length=2000)
    resource: str | None = Field(default=None, max_length=500)
    responsible_user_id: uuid.UUID | None = None
    due_date: datetime.date | None = None


# --- serializers ---
def _measurement(m: KpiMeasurement) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "objective_id": str(m.objective_id) if m.objective_id else None,
        "record_id": str(m.record_id),
        "period": m.period.isoformat(),
        "value": str(m.value),
        "target_at_capture": str(m.target_at_capture),
        "unit": m.unit,
        "source": m.source,
        "created_at": m.created_at.isoformat(),
    }


def _plan(p: ObjectivePlan) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "objective_id": str(p.objective_id),
        "action": p.action,
        "resource": p.resource,
        "responsible_user_id": str(p.responsible_user_id) if p.responsible_user_id else None,
        "due_date": p.due_date.isoformat() if p.due_date else None,
    }


def _objective(
    qo: QualityObjective,
    *,
    identifier: str,
    title: str,
    current_state: Any,
    today: datetime.date,
    plans: list[ObjectivePlan] | None = None,
) -> dict[str, Any]:
    rag = rag_status(
        current=qo.current_value,
        target=qo.target_value,
        direction=qo.direction,
        at_risk_threshold=qo.at_risk_threshold,
    )
    return {
        "id": str(qo.id),
        "identifier": identifier,
        "title": title,
        "current_state": (
            current_state.value if hasattr(current_state, "value") else str(current_state)
        ),
        "target_value": str(qo.target_value),
        "unit": qo.unit,
        "baseline_value": str(qo.baseline_value) if qo.baseline_value is not None else None,
        "current_value": str(qo.current_value) if qo.current_value is not None else None,
        "direction": qo.direction.value,
        "at_risk_threshold": (
            str(qo.at_risk_threshold) if qo.at_risk_threshold is not None else None
        ),
        "due_date": qo.due_date.isoformat(),
        "process_id": str(qo.process_id) if qo.process_id else None,
        "policy_id": str(qo.policy_id) if qo.policy_id else None,
        "rag": rag,
        "pct_toward_target": pct_toward_target(
            current=qo.current_value,
            target=qo.target_value,
            baseline=qo.baseline_value,
            direction=qo.direction,
        ),
        "attainment": attainment(
            current=qo.current_value,
            target=qo.target_value,
            direction=qo.direction,
            due_date=qo.due_date,
            today=today,
        ),
        "plans": [_plan(p) for p in (plans or [])],
    }


# --- scope helpers ---
def _process_scope(process_id: uuid.UUID | None) -> ResourceContext:
    if process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(process_id)}))


async def _objective_scope(
    request: Request, session: AsyncSession = Depends(get_session)
) -> ResourceContext:
    raw = request.path_params.get("objective_id")
    if not raw:
        return ResourceContext.system()
    try:
        oid = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    qo = await session.get(QualityObjective, oid)
    if qo is None or qo.process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(qo.process_id)}))


async def _load_objective_doc(
    session: AsyncSession, caller: AppUser, objective_id: uuid.UUID, *, for_update: bool = False
) -> tuple[DocumentedInformation, QualityObjective]:
    """Load the objective's base document + satellite, 404 if it isn't an OBJ in the caller's org.
    ``for_update`` takes the row lock + ``populate_existing`` (the authz resolver already
    session.get-loaded the satellite — the S-drift-1 identity-map staleness trap)."""
    if for_update:
        doc = (
            await session.execute(
                select(DocumentedInformation)
                .where(DocumentedInformation.id == objective_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        # The satellite gets the same freshness treatment — its commitment fields feed the freeze,
        # and the authz resolver already identity-mapped the row.
        qo = (
            await session.execute(
                select(QualityObjective)
                .where(QualityObjective.id == objective_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    else:
        doc = await session.get(DocumentedInformation, objective_id)
        qo = await session.get(QualityObjective, objective_id)
    if doc is None or qo is None or doc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    return doc, qo


async def _objective_release_scope(
    session: AsyncSession, doc: DocumentedInformation
) -> ResourceContext:
    """Release scope = the objective's document scope + the SoD-2 inputs for the version the
    cutover will promote (the latest Approved): its author + approval signers. Mirrors the
    document ``_release_scope`` (documents.py) — same base fields as ``_document_scope_by_id``
    (artifact + folder + doc-class + lifecycle), then the shared SoD-2 enrichment."""
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    base = ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        lifecycle_state=doc.current_state.value,
    )
    return await enrich_release_sod_scope(session, base, doc.id, None)


_objective_read = require("objective.read")
_kpi_read = require("kpi.read", async_scope_resolver=_objective_scope)
_objective_manage_path = require("objective.manage", async_scope_resolver=_objective_scope)
_kpi_record = require("kpi.record", async_scope_resolver=_objective_scope)


def _today() -> datetime.date:
    return datetime.date.today()


# --- endpoints ---
# NOTE: /objectives/scorecard is declared BEFORE /objectives/{objective_id} so the
# literal path isn't shadowed by the {objective_id} str-convertor (S-pack-2 lesson).


@router.post("/objectives", status_code=status.HTTP_201_CREATED)
async def create_objective_endpoint(
    body: ObjectiveCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    await enforce(
        session, authz_sink, request, caller, "objective.manage", _process_scope(body.process_id)
    )
    qo = await create_objective(
        session,
        vault_sink,
        caller,
        title=body.title,
        target_value=body.target_value,
        unit=body.unit,
        direction=body.direction,
        due_date=body.due_date,
        baseline_value=body.baseline_value,
        at_risk_threshold=body.at_risk_threshold,
        process_id=body.process_id,
        policy_id=body.policy_id,
    )
    row = await get_objective(session, qo.id)
    if row is None:  # pragma: no cover — just created, cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after create"
        )
    _, ident, title, state = row
    return _objective(qo, identifier=ident, title=title, current_state=state, today=_today())


@router.get("/objectives")
async def list_objectives_endpoint(
    process_id: uuid.UUID | None = None,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await list_objectives(session, caller.org_id, process_id=process_id)
    today = _today()
    return {
        "data": [
            _objective(qo, identifier=i, title=t, current_state=s, today=today)
            for qo, i, t, s in rows
        ]
    }


@router.get("/objectives/scorecard")
async def scorecard_endpoint(
    process_id: uuid.UUID | None = None,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await list_objectives(session, caller.org_id, process_id=process_id)
    today = _today()
    serialized = [
        _objective(qo, identifier=i, title=t, current_state=s, today=today) for qo, i, t, s in rows
    ]
    by_rag: dict[str, int] = {"green": 0, "amber": 0, "red": 0, "unmeasured": 0}
    for o in serialized:
        rag_val = o["rag"]
        if isinstance(rag_val, str) and rag_val in by_rag:
            by_rag[rag_val] += 1
    return {
        "total": len(serialized),
        "on_target": by_rag["green"],
        "by_rag": by_rag,
        "objectives": serialized,
    }


@router.get("/objectives/{objective_id}")
async def get_objective_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await get_objective(session, objective_id)
    if row is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    qo, ident, title, state = row
    plans = await list_plans(session, objective_id)
    return _objective(
        qo, identifier=ident, title=title, current_state=state, today=_today(), plans=plans
    )


@router.post("/objectives/{objective_id}/submit-review")
async def submit_objective_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    # FOR UPDATE + populate_existing serializes concurrent submits and dodges the stale-identity-map
    # trap; submit_objective_for_review freezes the commitment, runs T2, instantiates approval, and
    # commits atomically. Approval then routes through POST /tasks/{id}/decision (DOCUMENT leg).
    doc, qo = await _load_objective_doc(session, caller, objective_id, for_update=True)
    await submit_objective_for_review(session, vault_sink, caller, doc, qo)
    row = await get_objective(session, objective_id)
    if row is None:  # pragma: no cover — just mutated it, cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after submit"
        )
    qo2, ident, title, state = row
    return _objective(qo2, identifier=ident, title=title, current_state=state, today=_today())


@router.post("/objectives/{objective_id}/release")
async def release_objective_endpoint(
    objective_id: uuid.UUID,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    # Enforce document.release imperatively over the SoD-2-enriched scope (author/approver ≠
    # releaser), then the shared release() runs the INV-1 SERIALIZABLE cutover in its own session.
    # An OBJ shares the documented_information id, so the kind-agnostic cutover drives it Effective
    # + signs release (the documents.py release_endpoint posture, minus the version_id body — v1
    # objectives have exactly one version stream, the latest Approved is the only candidate).
    doc, _ = await _load_objective_doc(session, caller, objective_id)
    resource = await _objective_release_scope(session, doc)
    await enforce(session, authz_sink, request, caller, "document.release", resource, sig_hook=True)
    await release(caller, objective_id, vault_sink, sig_sink)
    # release() committed in its own SERIALIZABLE session; this request session's identity map
    # still holds the pre-release state — expire it so the re-read refreshes from the DB.
    session.expire_all()
    row = await get_objective(session, objective_id)
    if row is None:  # pragma: no cover — the doc was just released, it cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after release"
        )
    qo, ident, title, state = row
    return _objective(qo, identifier=ident, title=title, current_state=state, today=_today())


@router.post("/objectives/{objective_id}/measurements", status_code=status.HTTP_201_CREATED)
async def record_measurement_endpoint(
    objective_id: uuid.UUID,
    body: MeasurementCreate,
    caller: AppUser = Depends(_kpi_record),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    m = await record_measurement(
        session,
        caller,
        objective_id=objective_id,
        period=body.period,
        value=body.value,
        unit=body.unit,
        source=body.source,
    )
    return _measurement(m)


@router.get("/objectives/{objective_id}/measurements")
async def list_measurements_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_kpi_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return {"data": [_measurement(m) for m in await list_measurements(session, objective_id)]}


@router.post("/objectives/{objective_id}/plans", status_code=status.HTTP_201_CREATED)
async def add_plan_endpoint(
    objective_id: uuid.UUID,
    body: PlanCreate,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    plan = await add_objective_plan(
        session,
        caller,
        objective_id=objective_id,
        action=body.action,
        resource=body.resource,
        responsible_user_id=body.responsible_user_id,
        due_date=body.due_date,
    )
    return _plan(plan)


@router.delete(
    "/objectives/{objective_id}/plans/{plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_plan_endpoint(
    objective_id: uuid.UUID,
    plan_id: uuid.UUID,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await remove_objective_plan(session, caller, objective_id=objective_id, plan_id=plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
