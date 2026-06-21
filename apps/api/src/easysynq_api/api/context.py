"""The Context register surface (clause 4.1, S-context-1).

Rides the seeded ``register.read`` / ``register.manage`` keys — NO new key, catalog 102. Clause 4.1
"context of the organization" is ORG-LEVEL (external/internal issues are strategic, org-wide), so —
unlike ``/risks`` — every gate is at the SYSTEM scope: ``GET /context`` is a filter-not-403 list
(calm-empty for a no-grant caller; all-or-nothing since the scope is org-level); ``GET
/context/{id}``
enforces ``register.read`` @ SYSTEM; ``POST``/``PATCH`` enforce ``register.manage`` @ SYSTEM (the
QMS-leadership steward; a bound Process-Owner's PROCESS grant matches no context row). The register
head's controlled-document lifecycle (start-revision/publish/release) rides the shared vault
primitives, identical to ``/risks``. ``status`` defaults to ``active`` on create (closed via
PATCH)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._context_enums import (
    ContextCategory,
    ContextClassification,
    ContextIssueStatus,
)
from ..db.models.app_user import AppUser
from ..db.models.context_issue import ContextIssue
from ..db.models.document_type import DocumentType
from ..db.models.documented_information import DocumentedInformation
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..domain.context.summary import summarize_register
from ..problems import ProblemException
from ..services.authz import (
    AuthzAuditSink,
    enforce,
    gather_grants,
    get_authz_audit_sink,
    require,
)
from ..services.authz.register_caps import register_capabilities
from ..services.context import (
    add_context_issue,
    find_head,
    get_context_issue,
    governing_register,
    list_context_issues,
    publish_register,
    start_context_revision,
    update_context_issue_row,
)
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    get_vault_audit_sink,
    get_vault_signature_sink,
    release,
)
from ..services.vault.release_scope import enrich_release_sod_scope

router = APIRouter(prefix="/api/v1", tags=["context"])


# --- request bodies ---
class ContextCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # reject unknown fields (additionalProperties:false)
    classification: ContextClassification
    description: str = Field(min_length=1, max_length=4000)
    category: ContextCategory | None = None
    last_reviewed_at: datetime.datetime | None = None
    # NB: no ``status`` — a new issue is always ``active`` (close it via PATCH, never create
    # closed).


class ContextUpdate(BaseModel):
    """Partial PATCH. Omitted ≠ null (``model_dump(exclude_unset=True)``): an explicit null clears a
    nullable field (``category``/``last_reviewed_at``), a null on a NOT-NULL field
    (``classification``/``status``/``description``) 422s."""

    model_config = ConfigDict(extra="forbid")  # a typo'd field 422s, never a silent no-op
    classification: ContextClassification | None = None
    category: ContextCategory | None = None
    status: ContextIssueStatus | None = None
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    last_reviewed_at: datetime.datetime | None = None


class RegisterPublish(BaseModel):
    """The optional publish body — an INV-3 change reason for the frozen register version (defaults
    to a system-generated reason when omitted; a no-freeze re-publish ignores it, as in RSK/OBJ)."""

    model_config = ConfigDict(extra="forbid")
    change_reason: str | None = Field(default=None, max_length=2000)


# --- authz deps (all SYSTEM-scoped — clause 4.1 is org-level) ---
_context_read_system = require("register.read")
_context_manage_system = require("register.manage")


# --- serializer ---
def _context_issue(row: ContextIssue) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "register_doc_id": str(row.register_doc_id),
        "classification": row.classification.value,
        "category": row.category.value if row.category else None,
        "status": row.status.value,
        "description": row.description,
        "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
        "row_version": row.row_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# --- endpoints ---
async def _readable_context(
    request: Request, session: AsyncSession, caller: AppUser
) -> list[ContextIssue]:
    """The org's context issues the caller may ``register.read`` (filter-not-403, doc 18 §5.2).
    Clause 4.1 is org-level, so the scope is SYSTEM for every row — the check is all-or-nothing: a
    SYSTEM ``register.read`` grant (QMS Owner / Internal Auditor) returns every row; a no-grant
    caller gets an empty list, never 403. ``source_ip`` is threaded so an ``ip_allow`` predicate
    evaluates as a real enforce would."""
    grants = await gather_grants(session, caller.id, caller.org_id, "register.read")
    ctx = RequestContext(
        now=datetime.datetime.now(datetime.UTC),
        source_ip=request.client.host if request.client else None,
    )
    if not authorize(grants, "register.read", ResourceContext.system(), ctx).allow:
        return []
    return await list_context_issues(session, caller.org_id)


@router.get("/context")
async def list_context_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await _readable_context(request, session, caller)
    return {"data": [_context_issue(r) for r in rows]}


# --- the register-head lifecycle (S-context-1) ----------------------------------------------
# ⚠ Static routes MUST precede /context/{issue_id} — FastAPI's {issue_id} convertor would otherwise
# match "/context/register" (and 422 on the UUID parse). A real UUID never matches "register".
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
    promote (the latest Approved): its author + approval signers (the ``_register_release_scope``
    risk mirror). The head carries no process, so no PROCESS context — release is a SYSTEM-scoped
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


@router.get("/context/register")
async def get_register_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The org's context register head lifecycle state. Any authenticated org member may read it
    (the lifecycle state is org-level, not row-level sensitive; the row contents are gated
    separately
    by GET /context). ``exists:false`` before the first issue is added (the head is lazily created
    on
    the first POST /context). Carries the server-computed ``can_release``/``can_manage`` capability
    booleans (S-context-fe) — the steward console's faithful multi-axis release gate (a single-axis
    FE probe can't replicate ``_register_release_scope``). GET-only; the action routes stay lean
    (the FE refetches this after each mutation)."""
    head = await find_head(session, caller.org_id)
    source_ip = request.client.host if request.client else None
    release_scope = await _register_release_scope(session, head) if head is not None else None
    caps = await register_capabilities(
        session, caller, release_scope=release_scope, source_ip=source_ip
    )
    base = _register_status(head) if head is not None else dict(_NO_REGISTER)
    return {**base, **caps}


@router.post("/context/register/start-revision")
async def start_register_revision_endpoint(
    caller: AppUser = Depends(_context_manage_system),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """T7 (Effective → UnderRevision) — open the edit window so rows become editable again. Gated
    register.manage @ SYSTEM; 409 unless the register is Effective."""
    head = await start_context_revision(session, vault_sink, caller)
    return _register_status(head)


@router.post("/context/register/publish")
async def publish_register_endpoint(
    body: RegisterPublish | None = None,
    caller: AppUser = Depends(_context_manage_system),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """Freeze the working rows into a new version and submit it for review (T2/T9). Gated
    register.manage @ SYSTEM; 409 unless the head is Draft/UnderRevision. Approval then routes
    through
    POST /tasks/{id}/decision (DOCUMENT leg); release via /context/register/release."""
    head = await publish_register(
        session, vault_sink, caller, change_reason=body.change_reason if body else None
    )
    return _register_status(head)


@router.post("/context/register/release")
async def release_register_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    """T6 (Approved → Effective). Enforces document.release over the SoD-2-enriched scope
    (author/approver ≠ releaser — the RSK release posture; held by no seeded role → a SYSTEM
    override
    in v1), then runs the shared INV-1 SERIALIZABLE ``release`` cutover (CTX ∉ LEADERSHIP_DOC_TYPES,
    so the cutover leadership gate is a no-op). After release: read-only."""
    head = await find_head(session, caller.org_id)
    if head is None:
        raise ProblemException(status=409, code="conflict", title="No context register to release")
    resource = await _register_release_scope(session, head)
    await enforce(session, authz_sink, request, caller, "document.release", resource, sig_hook=True)
    # release() runs the cutover in its OWN session and returns the doc fully refreshed — read the
    # status off THAT (re-reading via the request session after expire_all lazy-loads in the sync
    # serializer → MissingGreenlet; the risk release precedent).
    released = await release(caller, head.id, vault_sink, sig_sink)
    return _register_status(released)


# register.read @ SYSTEM — the context summary is an org-wide CONTROLLED read (the read-of-record
# for the doc-13 / Home dashboard tile + the Context SPA, S-context-2/-fe), gated like the
# single-row GET /context/{id} enforce. Clause 4.1 is fully org-level (no per-row/per-process
# scoping), so this is a 403-on-deny enforce, NOT a per-row filter — the cross-cutting "should
# sourced summaries honor per-process denies" deferral does not apply to org-level context.
@router.get("/context/summary")
async def context_summary_endpoint(
    caller: AppUser = Depends(_context_read_system),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The org's Context register summary (the Home/dashboard + Context-SPA seam, S-context-2).

    Projects the GOVERNING (current Effective) snapshot via pure ``summarize_register``: the
    CONTROLLED read-of-record, never the live working satellite (an UnderRevision edit is invisible
    until the next publish/release; the MR read-of-record discipline). ``active`` is the open-issues
    headline; ``never_reviewed`` counts rows with no ``last_reviewed_at``. Pre-first-release (no
    published register → ``governing`` is ``None``) returns ``published: false`` + an all-zero
    summary, so a brand-new working register reads honestly as 'no published register yet' rather
    than a misleading set of zeros. Gated register.read @ SYSTEM (org-level)."""
    governing = await governing_register(session, caller.org_id)
    return {"published": governing is not None, **summarize_register(governing or {"rows": []})}


@router.get("/context/{issue_id}")
async def get_context_issue_endpoint(
    issue_id: uuid.UUID,
    caller: AppUser = Depends(_context_read_system),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await get_context_issue(session, issue_id)
    if row is None or row.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Context issue not found")
    return _context_issue(row)


@router.post("/context", status_code=status.HTTP_201_CREATED)
async def create_context_issue_endpoint(
    body: ContextCreate,
    caller: AppUser = Depends(_context_manage_system),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """Author a context issue (register.manage @ SYSTEM). The CTX head is lazily created on the
    first
    issue. A new issue is always ``active``."""
    row = await add_context_issue(
        session,
        vault_sink,
        caller,
        classification=body.classification,
        description=body.description,
        category=body.category,
        last_reviewed_at=body.last_reviewed_at,
    )
    return _context_issue(row)


@router.patch("/context/{issue_id}")
async def update_context_issue_endpoint(
    issue_id: uuid.UUID,
    body: ContextUpdate,
    caller: AppUser = Depends(_context_manage_system),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Partial PATCH (register.manage @ SYSTEM). An explicit null clears ``category``/
    ``last_reviewed_at``; a null on classification/status/description 422s. Editable only while the
    register head is Draft/UnderRevision."""
    updates = body.model_dump(exclude_unset=True)
    row = await update_context_issue_row(session, caller, issue_id, updates=updates)
    return _context_issue(row)
