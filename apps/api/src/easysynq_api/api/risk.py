"""The Risk & Opportunity register surface (clause 6.1, S-risk-1).

Rides the seeded ``register.read`` / ``register.manage`` keys (PROCESS-scoped — NO new key, catalog
102). ``GET /risks`` is a filter-not-403 row-filter; ``GET /risks/{id}`` enforces at the row's
PROCESS
scope; ``POST`` enforces ``register.manage`` over the body ``process_id``; ``PATCH`` re-enforces the
NEW target on a ``process_id`` reassign (the ``_enforce_target_process`` escalation guard).
``risk_rating``
is server-derived (read-only in the response); the displayed band is graded against the v1
golden-pinned criteria (S-risk-1b routes this through the governing version's frozen criteria).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._risk_enums import RiskOpportunityType, ScoringMethod
from ..db.models.app_user import AppUser
from ..db.models.risk_opportunity import RiskOpportunity
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..domain.risk.rules import BAND_RANK, BAND_TONE, default_criteria, risk_band
from ..problems import ProblemException
from ..services.authz import (
    AuthzAuditSink,
    enforce,
    gather_grants,
    get_authz_audit_sink,
    require,
)
from ..services.risk import (
    add_risk_row,
    get_risk,
    list_risks,
    update_risk_row,
)
from ..services.vault import VaultAuditSink, get_vault_audit_sink

router = APIRouter(prefix="/api/v1", tags=["risk"])


# --- request bodies ---
class RiskCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )  # reject unknown fields (the openapi additionalProperties:false)
    type: RiskOpportunityType
    description: str = Field(min_length=1, max_length=4000)
    likelihood: int = Field(ge=1, le=5)
    severity: int = Field(ge=1, le=5)
    scoring_method: ScoringMethod = ScoringMethod.MATRIX_5X5
    process_id: uuid.UUID | None = None
    clause_id: uuid.UUID | None = None
    treatment: str | None = Field(default=None, max_length=4000)


class RiskUpdate(BaseModel):
    """Partial PATCH. Omitted ≠ null (``model_fields_set`` via ``exclude_unset``): an explicit null
    clears a nullable field, a null on a NOT-NULL field 422s. ``scoring_method`` is write-once."""

    model_config = ConfigDict(extra="forbid")  # a typo'd field 422s, never a silent no-op (Codex)
    type: RiskOpportunityType | None = None
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    likelihood: int | None = Field(default=None, ge=1, le=5)
    severity: int | None = Field(default=None, ge=1, le=5)
    scoring_method: ScoringMethod | None = None
    process_id: uuid.UUID | None = None
    clause_id: uuid.UUID | None = None
    treatment: str | None = Field(default=None, max_length=4000)
    effectiveness: str | None = Field(default=None, max_length=4000)


# --- authz scope ---
def _risk_scope(process_id: uuid.UUID | None) -> ResourceContext:
    """The PROCESS scope from a row's own ``process_id`` (SYSTEM when none — an org-level row, only
    a
    SYSTEM grant matches). R48 own-id-only; NO ``artifact_id`` (a shared head id would let an
    ARTIFACT-scoped ``register.read`` read every row)."""
    if process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(process_id)}))


async def _risk_path_scope(request: Request, session: AsyncSession) -> ResourceContext:
    raw = request.path_params.get("risk_id")
    if not raw:
        return ResourceContext.system()
    try:
        risk_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    row = await get_risk(session, risk_id)
    if row is None or row.process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(row.process_id)}))


_risk_read_scoped = require("register.read", async_scope_resolver=_risk_path_scope)
_risk_manage_path = require("register.manage", async_scope_resolver=_risk_path_scope)


# --- serializer ---
def _risk(row: RiskOpportunity) -> dict[str, Any]:
    # S-risk-1b: route this through register_content.resolve_criteria(governing) once publish/freeze
    # lands. In S-risk-1 the criteria are the golden-pinned default (no Effective version exists
    # yet);
    # the golden test is the criteria-immutability guard in the absence of versioning.
    band = risk_band(row.risk_rating, default_criteria(row.scoring_method))
    return {
        "id": str(row.id),
        "register_doc_id": str(row.register_doc_id),
        "type": row.type.value,
        "description": row.description,
        "process_id": str(row.process_id) if row.process_id else None,
        "clause_id": str(row.clause_id) if row.clause_id else None,
        "likelihood": row.likelihood,
        "severity": row.severity,
        "risk_rating": row.risk_rating,
        "scoring_method": row.scoring_method.value,
        "band": band.value,
        "band_tone": BAND_TONE[band],
        "band_rank": BAND_RANK[band],
        "treatment": row.treatment,
        "effectiveness": row.effectiveness,
        "linked_capa_id": str(row.linked_capa_id) if row.linked_capa_id else None,
        "row_version": row.row_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# --- endpoints ---
async def _readable_risks(
    request: Request, session: AsyncSession, caller: AppUser
) -> list[RiskOpportunity]:
    """The org's risk rows the caller may ``register.read``, row-filtered per-process
    (filter-not-403,
    doc 18 §5.2). A SYSTEM grant matches every row (QMS Owner / Internal Auditor byte-identical); a
    bound Process Owner's PROCESS-scoped grant narrows to their owned process(es); an org-level
    (process-less) row needs a SYSTEM grant; a no-grant caller gets an empty list, never 403.
    ``source_ip`` is threaded so an ``ip_allow`` predicate evaluates as the replaced enforce did."""
    rows = await list_risks(session, caller.org_id)
    grants = await gather_grants(session, caller.id, caller.org_id, "register.read")
    ctx = RequestContext(
        now=datetime.datetime.now(datetime.UTC),
        source_ip=request.client.host if request.client else None,
    )
    return [
        row
        for row in rows
        if authorize(grants, "register.read", _risk_scope(row.process_id), ctx).allow
    ]


@router.get("/risks")
async def list_risks_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await _readable_risks(request, session, caller)
    return {"data": [_risk(r) for r in rows]}


@router.get("/risks/{risk_id}")
async def get_risk_endpoint(
    risk_id: uuid.UUID,
    caller: AppUser = Depends(_risk_read_scoped),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await get_risk(session, risk_id)
    if row is None or row.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Risk not found")
    return _risk(row)


@router.post("/risks", status_code=status.HTTP_201_CREATED)
async def create_risk_endpoint(
    body: RiskCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    # register.manage is PROCESS-scoped; resolve the scope from the body so a bound owner matches
    # their own process. A null process_id → SYSTEM (org-level row, SYSTEM grant only). A path-only
    # dependency cannot see the body, hence the in-handler enforce (the raise_capa precedent).
    await enforce(
        session, authz_sink, request, caller, "register.manage", _risk_scope(body.process_id)
    )
    row = await add_risk_row(
        session,
        vault_sink,
        caller,
        type=body.type,
        description=body.description,
        likelihood=body.likelihood,
        severity=body.severity,
        scoring_method=body.scoring_method,
        process_id=body.process_id,
        clause_id=body.clause_id,
        treatment=body.treatment,
    )
    return _risk(row)


@router.patch("/risks/{risk_id}")
async def update_risk_endpoint(
    risk_id: uuid.UUID,
    body: RiskUpdate,
    request: Request,
    caller: AppUser = Depends(_risk_manage_path),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    updates = body.model_dump(exclude_unset=True)
    # Setting/changing process_id re-enforces register.manage over the NEW target (the
    # _enforce_target_process escalation guard) — _risk_manage_path already authorized the CURRENT
    # process. ⚠ Gate on PRESENCE, not non-null: an explicit ``process_id: null`` (clearing the row
    # to org-level) must enforce at SYSTEM scope (`_risk_scope(None)`), else a bound PROCESS owner
    # could downgrade their row to an org-level row they cannot create directly. R48 own-id-only.
    if "process_id" in updates:
        await enforce(
            session,
            authz_sink,
            request,
            caller,
            "register.manage",
            _risk_scope(updates["process_id"]),
        )
    row = await update_risk_row(session, authz_sink, request, caller, risk_id, updates=updates)
    return _risk(row)
