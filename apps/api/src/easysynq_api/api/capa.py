"""The CAPA / NCR / Complaint surface (slice S-capa-1; doc 02 Cl 8.7/10.2, doc 10 §6, doc 15).

All keys are the seeded doc-07 catalog (no new keys; S-capa-1 backfills the orphaned grants). The
CAPA-write keys (``capa.create`` / ``capa.update`` / ``ncr.create`` / ``ncr.record_correction``) are
``finest_scope=PROCESS``, so their gates resolve a PROCESS ``ResourceContext`` from the relevant
process (the body's ``process_id`` for creates — in-handler ``enforce``, the ``records`` capture
precedent; the row's ``process_id`` for path-id writes — an async scope resolver, the
``_audit_scope``
precedent), with a SYSTEM fallback so a SYSTEM grant/override always matches. A concrete PROCESS
grant
matches once owner-assignment binds the seeded ``:assignment_process`` placeholder. Reads
(``capa.read`` / ``ncr.read`` / ``record.read`` for complaints) gate at SYSTEM + an org-scoped query
(the S-aud-1 audits-list precedent). Complaint capture rides ``record.create`` (a complaint IS a
record); the complaint→CAPA spawn rides ``capa.create``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._capa_enums import CapaSource, NcrDisposition, NcrSource, NcSeverity
from ..db.models.app_user import AppUser
from ..db.models.capa import Capa
from ..db.models.capa_stage import CapaStage
from ..db.models.complaint import Complaint
from ..db.models.ncr import Ncr
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..problems import ProblemException
from ..services.authz import AuthzAuditSink, enforce, get_authz_audit_sink, require
from ..services.capa import (
    advance_capa_to_containment,
    advance_capa_to_root_cause,
    capture_complaint,
    create_ncr,
    propose_action_plan,
    raise_capa,
    record_ncr_disposition,
    spawn_capa_from_complaint,
)
from ..services.capa import repository as capa_repo

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


# --- serializers ------------------------------------------------------------------------------


def _stage(s: CapaStage) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "stage": s.stage.value,
        "content_block": s.content_block,
        "cycle_marker": s.cycle_marker,
        "created_by": str(s.created_by),
        "created_at": s.created_at.isoformat(),
    }


def _capa(
    c: Capa, identifier: str | None, stages: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(c.id),
        "identifier": identifier,
        "source": c.source.value,
        "severity": c.severity.value,
        "process_id": str(c.process_id) if c.process_id else None,
        "close_state": c.close_state.value,
        "cycle_marker": c.cycle_marker,
        "origin_finding_id": str(c.origin_finding_id) if c.origin_finding_id else None,
    }
    if stages is not None:
        out["stages"] = stages
    return out


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


_capa_read = require("capa.read")
_ncr_read = require("ncr.read")
_complaint_read = require("record.read")
_complaint_create = require("record.create")  # complaints are ad-hoc records (SYSTEM, no process)
_capa_update = require("capa.update", async_scope_resolver=_capa_scope)
_capa_record_rca = require("capa.record_rca", async_scope_resolver=_capa_scope)
_capa_plan_action = require("capa.plan_action", async_scope_resolver=_capa_scope)
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
    return _capa(capa, await capa_repo.get_identifier(session, capa.id))


@router.get("/capas")
async def list_capas_endpoint(
    caller: AppUser = Depends(_capa_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await capa_repo.list_capas(session, caller.org_id)
    return {"data": [_capa(c, ident) for c, ident in rows]}


@router.get("/capas/{capa_id}")
async def get_capa_endpoint(
    capa_id: uuid.UUID,
    caller: AppUser = Depends(_capa_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    capa = await capa_repo.get_capa(session, capa_id)
    if capa is None or capa.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="CAPA not found")
    stages = [_stage(s) for s in await capa_repo.list_capa_stages(session, capa_id)]
    return _capa(capa, await capa_repo.get_identifier(session, capa.id), stages=stages)


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
    return _capa(capa, await capa_repo.get_identifier(session, capa.id))


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
    return _capa(capa, await capa_repo.get_identifier(session, capa.id))


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
    out = _capa(capa, await capa_repo.get_identifier(session, capa.id))
    out["approval_instance"] = {
        "id": str(instance.id),
        "current_state": instance.current_state,
        "definition_version": instance.definition_version,
    }
    return out


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
        content=_capa(capa, await capa_repo.get_identifier(session, capa.id)),
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
