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

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._iso_audit_enums import AuditState
from ..db.models.app_user import AppUser
from ..db.models.audit import Audit
from ..db.models.audit_plan import AuditPlan
from ..db.models.audit_program import AuditProgram
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..problems import ProblemException
from ..services.audits import (
    advance_audit,
    create_audit,
    create_audit_plan,
    create_audit_program,
    update_audit_program,
)
from ..services.audits import repository as audits_repo
from ..services.authz import require

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


def _audit(a: Audit) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "plan_id": str(a.plan_id),
        "lead_auditor_user_id": str(a.lead_auditor_user_id) if a.lead_auditor_user_id else None,
        "state": a.state.value,
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "result_summary": a.result_summary,
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


_read = require("audit.read")
_plan_gate = require("audit.plan")
_create = require("audit.create")
_conduct = require("audit.conduct", async_scope_resolver=_audit_scope)
_close = require("audit.close", async_scope_resolver=_audit_scope)


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
    return _audit(audit)


@router.get("/audits")
async def list_audits_endpoint(
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await audits_repo.list_audits(session, caller.org_id)
    return {"data": [_audit(a) for a in rows]}


@router.get("/audits/{audit_id}")
async def get_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    audit = await audits_repo.get_audit(session, audit_id)
    if audit is None or audit.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Audit not found")
    return _audit(audit)


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
    return _audit(await advance_audit(session, caller, audit_id, AuditState.Planned))


@router.post("/audits/{audit_id}/conduct")
async def conduct_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_conduct),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Planned → InProgress (the auditor begins)."""
    return _audit(await advance_audit(session, caller, audit_id, AuditState.InProgress))


@router.post("/audits/{audit_id}/draft-findings")
async def draft_findings_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_conduct),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """InProgress → FindingsDraft."""
    return _audit(await advance_audit(session, caller, audit_id, AuditState.FindingsDraft))


@router.post("/audits/{audit_id}/report")
async def report_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_conduct),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """FindingsDraft → Reported."""
    return _audit(await advance_audit(session, caller, audit_id, AuditState.Reported))


@router.post("/audits/{audit_id}/begin-closing")
async def begin_closing_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Reported → Closing."""
    return _audit(await advance_audit(session, caller, audit_id, AuditState.Closing))


@router.post("/audits/{audit_id}/close")
async def close_audit_endpoint(
    audit_id: uuid.UUID,
    caller: AppUser = Depends(_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Closing → Closed (the close gate; a no-op until S-aud-2 wires the NC-CAPA check)."""
    return _audit(await advance_audit(session, caller, audit_id, AuditState.Closed))
