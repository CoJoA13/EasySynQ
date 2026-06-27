"""The CAPA / NCR / Complaint surface (slice S-capa-1; doc 02 Cl 8.7/10.2, doc 10 §6, doc 15).

All keys are the seeded doc-07 catalog (no new keys; S-capa-1 backfills the orphaned grants). The
CAPA-write keys (``capa.create`` / ``capa.update`` / ``ncr.create`` / ``ncr.record_correction``) are
``finest_scope=PROCESS``, so their gates resolve a PROCESS ``ResourceContext`` from the relevant
process (the body's ``process_id`` for creates — in-handler ``enforce``, the ``records`` capture
precedent; the row's ``process_id`` for path-id writes — an async scope resolver, the
``_audit_scope``
precedent), with a SYSTEM fallback so a SYSTEM grant/override always matches. A concrete PROCESS
grant
matches once owner-assignment binds the seeded ``:assignment_process`` placeholder. ``capa.read``
reads are PROCESS-scoped so a bound Process Owner can reach the board the process-scoped raise
targets: the LIST (``GET /capas``) row-filters per-process (filter-not-403, the
``_readable_processes`` precedent) and the single reads (``GET /capas/{id}`` + ``/approval``)
enforce at the CAPA's PROCESS scope — a SYSTEM grant matches every row/CAPA, so SYSTEM holders are
byte-identical. ``ncr.read`` / ``record.read`` (complaints) still gate at SYSTEM + an org-scoped
query (the S-aud-1 audits-list precedent). Complaint capture rides ``record.create`` (a complaint IS
a record); the complaint→CAPA spawn rides ``capa.create``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._capa_enums import (
    CapaCloseState,
    CapaSource,
    NcrDisposition,
    NcrSource,
    NcSeverity,
)
from ..db.models._workflow_enums import WorkflowSubjectType
from ..db.models.app_user import AppUser
from ..db.models.capa import Capa
from ..db.models.capa_stage import CapaStage
from ..db.models.complaint import Complaint
from ..db.models.ncr import Ncr
from ..db.models.workflow import Task, WorkflowInstance
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..problems import ProblemException
from ..services.authz import AuthzAuditSink, enforce, gather_grants, get_authz_audit_sink, require
from ..services.capa import (
    advance_capa_to_containment,
    advance_capa_to_implement,
    advance_capa_to_root_cause,
    capture_complaint,
    close_capa,
    create_ncr,
    propose_action_plan,
    raise_capa,
    record_ncr_disposition,
    set_capa_target_date,
    spawn_capa_from_complaint,
    verify_capa,
)
from ..services.capa import repository as capa_repo
from ..services.common.org_clock import current_org_tz
from ..services.vault import SignatureEventSink, get_vault_signature_sink
from ..services.workflow import repository as wf_repo

router = APIRouter(prefix="/api/v1", tags=["capa"])


# --- request bodies ---------------------------------------------------------------------------


class CapaRaise(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    severity: NcSeverity
    source: CapaSource = CapaSource.process
    process_id: uuid.UUID | None = None
    problem: str | None = Field(default=None, max_length=4000)


class ContainmentCreate(BaseModel):
    # The sealed correction narrative (caller-constructed). Must be non-empty (the service guards;
    # 422 otherwise) — e.g. {"correction": "...", "evidence_note": "..."}.
    content_block: dict[str, Any]


class RootCauseCreate(BaseModel):
    # The sealed RCA narrative (5-Whys / fishbone), e.g. {"root_cause": "...", "method": "5-whys"}.
    content_block: dict[str, Any]


class ActionPlanPropose(BaseModel):
    # The proposed corrective action plan (caller-constructed), e.g.
    # {"action_items": [{"description": "...", "owner": "...", "due_date": "..."}]}.
    content_block: dict[str, Any]


class ImplementCreate(BaseModel):
    # The action-completion narrative (caller-constructed), e.g. {"actions_done": "..."}.
    content_block: dict[str, Any]


class VerifyCreate(BaseModel):
    # The verifier's effectiveness decision + narrative. ``decision`` drives the M4 close gate:
    # ``effective`` is the only value that can close; ``not_effective`` loops back to RootCause.
    decision: Literal["effective", "not_effective"]
    content_block: dict[str, Any]


class ComplaintCreate(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    customer: str | None = Field(default=None, max_length=300)
    received_at: datetime.datetime | None = None
    channel: str | None = Field(default=None, max_length=100)
    severity: NcSeverity | None = None


class SpawnCapa(BaseModel):
    severity: NcSeverity | None = None
    process_id: uuid.UUID | None = None


class NcrCreate(BaseModel):
    source: NcrSource
    description: str = Field(min_length=1, max_length=4000)
    severity: NcSeverity
    process_id: uuid.UUID | None = None


class NcrDispositionBody(BaseModel):
    disposition: NcrDisposition
    notes: str | None = Field(default=None, max_length=2000)


class CapaTargetDate(BaseModel):
    target_completion_date: datetime.date | None


# --- serializers ------------------------------------------------------------------------------


def _stage(s: CapaStage, *, evidence_links: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(s.id),
        "stage": s.stage.value,
        "content_block": s.content_block,
        "cycle_marker": s.cycle_marker,
        "created_by": str(s.created_by),
        "created_at": s.created_at.isoformat(),
    }
    if evidence_links is not None:
        out["evidence_links"] = evidence_links
    return out


def _capa(
    c: Capa,
    identifier: str | None,
    *,
    title: str | None = None,
    created_at: datetime.datetime | None = None,
    raised_by: str | None = None,
    stages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(c.id),
        "identifier": identifier,
        "title": title,
        "source": c.source.value,
        "severity": c.severity.value,
        "process_id": str(c.process_id) if c.process_id else None,
        "close_state": c.close_state.value,
        "cycle_marker": c.cycle_marker,
        "origin_finding_id": str(c.origin_finding_id) if c.origin_finding_id else None,
        "raised_by": raised_by,
        "created_at": created_at.isoformat() if created_at else None,
        "target_completion_date": (
            c.target_completion_date.isoformat() if c.target_completion_date else None
        ),
        "overdue": (
            c.target_completion_date is not None
            and c.close_state not in (CapaCloseState.Closed, CapaCloseState.Rejected)
            and datetime.datetime.now(current_org_tz()).date() > c.target_completion_date
        ),
    }
    if stages is not None:
        out["stages"] = stages
    return out


async def _capa_full(
    session: AsyncSession, capa: Capa, stages: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Serialize a CAPA with its record header (identifier/title/created_at) populated — the single
    response builder for every single-CAPA endpoint (create + each transition + detail), so a write
    response never returns ``title``/``created_at`` as null for a CAPA that has them. ``raised_by``
    is derived from the loaded ``stages`` (detail only); ``None`` when stages aren't passed."""
    header = await capa_repo.get_capa_header(session, capa.id)
    raised_by = stages[0]["created_by"] if stages else None
    return _capa(
        capa,
        header[0] if header else None,
        title=header[1] if header else None,
        created_at=header[2] if header else None,
        raised_by=raised_by,
        stages=stages,
    )


def _approval_task(t: Task) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "stage_key": t.stage_key,
        "type": t.type.value,
        "state": t.state.value,
        "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None,
        "candidate_pool": t.candidate_pool,
        "action_expected": t.action_expected,
        "due_at": t.due_at.isoformat() if t.due_at else None,
    }


def _approval(instance: WorkflowInstance, tasks: list[Task]) -> dict[str, Any]:
    ctx = instance.context or {}
    return {
        "instance": {
            "id": str(instance.id),
            "current_state": instance.current_state,
            "definition_version": instance.definition_version,
            "subject_type": instance.subject_type.value,
            "subject_id": str(instance.subject_id),
            "tasks": [_approval_task(t) for t in tasks],
        },
        "proposed_action_plan": ctx.get("action_plan"),
    }


def _complaint(c: Complaint, identifier: str | None) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "identifier": identifier,
        "customer": c.customer,
        "received_at": c.received_at.isoformat() if c.received_at else None,
        "channel": c.channel,
        "description": c.description,
        "severity": c.severity.value if c.severity else None,
        "spawned_capa_id": str(c.spawned_capa_id) if c.spawned_capa_id else None,
    }


def _ncr(n: Ncr) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "identifier": n.identifier,
        "source": n.source.value,
        "description": n.description,
        "severity": n.severity.value,
        "process_id": str(n.process_id) if n.process_id else None,
        "disposition": n.disposition.value if n.disposition else None,
        "disposition_authorized_by": (
            str(n.disposition_authorized_by) if n.disposition_authorized_by else None
        ),
        "disposition_notes": n.disposition_notes,
        "disposed_at": n.disposed_at.isoformat() if n.disposed_at else None,
        "created_at": n.created_at.isoformat(),
    }


# --- scope helpers (PROCESS-scoped write keys) ------------------------------------------------


def _process_scope(process_id: uuid.UUID | None) -> ResourceContext:
    """The create-time scope from a body ``process_id`` (SYSTEM when none — an ad-hoc raise; a
    SYSTEM
    grant/override always matches)."""
    if process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(process_id)}))


async def _capa_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Resolve a CAPA's PROCESS authz scope from its ``process_id`` (SYSTEM fallback). Like the
    mature
    ``_audit_scope``, the resolver does NOT org-check — the service layer is the org boundary."""
    raw = request.path_params.get("capa_id")
    if not raw:
        return ResourceContext.system()
    try:
        capa_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    capa = await capa_repo.get_capa(session, capa_id)
    if capa is None or capa.process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(capa.process_id)}))


async def _ncr_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Resolve an NCR's PROCESS authz scope from its ``process_id`` (SYSTEM fallback)."""
    raw = request.path_params.get("ncr_id")
    if not raw:
        return ResourceContext.system()
    try:
        ncr_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    ncr = await capa_repo.get_ncr(session, ncr_id)
    if ncr is None or ncr.process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(ncr.process_id)}))


# Single-CAPA reads enforce capa.read at the CAPA's PROCESS scope (a SYSTEM grant still matches; a
# bound Process Owner matches their own process's CAPA; else 403). The LIST surface (GET /capas)
# row-filters instead (filter-not-403, doc 18 §5.2) via _readable_capas — a list can't enforce at
# one scope. Both unblock a bound Process Owner reaching the board the process-scoped raise targets.
_capa_read_scoped = require("capa.read", async_scope_resolver=_capa_scope)
_ncr_read = require("ncr.read")
_complaint_read = require("record.read")
_complaint_create = require("record.create")  # complaints are ad-hoc records (SYSTEM, no process)
_capa_update = require("capa.update", async_scope_resolver=_capa_scope)
_capa_record_rca = require("capa.record_rca", async_scope_resolver=_capa_scope)
_capa_plan_action = require("capa.plan_action", async_scope_resolver=_capa_scope)
_capa_implement = require("capa.capture_effectiveness", async_scope_resolver=_capa_scope)
_capa_verify = require("capa.verify", async_scope_resolver=_capa_scope)
_capa_close = require("capa.close", async_scope_resolver=_capa_scope)
_ncr_disposition = require("ncr.record_correction", async_scope_resolver=_ncr_scope)


# --- CAPA -------------------------------------------------------------------------------------


@router.post("/capas", status_code=status.HTTP_201_CREATED)
async def raise_capa_endpoint(
    body: CapaRaise,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    # capa.create is PROCESS-scoped; resolve the scope from the body (the records-capture precedent)
    # so a PROCESS grant matches once owner-assignment binds the placeholder. SYSTEM
    # grants/overrides
    # always match. A path-only dependency cannot see the body, hence in-handler enforce.
    await enforce(
        session, authz_sink, request, caller, "capa.create", _process_scope(body.process_id)
    )
    capa = await raise_capa(
        session,
        caller,
        title=body.title,
        severity=body.severity,
        source=body.source,
        process_id=body.process_id,
        problem=body.problem,
    )
    return await _capa_full(session, capa)


async def _readable_capas(
    request: Request, session: AsyncSession, caller: AppUser
) -> list[tuple[Capa, str | None, str | None, datetime.datetime | None]]:
    """The org's CAPAs the caller may ``capa.read``, row-filtered per-process (filter-not-403, doc
    18 §5.2 — the ``_readable_processes`` precedent). A SYSTEM grant matches every row (QMS Owner /
    Internal Auditor / a Top-Management approver / a ``demo`` override see the org-wide list
    byte-identical); a bound Process Owner's PROCESS-scoped ``capa.read`` narrows to CAPAs in their
    owned process(es); a process-less (ad-hoc/SYSTEM) CAPA needs a SYSTEM grant; a no-grant caller
    gets an empty list, never ``403``. ``source_ip`` is threaded so an ``ip_allow`` predicate
    evaluates exactly as the replaced ``require()`` enforce did (``ip_allow`` is v1-deferred)."""
    rows = await capa_repo.list_capas(session, caller.org_id)
    grants = await gather_grants(session, caller.id, caller.org_id, "capa.read")
    ctx = RequestContext(
        now=datetime.datetime.now(datetime.UTC),
        source_ip=request.client.host if request.client else None,
    )
    return [
        row
        for row in rows
        if authorize(grants, "capa.read", _process_scope(row[0].process_id), ctx).allow
    ]


@router.get("/capas")
async def list_capas_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await _readable_capas(request, session, caller)
    return {
        "data": [
            _capa(c, ident, title=title, created_at=created) for c, ident, title, created in rows
        ]
    }


@router.get("/capas/{capa_id}")
async def get_capa_endpoint(
    capa_id: uuid.UUID,
    caller: AppUser = Depends(_capa_read_scoped),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    capa = await capa_repo.get_capa(session, capa_id)
    if capa is None or capa.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="CAPA not found")
    stage_rows = await capa_repo.list_capa_stages(session, capa_id)
    evidence = await capa_repo.list_stage_evidence(session, [s.id for s in stage_rows])
    stages = [_stage(s, evidence_links=evidence.get(s.id, [])) for s in stage_rows]
    return await _capa_full(session, capa, stages=stages)


@router.patch("/capas/{capa_id}")
async def set_capa_target_date_endpoint(
    capa_id: uuid.UUID,
    body: CapaTargetDate,
    caller: AppUser = Depends(_capa_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Set/clear the CAPA's target-completion date (gate ``capa.update``). 409 on a terminal CAPA;
    clears the overdue stamp to re-arm the sweep."""
    capa = await set_capa_target_date(
        session, caller, capa_id, target_completion_date=body.target_completion_date
    )
    return await _capa_full(session, capa)


@router.get("/capas/{capa_id}/approval")
async def get_capa_approval_endpoint(
    capa_id: uuid.UUID,
    caller: AppUser = Depends(_capa_read_scoped),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    """The CAPA's current action-plan approval cycle (the latest CAPA workflow instance + its
    tasks + the proposed action plan from the instance context), or ``null`` when none has opened.
    Gated ``capa.read`` at the CAPA's PROCESS scope (the S-web-5 ``GET /documents/{id}/approval``
    mirror) — so a Top-Management approver, who holds only SYSTEM ``capa.read``, can read what they
    sign (the SYSTEM grant matches), and a bound Process Owner can read their own process's CAPA,
    both without ``document.read``."""
    capa = await capa_repo.get_capa(session, capa_id)
    if capa is None or capa.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="CAPA not found")
    instance = await wf_repo.latest_instance_for_subject(
        session, caller.org_id, WorkflowSubjectType.CAPA, capa.id
    )
    if instance is None:
        return None
    tasks = await wf_repo.list_instance_tasks(session, instance.id)
    return _approval(instance, tasks)


@router.post("/capas/{capa_id}/containment")
async def capa_containment_endpoint(
    capa_id: uuid.UUID,
    body: ContainmentCreate,
    caller: AppUser = Depends(_capa_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Raised → Containment: append the immediate-correction stage block (gate ``capa.update``)."""
    capa = await advance_capa_to_containment(
        session, caller, capa_id, content_block=body.content_block
    )
    return await _capa_full(session, capa)


@router.post("/capas/{capa_id}/root-cause")
async def capa_root_cause_endpoint(
    capa_id: uuid.UUID,
    body: RootCauseCreate,
    caller: AppUser = Depends(_capa_record_rca),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Containment → RootCause: append the sealed RCA stage block (gate ``capa.record_rca``).
    Unsigned — RCA is an informational gate; only the Action-Plan approval signs (doc 10 §6.2)."""
    capa = await advance_capa_to_root_cause(
        session, caller, capa_id, content_block=body.content_block
    )
    return await _capa_full(session, capa)


@router.post("/capas/{capa_id}/action-plan")
async def capa_action_plan_endpoint(
    capa_id: uuid.UUID,
    body: ActionPlanPropose,
    caller: AppUser = Depends(_capa_plan_action),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Propose the corrective Action Plan + open the severity-routed approval workflow (gate
    ``capa.plan_action``). ``close_state`` stays RootCause until the approval completes (the flip
    to ActionPlan happens on the approving ``POST /tasks/{id}/decision``). Returns the CAPA + the
    opened approval instance (``current_state`` is NEEDS_ATTENTION when no QMS-Owner / Top-Mgmt
    approver is assigned — assign one and re-propose)."""
    capa, instance = await propose_action_plan(
        session, caller, capa_id, content_block=body.content_block
    )
    out = await _capa_full(session, capa)
    out["approval_instance"] = {
        "id": str(instance.id),
        "current_state": instance.current_state,
        "definition_version": instance.definition_version,
    }
    return out


@router.post("/capas/{capa_id}/implement")
async def capa_implement_endpoint(
    capa_id: uuid.UUID,
    body: ImplementCreate,
    caller: AppUser = Depends(_capa_implement),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """ActionPlan → Implement: append the action-completion stage (gate
    ``capa.capture_effectiveness``). Unsigned; link completion evidence to the new Implement stage
    via ``POST /records/{id}/evidence-links`` (target_type=capa_stage)."""
    capa = await advance_capa_to_implement(
        session, caller, capa_id, content_block=body.content_block
    )
    return await _capa_full(session, capa)


@router.post("/capas/{capa_id}/verify")
async def capa_verify_endpoint(
    capa_id: uuid.UUID,
    body: VerifyCreate,
    caller: AppUser = Depends(_capa_verify),
    session: AsyncSession = Depends(get_session),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    """Implement → Verify: record the effectiveness ``decision`` as a SIGNED Verify stage (gate
    ``capa.verify``). Severity-aware SoD-4 (verifier ≠ implementer) is enforced in the service layer
    — after the permission gate, but never bypassed by a SYSTEM grant — before the signature is
    written (409 ``sod_self_verify``); then writes ``signature_event(meaning=verify)``. Link
    effectiveness evidence to the new Verify stage (it is then frozen — unlink-blocked)."""
    capa = await verify_capa(
        session,
        caller,
        capa_id,
        decision=body.decision,
        content_block=body.content_block,
        sig_sink=sig_sink,
    )
    return await _capa_full(session, capa)


@router.post("/capas/{capa_id}/close")
async def capa_close_endpoint(
    capa_id: uuid.UUID,
    caller: AppUser = Depends(_capa_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The M4 closure gate (gate ``capa.close``): an ``effective`` verification with root_cause +
    implemented-action-with-evidence + effectiveness-evidence → ``Closed``; a ``not_effective``
    verification loops back to RootCause (cycle++; re-propose + re-approve a revised plan); an
    ``effective`` verification still missing evidence → 409 ``capa_close_incomplete``."""
    capa = await close_capa(session, caller, capa_id)
    return await _capa_full(session, capa)


# --- Complaints -------------------------------------------------------------------------------


@router.post("/complaints", status_code=status.HTTP_201_CREATED)
async def capture_complaint_endpoint(
    body: ComplaintCreate,
    caller: AppUser = Depends(_complaint_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    complaint = await capture_complaint(
        session,
        caller,
        description=body.description,
        customer=body.customer,
        received_at=body.received_at,
        channel=body.channel,
        severity=body.severity,
    )
    return _complaint(complaint, await capa_repo.get_identifier(session, complaint.id))


@router.get("/complaints")
async def list_complaints_endpoint(
    caller: AppUser = Depends(_complaint_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await capa_repo.list_complaints(session, caller.org_id)
    return {"data": [_complaint(c, ident) for c, ident in rows]}


@router.get("/complaints/{complaint_id}")
async def get_complaint_endpoint(
    complaint_id: uuid.UUID,
    caller: AppUser = Depends(_complaint_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    complaint = await capa_repo.get_complaint(session, complaint_id)
    if complaint is None or complaint.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Complaint not found")
    return _complaint(complaint, await capa_repo.get_identifier(session, complaint.id))


@router.post("/complaints/{complaint_id}/spawn-capa")
async def spawn_capa_endpoint(
    complaint_id: uuid.UUID,
    body: SpawnCapa,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> JSONResponse:
    """Idempotently spawn a CAPA from a complaint (gate ``capa.create``). 201 on first spawn; 200 on
    an idempotent replay (the complaint already spawned — same CAPA returned)."""
    await enforce(
        session, authz_sink, request, caller, "capa.create", _process_scope(body.process_id)
    )
    capa, created = await spawn_capa_from_complaint(
        session, caller, complaint_id, severity=body.severity, process_id=body.process_id
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=await _capa_full(session, capa),
    )


# --- NCRs -------------------------------------------------------------------------------------


@router.post("/ncrs", status_code=status.HTTP_201_CREATED)
async def create_ncr_endpoint(
    body: NcrCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    await enforce(
        session, authz_sink, request, caller, "ncr.create", _process_scope(body.process_id)
    )
    ncr = await create_ncr(
        session,
        caller,
        source=body.source,
        description=body.description,
        severity=body.severity,
        process_id=body.process_id,
    )
    return _ncr(ncr)


@router.get("/ncrs")
async def list_ncrs_endpoint(
    caller: AppUser = Depends(_ncr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await capa_repo.list_ncrs(session, caller.org_id)
    return {"data": [_ncr(n) for n in rows]}


@router.get("/ncrs/{ncr_id}")
async def get_ncr_endpoint(
    ncr_id: uuid.UUID,
    caller: AppUser = Depends(_ncr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ncr = await capa_repo.get_ncr(session, ncr_id)
    if ncr is None or ncr.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="NCR not found")
    return _ncr(ncr)


@router.patch("/ncrs/{ncr_id}/disposition")
async def ncr_disposition_endpoint(
    ncr_id: uuid.UUID,
    body: NcrDispositionBody,
    caller: AppUser = Depends(_ncr_disposition),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Record the ISO 9001 8.7 disposition (gate ``ncr.record_correction``). One-shot (409 if
    set)."""
    ncr = await record_ncr_disposition(
        session, caller, ncr_id, disposition=body.disposition, notes=body.notes
    )
    return _ncr(ncr)
