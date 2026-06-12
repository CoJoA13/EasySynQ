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
from ..db.models._vault_enums import DocumentCurrentState
from ..db.models._workflow_enums import WorkflowSubjectType
from ..db.models.app_user import AppUser
from ..db.models.document_type import DocumentType
from ..db.models.document_version import DocumentVersion
from ..db.models.documented_information import DocumentedInformation
from ..db.models.kpi_measurement import KpiMeasurement
from ..db.models.objective_plan import ObjectivePlan
from ..db.models.quality_objective import QualityObjective
from ..db.models.workflow import Task, WorkflowInstance
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..domain.objectives.commitment import build_commitment, resolve_commitment
from ..domain.objectives.rules import attainment, pct_toward_target, rag_status
from ..problems import ProblemException
from ..services.authz import (
    AuthzAuditSink,
    enforce,
    gather_grants,
    get_authz_audit_sink,
    require,
)
from ..services.authz.repository import gather_sod_constraints, get_allow_approver_release
from ..services.objectives import (
    add_objective_plan,
    create_objective,
    current_effective_policy,
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
    start_revision,
)
from ..services.vault.release_scope import enrich_release_sod_scope
from ..services.workflow import repository as wf_repo

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


class ObjectiveUpdate(BaseModel):
    """S-obj-4 (O-1): a partial commitment edit. Omitted ≠ null — ``model_fields_set``
    distinguishes them (the documents metadata-PATCH precedent); explicit null CLEARS the three
    nullable fields and 422s on the four NOT-NULL ones."""

    target_value: Decimal | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=50)
    direction: ObjectiveDirection | None = None
    due_date: datetime.date | None = None
    at_risk_threshold: Decimal | None = None
    baseline_value: Decimal | None = None
    policy_id: uuid.UUID | None = None


class ObjectiveSubmitBody(BaseModel):
    """Optional INV-3 change reason for the freeze (defaults: first submit vs revision)."""

    change_reason: str | None = Field(default=None, max_length=500)


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
    capabilities: dict[str, bool] | None = None,
    effective_from: str | None = None,
    governing: dict[str, Any] | None = None,
    pending_commitment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # S-obj-4 (O-3, F-2 closed): every commitment-shaped field + the RAG/pct/attainment grade
    # resolve through the GOVERNING frozen commitment when one exists (the version snapshot,
    # never the mutable working row) — an in-flight revision edit can never re-grade the live
    # register/scorecard. Pre-first-release (governing None) the working row IS the commitment
    # (bit-identical to the S-obj-3 output). current_value stays operational (satellite-side).
    c = resolve_commitment(
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
        target=c.target_value,
        direction=c.direction,
        at_risk_threshold=c.at_risk_threshold,
    )
    out: dict[str, Any] = {
        "id": str(qo.id),
        "identifier": identifier,
        "title": title,
        "current_state": (
            current_state.value if hasattr(current_state, "value") else str(current_state)
        ),
        "target_value": str(c.target_value),
        "unit": c.unit,
        "baseline_value": str(c.baseline_value) if c.baseline_value is not None else None,
        "current_value": str(qo.current_value) if qo.current_value is not None else None,
        "direction": c.direction.value,
        "at_risk_threshold": (
            str(c.at_risk_threshold) if c.at_risk_threshold is not None else None
        ),
        "due_date": c.due_date.isoformat(),
        "process_id": str(qo.process_id) if qo.process_id else None,
        "policy_id": str(c.policy_id) if c.policy_id is not None else None,
        "rag": rag,
        "pct_toward_target": pct_toward_target(
            current=qo.current_value,
            target=c.target_value,
            baseline=c.baseline_value,
            direction=c.direction,
        ),
        "attainment": attainment(
            current=qo.current_value,
            target=c.target_value,
            direction=c.direction,
            due_date=c.due_date,
            today=today,
        ),
        "plans": [_plan(p) for p in (plans or [])],
    }
    # S-obj-3 (detail-only): the caller's lifecycle affordances + the effective date for the
    # stepper. LIST/scorecard/create/submit/release call-sites pass neither → unchanged output.
    # A detail response ALWAYS carries effective_from (null until Effective — the _document
    # precedent); capabilities doubles as the detail marker AND, since S-obj-4,
    # pending_commitment (the in-edit working commitment when it diverges from governing, else
    # null — the edit modal's seed + the "proposed revision" card).
    if capabilities is not None:
        out["capabilities"] = capabilities
        out["effective_from"] = effective_from
        out["pending_commitment"] = pending_commitment
    return out


def _approval_task(t: Task) -> dict[str, Any]:
    """Field-equivalent to api/workflow.py's ``_task`` as the document-approval endpoint emits it
    (no ``subject_type``/``subject_id`` — those are the My-Tasks-inbox extras). Kept local: no
    cross-router private import; the FE's Task type was pinned to this shape (S-web-5)."""
    return {
        "id": str(t.id),
        "instance_id": str(t.instance_id),
        "stage_key": t.stage_key,
        "type": t.type.value,
        "state": t.state.value,
        "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None,
        "candidate_pool": t.candidate_pool,
        "action_expected": t.action_expected,
        "due_at": t.due_at.isoformat() if t.due_at else None,
    }


def _approval_instance(i: WorkflowInstance, tasks: list[Task]) -> dict[str, Any]:
    """Field-equivalent to api/workflow.py's ``_instance`` with tasks expanded (the
    ``GET /documents/{id}/approval`` shape the FE's WorkflowInstance type is pinned to)."""
    return {
        "id": str(i.id),
        "definition_id": str(i.definition_id),
        "definition_version": i.definition_version,
        "subject_type": i.subject_type.value,
        "subject_id": str(i.subject_id),
        "current_state": i.current_state,
        "started_at": i.started_at.isoformat() if i.started_at else None,
        "revision": i.revision,
        "tasks": [_approval_task(t) for t in tasks],
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
    # qo is None ↔ no quality_objective row → a non-OBJ document or a Record id; the kind-scoping
    # guard (S-rec-1) rides the satellite's existence rather than an explicit doc.kind check.
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


async def _objective_capabilities(
    session: AsyncSession, caller: AppUser, doc: DocumentedInformation, qo: QualityObjective
) -> dict[str, bool]:
    """The caller's lifecycle affordances on this objective (detail-only). submit = objective.manage
    at the objective's scope (the _objective_scope rule); release = document.release + the SoD-2
    overlay over the version the cutover would promote. Mirrors documents._document_capabilities."""
    now = datetime.datetime.now(datetime.UTC)
    ctx = RequestContext(now=now, actor_user_id=str(caller.id))
    submit_scope = (
        ResourceContext(process_ids=frozenset({str(qo.process_id)}))
        if qo.process_id is not None
        else ResourceContext.system()
    )
    mgr_grants = await gather_grants(session, caller.id, caller.org_id, "objective.manage")
    submit_cap = authorize(mgr_grants, "objective.manage", submit_scope, ctx).allow

    release_scope = await _objective_release_scope(session, doc)
    sod = await gather_sod_constraints(session, caller.org_id)
    allow_approver_release = await get_allow_approver_release(session, caller.org_id)
    rel_ctx = RequestContext(
        now=now, actor_user_id=str(caller.id), allow_approver_release=allow_approver_release
    )
    rel_grants = await gather_grants(session, caller.id, caller.org_id, "document.release")
    release_cap = authorize(
        rel_grants, "document.release", release_scope, rel_ctx, sig_hook=True, sod=sod
    ).allow
    # S-obj-4 (micro-call C): edit/start_revision ride the SAME objective.manage answer as submit
    # (permission-only, state-blind — the FE combines with current_state); split keys so the
    # contract self-describes and an Edit button never gates on a flag named "submit".
    return {
        "submit": submit_cap,
        "edit": submit_cap,
        "start_revision": submit_cap,
        "release": release_cap,
    }


async def _objective_effective_from(
    session: AsyncSession, doc: DocumentedInformation
) -> str | None:
    if doc.current_effective_version_id is None:
        return None
    v = await session.get(DocumentVersion, doc.current_effective_version_id)
    return v.effective_from.isoformat() if v is not None and v.effective_from else None


_objective_read = require("objective.read")
_kpi_read = require("kpi.read", async_scope_resolver=_objective_scope)
_objective_manage_path = require("objective.manage", async_scope_resolver=_objective_scope)
_kpi_record = require("kpi.record", async_scope_resolver=_objective_scope)


def _today() -> datetime.date:
    return datetime.date.today()


# --- endpoints ---
# NOTE: /objectives/scorecard AND /objectives/policy are declared BEFORE
# /objectives/{objective_id} so the literal paths aren't shadowed by the {objective_id}
# str-convertor (S-pack-2 lesson).


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
    _, ident, title, state, gov = row
    return _objective(
        qo, identifier=ident, title=title, current_state=state, today=_today(), governing=gov
    )


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
            _objective(qo, identifier=i, title=t, current_state=s, today=today, governing=g)
            for qo, i, t, s, g in rows
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
        _objective(qo, identifier=i, title=t, current_state=s, today=today, governing=g)
        for qo, i, t, s, g in rows
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


@router.get("/objectives/policy")
async def get_objective_policy_endpoint(
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    """The current Effective Quality Policy (POL singleton, R25) for the create modal's policy link,
    or ``null`` when none is effective yet. Gated ``objective.read``."""
    pol = await current_effective_policy(session, caller.org_id)
    if pol is None:
        return None
    return {"id": str(pol.id), "identifier": pol.identifier, "title": pol.title}


@router.get("/objectives/{objective_id}")
async def get_objective_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await get_objective(session, objective_id)
    if row is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    qo, ident, title, state, gov = row
    plans = await list_plans(session, objective_id)
    doc = await session.get(DocumentedInformation, objective_id)
    if doc is None:  # pragma: no cover — the satellite row exists, so the base must too
        raise ProblemException(
            status=500, code="internal_error", title="Objective base row missing"
        )
    caps = await _objective_capabilities(session, caller, doc, qo)
    eff = await _objective_effective_from(session, doc)
    # S-obj-4: the in-edit working commitment, exposed only while it diverges from governing.
    working = build_commitment(
        target_value=qo.target_value,
        unit=qo.unit,
        direction=qo.direction,
        due_date=qo.due_date,
        at_risk_threshold=qo.at_risk_threshold,
        baseline_value=qo.baseline_value,
        policy_id=qo.policy_id,
    )
    pending = working if (gov is not None and working != gov) else None
    return _objective(
        qo,
        identifier=ident,
        title=title,
        current_state=state,
        today=_today(),
        plans=plans,
        capabilities=caps,
        effective_from=eff,
        governing=gov,
        pending_commitment=pending,
    )


@router.patch("/objectives/{objective_id}")
async def update_objective_endpoint(
    objective_id: uuid.UUID,
    body: ObjectiveUpdate,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Edit the working-copy commitment (S-obj-4, O-1) — legal only in Draft/UnderRevision (the
    FRM form-schema posture). The satellite is the editable working copy (its model docstring);
    the edit is deliberately UNAUDITED (the documents metadata-PATCH precedent, micro-call A): the
    auditable act is the freeze at submit, and every commitment that reaches review is
    reconstructable from consecutive frozen snapshots. No version is minted here; the read-back
    switch (O-3) keeps an Effective objective's register/scorecard/detail reads on the GOVERNING
    frozen commitment, so an in-flight edit is visible only via pending_commitment."""
    doc, qo = await _load_objective_doc(session, caller, objective_id, for_update=True)
    if doc.current_state not in (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision):
        raise ProblemException(
            status=409,
            code="conflict",
            title="Objective commitment is only editable in Draft or UnderRevision",
            detail=f"current_state is {doc.current_state.value}",
        )
    fields = body.model_fields_set
    for f in ("target_value", "unit", "direction", "due_date"):
        if f in fields and getattr(body, f) is None:
            raise ProblemException(
                status=422,
                code="validation_error",
                title=f"{f} cannot be null",
                errors=[{"field": f, "code": "not_nullable", "message": "provide a value"}],
            )
    if "policy_id" in fields and body.policy_id is not None:
        # Mirrors create_objective: the only legal link is the current Effective POL (R25).
        eff = await current_effective_policy(session, caller.org_id)
        if eff is None or eff.id != body.policy_id:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="policy_id must be the current Effective Quality Policy",
            )
    if "target_value" in fields and body.target_value is not None:
        qo.target_value = body.target_value
    if "unit" in fields and body.unit is not None:
        qo.unit = body.unit
    if "direction" in fields and body.direction is not None:
        qo.direction = body.direction
    if "due_date" in fields and body.due_date is not None:
        qo.due_date = body.due_date
    if "at_risk_threshold" in fields:
        qo.at_risk_threshold = body.at_risk_threshold
    if "baseline_value" in fields:
        qo.baseline_value = body.baseline_value
    if "policy_id" in fields:
        qo.policy_id = body.policy_id
    doc.updated_by = caller.id
    await session.commit()
    row = await get_objective(session, objective_id)
    if row is None:  # pragma: no cover — just mutated it, cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after update"
        )
    qo2, ident, title, state, gov = row
    return _objective(
        qo2, identifier=ident, title=title, current_state=state, today=_today(), governing=gov
    )


@router.post("/objectives/{objective_id}/start-revision")
async def start_objective_revision_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """T7 (Effective → UnderRevision) for an objective (S-obj-4, O-4) — a thin wrapper over the
    SAME vault start_revision (FSM guard, Redis edit lock, WorkingDraft seeded from Effective,
    REVISION_STARTED audit, commits), gated objective.manage: the F-1 asymmetry — the QMS Owner
    holds no document.edit, so the generic route is unreachable (and guarded on OBJ rows anyway).
    The Effective version keeps governing (R43: in-force is the pointer, which only the v2
    cutover moves — the 6.2 ★ stays COVERED through the whole revision window)."""
    doc, _qo = await _load_objective_doc(session, caller, objective_id, for_update=True)
    await start_revision(session, vault_sink, caller, doc)
    row = await get_objective(session, objective_id)
    if row is None:  # pragma: no cover — just transitioned it, cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after start-revision"
        )
    qo2, ident, title, state, gov = row
    return _objective(
        qo2, identifier=ident, title=title, current_state=state, today=_today(), governing=gov
    )


@router.get("/objectives/{objective_id}/approval")
async def get_objective_approval_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    """The objective's current approval cycle — the latest workflow instance + tasks, or ``null``
    before submit. Gated ``objective.read`` (the objective owner may hold no ``document.read``); the
    OBJ approval instance carries ``subject_type=DOCUMENT`` (instantiate_approval hardcodes it)."""
    qo = await session.get(QualityObjective, objective_id)
    if qo is None or qo.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    instance = await wf_repo.latest_instance_for_subject(
        session, caller.org_id, WorkflowSubjectType.DOCUMENT, objective_id
    )
    if instance is None:
        return None
    tasks = await wf_repo.list_instance_tasks(session, instance.id)
    return _approval_instance(instance, tasks)


@router.post("/objectives/{objective_id}/submit-review")
async def submit_objective_endpoint(
    objective_id: uuid.UUID,
    body: ObjectiveSubmitBody | None = None,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    # FOR UPDATE + populate_existing serializes concurrent submits and dodges the stale-identity-map
    # trap; submit_objective_for_review freezes when the commitment changed (T2 AND T9 — S-obj-4),
    # instantiates approval, and commits atomically. Approval routes through POST
    # /tasks/{id}/decision (DOCUMENT leg).
    # A change_reason on a NO-freeze re-submit (unchanged commitment) is deliberately unused — the
    # reason documents the frozen version, and no version is minted.
    doc, qo = await _load_objective_doc(session, caller, objective_id, for_update=True)
    await submit_objective_for_review(
        session,
        vault_sink,
        caller,
        doc,
        qo,
        change_reason=body.change_reason if body is not None else None,
    )
    row = await get_objective(session, objective_id)
    if row is None:  # pragma: no cover — just mutated it, cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after submit"
        )
    qo2, ident, title, state, gov = row
    return _objective(
        qo2, identifier=ident, title=title, current_state=state, today=_today(), governing=gov
    )


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
    # Micro-call B: capture the CURRENTLY-governing unit before the cutover demotes it (None
    # pre-first-release). The doc row was loaded pre-release, so its pointer is the prior one.
    prior_unit: str | None = None
    if doc.current_effective_version_id is not None:
        pv = await session.get(DocumentVersion, doc.current_effective_version_id)
        pc = (pv.metadata_snapshot or {}).get("objective_commitment") if pv is not None else None
        prior_unit = pc.get("unit") if isinstance(pc, dict) else None
    await release(caller, objective_id, vault_sink, sig_sink)
    # release() committed in its own SERIALIZABLE session; this request session's identity map
    # still holds the pre-release state — expire it so the re-reads refresh from the DB.
    session.expire_all()
    # Micro-call B: a unit-changing revision makes current_value (old-unit readings) garbage
    # against the new target — reset to NULL (rag honestly reads unmeasured until a reading in
    # the new unit lands; a crash in this gap self-heals at the next measurement, which validates
    # against the NEW governing unit and rolls up only same-unit readings).
    doc_after = await session.get(DocumentedInformation, objective_id)
    new_unit: str | None = None
    if doc_after is not None and doc_after.current_effective_version_id is not None:
        nv = await session.get(DocumentVersion, doc_after.current_effective_version_id)
        nc = (nv.metadata_snapshot or {}).get("objective_commitment") if nv is not None else None
        new_unit = nc.get("unit") if isinstance(nc, dict) else None
    if prior_unit is not None and new_unit is not None and prior_unit != new_unit:
        qo_after = await session.get(QualityObjective, objective_id)
        if qo_after is not None and qo_after.current_value is not None:
            qo_after.current_value = None
            await session.commit()
    row = await get_objective(session, objective_id)
    if row is None:  # pragma: no cover — the doc was just released, it cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after release"
        )
    qo, ident, title, state, gov = row
    return _objective(
        qo, identifier=ident, title=title, current_state=state, today=_today(), governing=gov
    )


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
