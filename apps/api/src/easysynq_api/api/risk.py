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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._risk_enums import RiskOpportunityType, ScoringMethod
from ..db.models.app_user import AppUser
from ..db.models.document_type import DocumentType
from ..db.models.documented_information import DocumentedInformation
from ..db.models.risk_opportunity import RiskOpportunity
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..domain.risk.register_content import resolve_criteria
from ..domain.risk.rules import BAND_RANK, BAND_TONE, risk_band
from ..domain.risk.summary import summarize_register
from ..problems import ProblemException
from ..services.authz import (
    AuthzAuditSink,
    enforce,
    gather_grants,
    get_authz_audit_sink,
    require,
)
from ..services.authz.register_caps import register_capabilities
from ..services.risk import (
    add_risk_row,
    find_head,
    get_risk,
    governing_register,
    list_risks,
    publish_register,
    spawn_capa_for_risk,
    start_register_revision,
    update_risk_row,
)
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    get_vault_audit_sink,
    get_vault_signature_sink,
    release,
)
from ..services.vault.release_scope import enrich_release_sod_scope
from .capa import (
    _capa_full,
)  # the single CAPA response builder (the complaint→CAPA spawn precedent)

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


class RegisterPublish(BaseModel):
    """The optional publish body — an INV-3 change reason for the frozen register version (defaults
    to a system-generated reason when omitted; a no-freeze re-publish ignores it, as in OBJ)."""

    model_config = ConfigDict(extra="forbid")
    change_reason: str | None = Field(default=None, max_length=2000)


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
# The head lifecycle (start-revision / publish) is an org-wide act — gate on register.manage at the
# default SYSTEM scope (D-2b). The QMS Owner holds register.manage @ SYSTEM (the register steward);
# a bound Process-Owner's PROCESS grant does NOT match the org head, so they cannot publish the org
# register unilaterally (D-4). NO path resolver — the head has no process / no {risk_id} path param.
_register_manage_system = require("register.manage")
# The risk→CAPA spawn gate: capa.create at the risk's OWN process scope (the _risk_path_scope
# resolver — the risk's process_id, SYSTEM for an org-level row). Mirrors the complaint→CAPA gate
# (capa.create, not register.manage — the linked_capa_id latch is operational metadata that rides
# on the spawn authority). This is the FAST pre-lock 403; the service re-authorizes under the row
# lock to close a process-reassign TOCTOU. Process Owner holds capa.create @ PROCESS.
_risk_capa_create_path = require("capa.create", async_scope_resolver=_risk_path_scope)


# --- serializer ---
def _risk(row: RiskOpportunity, governing: dict[str, Any] | None) -> dict[str, Any]:
    # S-risk-1b: the displayed BAND grades the live row against the GOVERNING Effective version's
    # FROZEN per-method criteria (resolve_criteria) — never a live module constant — so a code-level
    # band-threshold edit cannot re-grade the live register (R49 L2). ``governing`` is None
    # pre-first-release (the working Draft register) → resolve_criteria falls back to the v1
    # golden-pinned default. The risk_rating itself is already re-derived on every write (S-risk-1).
    band = risk_band(row.risk_rating, resolve_criteria(governing, row.scoring_method))
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
    governing = await governing_register(session, caller.org_id)
    return {"data": [_risk(r, governing) for r in rows]}


# --- the register-head lifecycle (S-risk-1b) -------------------------------------------------
# ⚠ Static routes MUST precede /risks/{risk_id} — FastAPI's {risk_id} convertor would otherwise
# match "/risks/register" (and 422 on the UUID parse). A real UUID never matches "register".
def _register_status(head: DocumentedInformation) -> dict[str, Any]:
    """The register head's lifecycle state (the shared GET + lifecycle-endpoint payload)."""
    return {
        "exists": True,
        "register_doc_id": str(head.id),
        "identifier": head.identifier,
        "state": head.current_state.value,
        "current_effective_version_id": (
            str(head.current_effective_version_id) if head.current_effective_version_id else None
        ),
        "has_governing": head.current_effective_version_id is not None,
    }


_NO_REGISTER: dict[str, Any] = {
    "exists": False,
    "register_doc_id": None,
    "identifier": None,
    "state": None,
    "current_effective_version_id": None,
    "has_governing": False,
}


async def _register_release_scope(
    session: AsyncSession, doc: DocumentedInformation
) -> ResourceContext:
    """Release scope = the head's document scope + the SoD-2 inputs for the version the cutover will
    promote (the latest Approved): its author + approval signers (the ``_objective_release_scope``
    mirror). The head carries no process, so no PROCESS context — release is a SYSTEM-scoped
    document.release act."""
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


@router.get("/risks/register")
async def get_register_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The org's risk register head lifecycle state. Any authenticated org member may read it (the
    register's lifecycle state is org-level, not row-level sensitive; the row contents are gated
    separately by GET /risks). ``exists:false`` before the first risk is added (the head is lazily
    created on the first POST /risks). Carries the server-computed ``can_release``/``can_manage``
    capability booleans (S-context-fe) — the steward console's faithful multi-axis release gate (a
    single-axis FE probe can't replicate ``_register_release_scope``). GET-only; the action routes
    stay lean (the FE refetches this after each mutation)."""
    head = await find_head(session, caller.org_id)
    source_ip = request.client.host if request.client else None
    release_scope = await _register_release_scope(session, head) if head is not None else None
    caps = await register_capabilities(
        session, caller, release_scope=release_scope, source_ip=source_ip
    )
    base = _register_status(head) if head is not None else dict(_NO_REGISTER)
    return {**base, **caps}


@router.post("/risks/register/start-revision")
async def start_register_revision_endpoint(
    caller: AppUser = Depends(_register_manage_system),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """T7 (Effective → UnderRevision) — open the edit window so rows become editable again. Gated
    register.manage @ SYSTEM (D-2b); 409 unless the register is Effective."""
    head = await start_register_revision(session, vault_sink, caller)
    return _register_status(head)


@router.post("/risks/register/publish")
async def publish_register_endpoint(
    body: RegisterPublish | None = None,
    caller: AppUser = Depends(_register_manage_system),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """Freeze the working rows + criteria into a new version and submit it for review (T2/T9). Gated
    register.manage @ SYSTEM (D-2b); 409 unless the head is Draft/UnderRevision. Approval then
    routes through POST /tasks/{id}/decision (DOCUMENT leg); release via /risks/register/release."""
    head = await publish_register(
        session, vault_sink, caller, change_reason=body.change_reason if body else None
    )
    return _register_status(head)


@router.post("/risks/register/release")
async def release_register_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    """T6 (Approved → Effective). Enforces document.release @ SYSTEM over the SoD-2-enriched scope
    (author/approver ≠ releaser — the OBJ release posture; held by no seeded role → a SYSTEM
    override in v1), then runs the shared INV-1 SERIALIZABLE ``release`` cutover (RSK ∉
    LEADERSHIP_DOC_TYPES, so the cutover leadership gate is a no-op). After release: read-only."""
    head = await find_head(session, caller.org_id)
    if head is None:
        raise ProblemException(status=409, code="conflict", title="No risk register to release")
    resource = await _register_release_scope(session, head)
    await enforce(session, authz_sink, request, caller, "document.release", resource, sig_hook=True)
    # release() runs the cutover in its OWN session and returns the doc fully refreshed
    # (``_cutover``'s ``session.refresh`` + ``expire_on_commit=False`` retain every column on the
    # detached instance). Read the status off THAT — re-reading via the request session after
    # ``expire_all`` lazy-loads an attribute in the sync serializer (MissingGreenlet).
    released = await release(caller, head.id, vault_sink, sig_sink)
    return _register_status(released)


# register.read at the DEFAULT SYSTEM scope — the high-risk summary is an org-wide CONTROLLED read
# (the read-of-record for the doc-13 dashboard / Home PLAN tile), gated identically to the S-risk-2
# Management-Review input-(e) consumer: a SYSTEM register.read matches (QMS Owner / Auditor); a
# no-grant caller gets 403. NOT a per-row filter — this is a cross-surface summary, org-wide BY
# DESIGN (the named cross-cutting "should sourced summaries honor per-process denies" deferral stays
# closed here, for consistency with every other sourced MR/dashboard summary).
_register_read_system = require("register.read")


@router.get("/risks/summary")
async def risk_summary_endpoint(
    caller: AppUser = Depends(_register_read_system),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The org's Risk & Opportunity high-risk summary (the doc-13 / Home PLAN seam, S-risk-4a).

    Projects the GOVERNING (current Effective) snapshot via pure ``summarize_register``: the
    CONTROLLED read-of-record, never the live working satellite (an UnderRevision edit is
    invisible until the next publish/release; the MR input-(e) discipline, S-risk-2). ``high_risk``
    is the danger-tone (High and Critical) count. Pre-first-release (no published register →
    ``governing`` is ``None``) returns ``published: false`` + an all-zero summary, so a brand-new
    working register reads honestly as 'no published register yet' rather than a misleading
    '0 high-risk'. Gated register.read @ SYSTEM (org-level)."""
    governing = await governing_register(session, caller.org_id)
    # ``summarize_register`` reads criteria only when a row needs grading, so the empty register
    # never touches it (the all-zero shape is unit-proven — see test_risk_summary.py).
    return {"published": governing is not None, **summarize_register(governing or {"rows": []})}


@router.get("/risks/{risk_id}")
async def get_risk_endpoint(
    risk_id: uuid.UUID,
    caller: AppUser = Depends(_risk_read_scoped),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await get_risk(session, risk_id)
    if row is None or row.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Risk not found")
    governing = await governing_register(session, caller.org_id)
    return _risk(row, governing)


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
    governing = await governing_register(session, caller.org_id)
    return _risk(row, governing)


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
    governing = await governing_register(session, caller.org_id)
    return _risk(row, governing)


@router.post("/risks/{risk_id}/capa")
async def spawn_capa_for_risk_endpoint(
    risk_id: uuid.UUID,
    request: Request,
    caller: AppUser = Depends(_risk_capa_create_path),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> JSONResponse:
    """One-click 'treat this risk' — idempotently spawn a CAPA to treat a risk row (clause 6.1 §7,
    S-risk-3). 201 on the first spawn; 200 on an idempotent replay (the risk already latched a CAPA
    — the SAME CAPA returned). Gated capa.create at the risk's OWN process scope (the
    _risk_capa_create_path resolver gives the fast 403; the service re-authorizes under the row
    lock to close a process-reassign TOCTOU) AND register.read on the locked risk (the caller must
    be able to read the risk they treat — the service enforces it). The CAPA inherits the risk's
    process_id + a severity auto-derived from the band; linked_capa_id is set on the live satellite
    (operational metadata — works at any register head state, no editable gate). Only ``risk`` rows
    can be treated — an ``opportunity`` row is rejected (422). On a replay, capa.read is re-checked
    over the latched CAPA's OWN process (it may differ from the risk's current process after a
    reassign, and the replay returns the CAPA's details). Returns the spawned CAPA (the
    complaint→CAPA response shape)."""
    capa, created = await spawn_capa_for_risk(session, authz_sink, request, caller, risk_id)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=await _capa_full(session, capa),
    )
