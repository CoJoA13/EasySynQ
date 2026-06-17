"""The internal-audit surface (slice S-aud-1; doc 02 Cl 9.2, doc 10 §5, doc 15).

Programmes + plans are the maintained schedule (gate ``audit.plan``, SYSTEM scope — QMS Owner);
audits are retained records created from a plan (``audit.create``, SYSTEM scope — Internal Auditor)
and walked through the FSM (``audit.conduct`` / ``audit.close``, PROCESS scope — resolved from the
audit's plan's auditee process, with a SYSTEM-override fallback per the v1 override posture). Reads
are ``audit.read`` (SYSTEM; org-scoped rows). All keys are the seeded doc-07 catalog — no new keys.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._capa_enums import NcSeverity
from ..db.models._iso_audit_enums import AuditState, FindingType
from ..db.models.app_user import AppUser
from ..db.models.audit import Audit
from ..db.models.audit_finding import AuditFinding
from ..db.models.audit_plan import AuditPlan
from ..db.models.audit_program import AuditProgram
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..problems import ProblemException
from ..services.audits import (
    advance_audit,
    correct_finding,
    create_audit,
    create_audit_plan,
    create_audit_program,
    create_finding,
    raise_initiative_from_finding,
    update_audit_program,
)
from ..services.audits import repository as audits_repo
from ..services.authz import require

# Reuse the canonical improvement-initiative serializer (one source → no drift). api/improvement
# imports only services, so there is no import cycle (the api/objectives↔api/workflow precedent).
from .improvement import _initiative

router = APIRouter(prefix="/api/v1", tags=["audits"])


# --- request bodies ---------------------------------------------------------------------------


class AuditProgramCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    period: str | None = Field(default=None, max_length=100)
    coverage: dict[str, Any] | None = None


class AuditProgramUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    period: str | None = Field(default=None, max_length=100)
    coverage: dict[str, Any] | None = None
    archived: bool | None = None


class AuditPlanCreate(BaseModel):
    auditee_process_id: uuid.UUID | None = None
    lead_auditor_user_id: uuid.UUID | None = None
    scheduled_date: datetime.date | None = None
    checklist_ref: str | None = Field(default=None, max_length=300)


class AuditCreate(BaseModel):
    plan_id: uuid.UUID
    title: str | None = Field(default=None, min_length=1, max_length=300)
    lead_auditor_user_id: uuid.UUID | None = None


class FindingCreate(BaseModel):
    finding_type: FindingType
    # Required for NC (the auto-CAPA needs one; service 422s if absent); optional for OBS/OFI.
    severity: NcSeverity | None = None
    clause_ref: str | None = Field(default=None, max_length=100)
    process_ref: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, min_length=1, max_length=300)


class FindingCorrection(BaseModel):
    finding_type: FindingType
    severity: NcSeverity | None = None
    clause_ref: str | None = Field(default=None, max_length=100)
    process_ref: str | None = Field(default=None, max_length=300)
    reason: str | None = Field(default=None, max_length=300)


class FindingInitiativeCreate(BaseModel):
    """Body for raising an improvement initiative from a finding (S-improvement-2). The
    initiative is human-authored (forward-looking activity), distinct from the finding's record;
    ``source``/``source_link_id``/``process_id`` derive from the finding + audit, not the body."""

    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    target_outcome: str | None = Field(default=None, max_length=4000)
    owner_user_id: uuid.UUID | None = None


# --- serializers ------------------------------------------------------------------------------


def _program(p: AuditProgram) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "identifier": p.identifier,
        "title": p.title,
        "period": p.period,
        "coverage": p.coverage,
        "archived": p.archived,
        "created_at": p.created_at.isoformat(),
    }


def _plan(p: AuditPlan) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "program_id": str(p.program_id),
        "auditee_process_id": str(p.auditee_process_id) if p.auditee_process_id else None,
        "lead_auditor_user_id": str(p.lead_auditor_user_id) if p.lead_auditor_user_id else None,
        "scheduled_date": p.scheduled_date.isoformat() if p.scheduled_date else None,
        "checklist_ref": p.checklist_ref,
        "created_at": p.created_at.isoformat(),
    }


def _audit(
    a: Audit,
    identifier: str | None = None,
    title: str | None = None,
    created_at: datetime.datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "identifier": identifier,
        "title": title,
        "plan_id": str(a.plan_id),
        "lead_auditor_user_id": str(a.lead_auditor_user_id) if a.lead_auditor_user_id else None,
        "state": a.state.value,
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "result_summary": a.result_summary,
        "created_at": created_at.isoformat() if created_at else None,
    }


async def _audit_full(session: AsyncSession, a: Audit) -> dict[str, Any]:
    """Serialize an audit with its record header populated — used by every single-audit response
    (create + detail + each FSM transition), so a write never returns identifier/title as null."""
    header = await audits_repo.get_audit_header(session, a.id)
    identifier, title, created_at = header if header else (None, None, None)
    return _audit(a, identifier, title, created_at)


def _finding(
    f: AuditFinding,
    identifier: str | None,
    title: str | None = None,
    *,
    correction_of: uuid.UUID | None = None,
    superseded_by_correction: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "identifier": identifier,
        "title": title,
        "audit_id": str(f.audit_id),
        "finding_type": f.finding_type.value,
        "severity": f.severity.value if f.severity else None,
        "clause_ref": f.clause_ref,
        "process_ref": f.process_ref,
        "auto_capa_id": str(f.auto_capa_id) if f.auto_capa_id else None,
        "correction_of": str(correction_of) if correction_of else None,
        "superseded_by_correction": (
            str(superseded_by_correction) if superseded_by_correction else None
        ),
    }


# --- scope resolver (PROCESS keys: audit.conduct / audit.close) -------------------------------


async def _audit_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Resolve the audit's PROCESS authz scope from its plan's auditee process. A SYSTEM grant
    always matches; a concrete PROCESS grant matches once owner-assignment writes real bindings; an
    unset auditee process (or a bad id) falls back to SYSTEM so a SYSTEM override still works.

    Like the mature ``_document_scope_by_id`` precedent, the resolver does NOT org-check the loaded
    row — the authoritative org boundary is the service layer (``advance_audit`` 404s a cross-org
    audit). A cross-org process_id here cannot escalate: it matches none of the caller's own-org
    grants, and a SYSTEM grant would allow regardless. The cross-org service guard is test-proven
    (test_audits::test_cross_org_advance_is_denied)."""
    raw = request.path_params.get("audit_id")
    if not raw:
        return ResourceContext.system()
    try:
        audit_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    audit = await audits_repo.get_audit(session, audit_id)
    if audit is None:
        return ResourceContext.system()
    plan = await audits_repo.get_audit_plan(session, audit.plan_id)
    if plan is None or plan.auditee_process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(plan.auditee_process_id)}))


async def _finding_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Resolve a finding's PROCESS authz scope via its audit's plan auditee process (the
    ``_audit_scope`` shape, one hop deeper). SYSTEM fallback so a SYSTEM grant/override matches; no
    org-check (the service layer is the org boundary)."""
    raw = request.path_params.get("finding_id")
    if not raw:
        return ResourceContext.system()
    try:
        finding_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    finding = await audits_repo.get_finding(session, finding_id)
    if finding is None:
        return ResourceContext.system()
    audit = await audits_repo.get_audit(session, finding.audit_id)
    if audit is None:
        return ResourceContext.system()
    plan = await audits_repo.get_audit_plan(session, audit.plan_id)
    if plan is None or plan.auditee_process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(plan.auditee_process_id)}))


_read = require("audit.read")
_plan_gate = require("audit.plan")
_create = require("audit.create")
_conduct = require("audit.conduct", async_scope_resolver=_audit_scope)
_close = require("audit.close", async_scope_resolver=_audit_scope)
# finding.create gates the create (scope = the audit's process) + the correction (scope = the
# finding's audit's process). Keyword async_scope_resolver= is REQUIRED (positional slot is SYNC).
_finding_create = require("finding.create", async_scope_resolver=_audit_scope)
_finding_correct = require("finding.create", async_scope_resolver=_finding_scope)
_finding_read = require(
    "finding.read"
)  # SYSTEM default + org-scoped query (the family read precedent)
# raise-initiative (S-improvement-2): gate improvement.manage at the finding's audit auditee process
# (the _finding_scope path resolver) — NOT a finding.* key (R46 rejected riding capa.*/finding.* for
# improvement; a Process Owner of the audited process or a SYSTEM grant raises it).
_raise_initiative = require("improvement.manage", async_scope_resolver=_finding_scope)


# --- programmes -------------------------------------------------------------------------------


@router.post("/audit-programs", status_code=status.HTTP_201_CREATED)
async def create_program_endpoint(
    body: AuditProgramCreate,
    caller: AppUser = Depends(_plan_gate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    program = await create_audit_program(
        session, caller, title=body.title, period=body.period, coverage=body.coverage
    )
    return _program(program)


@router.get("/audit-programs")
async def list_programs_endpoint(
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await audits_repo.list_audit_programs(session, caller.org_id)
    return {"data": [_program(p) for p in rows]}


@router.get("/audit-programs/{program_id}")
async def get_program_endpoint(
    program_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    program = await audits_repo.get_audit_program(session, program_id)
    if program is None or program.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Audit programme not found")
    return _program(program)


@router.patch("/audit-programs/{program_id}")
async def update_program_endpoint(
    program_id: uuid.UUID,
    body: AuditProgramUpdate,
    caller: AppUser = Depends(_plan_gate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    program = await update_audit_program(
        session,
        caller,
        program_id,
        title=body.title,
        period=body.period,
        coverage=body.coverage,
        archived=body.archived,
    )
    return _program(program)


# --- plans ------------------------------------------------------------------------------------


@router.post("/audit-programs/{program_id}/plans", status_code=status.HTTP_201_CREATED)
async def create_plan_endpoint(
    program_id: uuid.UUID,
    body: AuditPlanCreate,
    caller: AppUser = Depends(_plan_gate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    plan = await create_audit_plan(
        session,
        caller,
        program_id,
        auditee_process_id=body.auditee_process_id,
        lead_auditor_user_id=body.lead_auditor_user_id,
        scheduled_date=body.scheduled_date,
        checklist_ref=body.checklist_ref,
    )
    return _plan(plan)


@router.get("/audit-programs/{program_id}/plans")
async def list_plans_endpoint(
    program_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    program = await audits_repo.get_audit_program(session, program_id)
    if program is None or program.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Audit programme not found")
    rows = await audits_repo.list_audit_plans(session, program_id)
    return {"data": [_plan(p) for p in rows]}


@router.get("/audit-plans/{plan_id}")
async def get_plan_endpoint(
    plan_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    plan = await audits_repo.get_audit_plan(session, plan_id)
    if plan is None or plan.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Audit plan not found")
    return _plan(plan)


# --- audits -----------------------------------------------------------------------------------


@router.post("/audits", status_code=status.HTTP_201_CREATED)
async def create_audit_endpoint(
    body: AuditCreate,
    caller: AppUser = Depends(_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    audit = await create_audit(
        session,
        caller,
        plan_id=body.plan_id,
        title=body.title,
        lead_auditor_user_id=body.lead_auditor_user_id,
    )
    return await _audit_full(session, audit)


@router.get("/audits")
async def list_audits_endpoint(
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await audits_repo.list_audits(session, caller.org_id)
    return {"data": [_audit(a, ident, title, created) for a, ident, title, created in rows]}


@router.get("/audits/{audit_id}")
async def get_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    audit = await audits_repo.get_audit(session, audit_id)
    if audit is None or audit.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Audit not found")
    return await _audit_full(session, audit)


# --- FSM transitions (flat-action sub-resources, doc 15) --------------------------------------


@router.post("/audits/{audit_id}/plan")
async def plan_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_conduct),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Scheduled → Planned (the lead auditor finalizes the plan). The audit-INSTANCE FSM is
    uniformly auditor-driven (audit.conduct / audit.close, PROCESS scope); audit.plan governs the
    programme + plan SCHEDULE, not an instance transition."""
    audit = await advance_audit(session, caller, audit_id, AuditState.Planned)
    return await _audit_full(session, audit)


@router.post("/audits/{audit_id}/conduct")
async def conduct_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_conduct),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Planned → InProgress (the auditor begins)."""
    audit = await advance_audit(session, caller, audit_id, AuditState.InProgress)
    return await _audit_full(session, audit)


@router.post("/audits/{audit_id}/draft-findings")
async def draft_findings_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_conduct),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """InProgress → FindingsDraft."""
    audit = await advance_audit(session, caller, audit_id, AuditState.FindingsDraft)
    return await _audit_full(session, audit)


@router.post("/audits/{audit_id}/report")
async def report_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_conduct),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """FindingsDraft → Reported."""
    audit = await advance_audit(session, caller, audit_id, AuditState.Reported)
    return await _audit_full(session, audit)


@router.post("/audits/{audit_id}/begin-closing")
async def begin_closing_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Reported → Closing."""
    audit = await advance_audit(session, caller, audit_id, AuditState.Closing)
    return await _audit_full(session, audit)


@router.post("/audits/{audit_id}/close")
async def close_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Closing → Closed (the close gate: 409 ``audit_close_blocked`` if any live NC lacks a Closed
    CAPA)."""
    audit = await advance_audit(session, caller, audit_id, AuditState.Closed)
    return await _audit_full(session, audit)


# --- findings (record subtype) ----------------------------------------------------------------


@router.post("/audits/{audit_id}/findings", status_code=status.HTTP_201_CREATED)
async def create_finding_endpoint(
    audit_id: uuid.UUID,
    body: FindingCreate,
    caller: AppUser = Depends(_finding_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Log a finding (gate ``finding.create``, PROCESS scope = the audit's auditee process). An NC
    auto-creates its mandatory CAPA in the same transaction; OBS/OFI do not."""
    finding = await create_finding(
        session,
        caller,
        audit_id,
        finding_type=body.finding_type,
        severity=body.severity,
        clause_ref=body.clause_ref,
        process_ref=body.process_ref,
        summary=body.summary,
    )
    row = await audits_repo.get_finding_row(session, finding.id)
    if row is None:  # pragma: no cover — written in this txn; structured 500 over a bare assert
        raise ProblemException(
            status=500, code="internal_error", title="Finding row missing after create"
        )
    f, ident, title, co, sbc = row
    return _finding(f, ident, title, correction_of=co, superseded_by_correction=sbc)


@router.get("/audits/{audit_id}/findings")
async def list_findings_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_finding_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    audit = await audits_repo.get_audit(session, audit_id)
    if audit is None or audit.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Audit not found")
    rows = await audits_repo.list_findings(session, audit_id)
    return {
        "data": [
            _finding(f, ident, title, correction_of=co, superseded_by_correction=sbc)
            for f, ident, title, co, sbc in rows
        ]
    }


@router.get("/findings/{finding_id}")
async def get_finding_endpoint(
    finding_id: uuid.UUID,
    caller: AppUser = Depends(_finding_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await audits_repo.get_finding_row(session, finding_id)
    if row is None or row[0].org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Finding not found")
    finding, ident, title, co, sbc = row
    return _finding(finding, ident, title, correction_of=co, superseded_by_correction=sbc)


@router.post("/findings/{finding_id}/correction", status_code=status.HTTP_201_CREATED)
async def correct_finding_endpoint(
    finding_id: uuid.UUID,
    body: FindingCorrection,
    caller: AppUser = Depends(_finding_correct),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Correct/retype a finding (gate ``finding.create``). General retype: to NC auto-creates its
    CAPA; NC->OBS/OFI declassifies (clears the close gate). Returns the superseding successor."""
    successor = await correct_finding(
        session,
        caller,
        finding_id,
        finding_type=body.finding_type,
        severity=body.severity,
        clause_ref=body.clause_ref,
        process_ref=body.process_ref,
        reason=body.reason,
    )
    row = await audits_repo.get_finding_row(session, successor.id)
    if row is None:  # pragma: no cover — written in this txn; structured 500 over a bare assert
        raise ProblemException(
            status=500, code="internal_error", title="Finding row missing after correction"
        )
    f, ident, title, co, sbc = row
    return _finding(f, ident, title, correction_of=co, superseded_by_correction=sbc)


@router.post("/findings/{finding_id}/raise-initiative")
async def raise_initiative_from_finding_endpoint(
    finding_id: uuid.UUID,
    body: FindingInitiativeCreate,
    caller: AppUser = Depends(_raise_initiative),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    """Raise an improvement initiative from an OBSERVATION/OFI finding (S-improvement-2; gate
    ``improvement.manage`` at the finding's audit auditee process). 422
    ``finding_not_improvable`` on an NC, 404 on an unknown finding. 1:N + Idempotency-Key (201 new /
    200 replay). ``source=OFI`` + ``source_link_id=finding.id``; inherits the audited process."""
    initiative, created = await raise_initiative_from_finding(
        session,
        caller,
        finding_id,
        title=body.title,
        description=body.description,
        target_outcome=body.target_outcome,
        owner_user_id=body.owner_user_id,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=_initiative(initiative),
    )
