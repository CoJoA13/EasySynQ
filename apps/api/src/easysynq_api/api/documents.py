"""The controlled-vault document surface (slice S3): create/list/get/patch metadata, the
check-out → presigned upload → immutable check-in cycle, break-lock, and version reads.

All routes are PEP-gated (doc 07 ``document.*`` keys). Per-document routes resolve their
ARTIFACT/FOLDER/DOC_CLASS scope from the document (``_document_scope``); create resolves scope
from the body in-handler; the list is row-filtered to what the caller may ``document.read``
(doc 15 §9.3). Lifecycle transitions (submit-review/release/…) are S4.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._ack_enums import DistributionTargetType
from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models._dcr_enums import VisualDiffStatus
from ..db.models._vault_enums import (
    Classification,
    DocumentCurrentState,
    DocumentKind,
    DocumentLinkType,
)
from ..db.models._workflow_enums import WorkflowSubjectType
from ..db.models.app_user import AppUser
from ..db.models.audit_event import AuditEvent
from ..db.models.clause import Clause
from ..db.models.clause_mapping import ClauseMapping
from ..db.models.distribution_entry import DistributionEntry
from ..db.models.document_link import DocumentLink
from ..db.models.document_type import DocumentType
from ..db.models.document_version import DocumentVersion
from ..db.models.documented_information import DocumentedInformation
from ..db.models.management_review import ManagementReview
from ..db.models.process import Process
from ..db.models.process_link import ProcessLink
from ..db.models.quality_objective import QualityObjective
from ..db.models.role import Role
from ..db.models.visual_diff import VisualDiff
from ..db.models.workflow import Task, WorkflowInstance
from ..db.models.working_draft import WorkingDraft
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..logging import request_id_var
from ..problems import ProblemException
from ..services.ack import queries as ack_queries
from ..services.ack.sink import get_ack_enqueue_sink
from ..services.authz import AuthzAuditSink, enforce, gather_grants, get_authz_audit_sink, require
from ..services.authz.repository import gather_sod_constraints, get_allow_approver_release
from ..services.dcr import build_where_used
from ..services.diff import build_version_diff, get_or_create_visual_diff, get_visual_diff
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    audit_transition,
    break_lock,
    checkin,
    checkin_form_schema,
    checkout,
    create_document,
    get_effective_schema,
    get_vault_audit_sink,
    get_vault_signature_sink,
    get_working_schema,
    heartbeat,
    init_upload,
    obsolete,
    reject_objective_byte_path,
    release,
    render_dynamic_copy,
    set_working_schema,
    start_revision,
    storage,
    submit_review,
)
from ..services.vault import repository as vault_repo
from ..services.vault.leadership_authorization import (
    release_authorization_status,
    request_leadership_authorization,
)
from ..services.vault.locks import LOCK_TTL_SECONDS
from ..services.vault.release_scope import enrich_release_sod_scope
from ..services.vault.review import compute_next_review_due, review_state, today_org
from ..services.workflow import instantiate_approval
from ..services.workflow import repository as wf_repo
from ..tasks.visual_diff import visual_diff as visual_diff_task

router = APIRouter(prefix="/api/v1", tags=["documents"])


# --- request bodies ---------------------------------------------------------------------


class DocumentCreate(BaseModel):
    title: str
    document_type_id: uuid.UUID
    area_code: str | None = None
    folder_path: str | None = None
    classification: str = "Internal"


class MetadataUpdate(BaseModel):
    title: str | None = None
    folder_path: str | None = None
    classification: str | None = None
    review_period_months: int | None = Field(default=None, ge=1, le=120)


class DistributionEntryCreate(BaseModel):
    target_type: str
    target_id: uuid.UUID
    ack_required: bool = True


class DistributionUpdate(BaseModel):
    acknowledgement_required: bool | None = None
    add_entries: list[DistributionEntryCreate] = Field(default_factory=list)


class InitUpload(BaseModel):
    sha256: str
    content_type: str = "application/octet-stream"


class CheckIn(BaseModel):
    sha256: str
    change_reason: str = ""
    change_significance: str = ""
    mime_type: str = "application/octet-stream"


class Obsolete(BaseModel):
    reason: str
    version_id: uuid.UUID | None = None
    # doc 05 §7.3 (S-dcr-5): override the obsoletion-safety block (coverage gap) with a recorded
    # justification. Both the direct endpoint and the DCR RETIRE-implement gate on the same check.
    force_retire: bool = False
    override_justification: str | None = None


class Release(BaseModel):
    version_id: uuid.UUID | None = None


class FormSchemaUpdate(BaseModel):
    field_schema: dict[str, Any]


class FormSchemaCheckin(BaseModel):
    change_reason: str = ""
    change_significance: str = ""


# --- representations --------------------------------------------------------------------


def _document(
    d: DocumentedInformation,
    *,
    clause_refs: list[str] | None = None,
    effective_from: str | None = None,
    capabilities: dict[str, bool] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(d.id),
        "identifier": d.identifier,
        "kind": d.kind.value,
        "title": d.title,
        "document_type_id": str(d.document_type_id) if d.document_type_id else None,
        "area_code": d.area_code,
        "folder_path": d.folder_path,
        "current_state": d.current_state.value,
        "classification": d.classification.value,
        "is_singleton": d.is_singleton,
        "owner_user_id": str(d.owner_user_id),
        "framework_id": str(d.framework_id),
        "current_effective_version_id": (
            str(d.current_effective_version_id) if d.current_effective_version_id else None
        ),
        # S-web-2: the governing effective version's effective_from (the library "Effective" column
        # + the artifact-header date). null when no effective version (Draft-only) — computed on the
        # read paths (list/detail) via _effective_from_map; null on create/patch responses.
        "effective_from": effective_from,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "review_period_months": d.review_period_months,
        "next_review_due": d.next_review_due.isoformat() if d.next_review_due else None,
        "last_reviewed_at": d.last_reviewed_at.isoformat() if d.last_reviewed_at else None,
        "review_state": review_state(d.next_review_due, today_org()),
    }
    # clause_refs (S10, doc 15 §2.1): the mapped clause numbers, derived from clause_mapping (never
    # a denormalized column — D2). Omitted unless the handler supplies it (batch-loaded per page).
    if clause_refs is not None:
        out["clause_refs"] = clause_refs
    # S-web-3 (DP-6): the caller's per-document authoring affordances (detail responses only).
    if capabilities is not None:
        out["capabilities"] = capabilities
    return out


async def _effective_from_map(
    session: AsyncSession, docs: list[DocumentedInformation]
) -> dict[uuid.UUID, str | None]:
    """Map each doc → its current effective version's effective_from ISO (S-web-2; batched)."""
    ver_ids = {d.current_effective_version_id for d in docs if d.current_effective_version_id}
    if not ver_ids:
        return {}
    rows = (
        await session.execute(
            select(DocumentVersion.id, DocumentVersion.effective_from).where(
                DocumentVersion.id.in_(ver_ids)
            )
        )
    ).all()
    by_ver = {vid: (ef.isoformat() if ef else None) for vid, ef in rows}
    return {
        d.id: by_ver.get(d.current_effective_version_id)
        for d in docs
        if d.current_effective_version_id
    }


def _version(v: DocumentVersion) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "document_id": str(v.document_id),
        "version_seq": v.version_seq,
        "revision_label": v.revision_label,
        "version_state": v.version_state.value,
        "change_significance": v.change_significance.value,
        "change_reason": v.change_reason,
        "source_blob_sha256": v.source_blob_sha256,
        "metadata_snapshot": v.metadata_snapshot,
        "author_user_id": str(v.author_user_id),
        "effective_from": v.effective_from.isoformat() if v.effective_from else None,
        "effective_to": v.effective_to.isoformat() if v.effective_to else None,
        "superseded_by_version_id": (
            str(v.superseded_by_version_id) if v.superseded_by_version_id else None
        ),
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _working_draft(wd: WorkingDraft) -> dict[str, Any]:
    return {
        "id": str(wd.id),
        "document_id": str(wd.document_id),
        "checked_out_by": str(wd.checked_out_by),
        "checked_out_at": wd.checked_out_at.isoformat() if wd.checked_out_at else None,
        "source_version_id": str(wd.source_version_id) if wd.source_version_id else None,
        "lock_ttl_seconds": LOCK_TTL_SECONDS,
    }


class ClauseMappingCreate(BaseModel):
    clause_id: uuid.UUID
    is_requirement_level: bool = False


def _clause_mapping(m: ClauseMapping, c: Clause) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "document_id": str(m.documented_information_id),
        "clause_id": str(m.clause_id),
        "clause_number": c.number,
        "clause_title": c.title,
        "is_requirement_level": m.is_requirement_level,
        "framework_id": str(m.framework_id),
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


class ProcessLinkCreate(BaseModel):
    process_id: uuid.UUID


def _process_link(link: ProcessLink, p: Process) -> dict[str, Any]:
    return {
        "id": str(link.id),
        "document_id": str(link.documented_information_id),
        "process_id": str(link.process_id),
        "process_name": p.name,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _emit_clause_event(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    doc: DocumentedInformation,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a clause-mapping ``audit_event`` (object_type=document, keyed to the mapped artifact)
    BEFORE commit, so the link change + its audit row commit atomically (mirrors
    ``users._emit_user_event``). Hashes stay NULL — the S6 linker stamps them off the hot path.
    ``scope_ref=doc.identifier`` is required so these events surface on the per-document trail
    (``GET /documents/{id}/audit-events`` filters on ``scope_ref == doc.identifier`` — mirrors
    ``_emit_distribution_event``). Historical rows stay NULL — no backfill (append-only audit)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.document,
            object_id=doc.id,
            scope_ref=doc.identifier,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


# The document↔process link audit reuses object_type=document (the link is *about the document*, the
# clause_mapping precedent) — so _emit_clause_event covers PROCESS_LINKED/PROCESS_UNLINKED too.
_emit_process_link_event = _emit_clause_event


# --- helpers ----------------------------------------------------------------------------


async def _document_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Resolve a document's authz scope (ARTIFACT + folder + doc-class) from the path id."""
    raw = request.path_params.get("document_id")
    if not raw:
        return ResourceContext.system()
    try:
        doc_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    return await _document_scope_by_id(session, doc_id)


async def _document_scope_by_id(session: AsyncSession, doc_id: uuid.UUID) -> ResourceContext:
    doc = await session.get(DocumentedInformation, doc_id)
    if doc is None:
        return ResourceContext(artifact_id=str(doc_id))
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    return ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        lifecycle_state=doc.current_state.value,
    )


async def _document_scope_with_processes(
    request: Request, session: AsyncSession
) -> ResourceContext:
    """``_document_scope`` + the doc's ProcessLink ids — R28: a PROCESS-scoped grant can only
    match when ``resource.process_ids`` is populated (the S-ack-1 decide-leg precedent). Used by
    the distribution/acknowledgement gates; the repo-wide ``_document_scope`` migration is the
    owner-assignment track's call."""
    base = await _document_scope(request, session)
    if base.artifact_id is None:
        return base
    process_ids = (
        await session.execute(
            select(ProcessLink.process_id).where(
                ProcessLink.documented_information_id == uuid.UUID(base.artifact_id)
            )
        )
    ).scalars()
    return dataclasses.replace(base, process_ids=frozenset(str(p) for p in process_ids))


async def _release_scope(
    session: AsyncSession, doc_id: uuid.UUID, version_id: uuid.UUID | None
) -> ResourceContext:
    """Release scope = the base document scope PLUS the SoD-2 inputs for the **exact version the
    cutover will promote** (``version_id`` if supplied, else the latest Approved) — its immutable
    author + prior approval signers. SoD-2 blocks the author from releasing their own edit and
    (unless ``allow_approver_release``) the sole approver. Resolving the SAME version the handler
    passes to the cutover keeps the guard sound even if multiple Approved versions ever coexist.
    Degrades to the base scope when there is no Approved version (the FSM 409 fires)."""
    base = await _document_scope_by_id(session, doc_id)
    # The SoD-2 enrichment (author + approver signers for the promoted version) is shared with the
    # DCR-as-orchestrator implement path so both fire the same overlay (S-dcr-5).
    return await enrich_release_sod_scope(session, base, doc_id, version_id)


# S-web-3 (DP-6): the caller's per-document authoring affordances — the AUTHZ answer only (the SPA
# combines it with lifecycle state + lock state for the final affordance, e.g. "Check out" vs
# "Locked by X"). Detail-only (GET /documents/{id}); NEVER per-row on the list (O(rows*keys) grant
# queries). Mirrors the PEP's evaluate() but calls the pure PDP directly, so a capability probe
# writes no authz-audit row (the effective-permissions precedent). ``document.create`` is absent —
# it is DOC_CLASS-scoped/coarse and answered by GET /me/permissions, not per-document.
_CAPABILITY_KEYS: dict[str, str] = {
    "checkout": "document.checkout",
    "edit": "document.edit",  # check-in + start-revision
    "manage_metadata": "document.manage_metadata",  # clause mapping
    "submit": "document.submit",
    "read_draft": "document.read_draft",  # history / diff / working-copy download
}


async def _document_capabilities(
    session: AsyncSession, caller: AppUser, doc: DocumentedInformation
) -> dict[str, bool]:
    base = await _document_scope_by_id(session, doc.id)
    now = datetime.datetime.now(datetime.UTC)
    ctx = RequestContext(now=now, actor_user_id=str(caller.id))
    caps: dict[str, bool] = {}
    for short_key, perm_key in _CAPABILITY_KEYS.items():
        grants = await gather_grants(session, caller.id, caller.org_id, perm_key)
        caps[short_key] = authorize(grants, perm_key, base, ctx).allow
    # obsolete: a sig-hook action (no SoD overlay). The §7.3 coverage gate is a separate runtime
    # check, not authz — the capability just says "you hold document.obsolete on this doc".
    obs_grants = await gather_grants(session, caller.id, caller.org_id, "document.obsolete")
    caps["obsolete"] = authorize(obs_grants, "document.obsolete", base, ctx, sig_hook=True).allow
    # release: sig-hook + the SoD-2 overlay over the version the cutover would promote (the latest
    # Approved), so the author-can't-release block is reflected. No Approved version → enrich
    # degrades to the base scope (the FSM 409 is what blocks release at action time).
    release_scope = await enrich_release_sod_scope(session, base, doc.id, None)
    sod = await gather_sod_constraints(session, caller.org_id)
    allow_approver_release = await get_allow_approver_release(session, caller.org_id)
    rel_ctx = RequestContext(
        now=now, actor_user_id=str(caller.id), allow_approver_release=allow_approver_release
    )
    rel_grants = await gather_grants(session, caller.id, caller.org_id, "document.release")
    caps["release"] = authorize(
        rel_grants, "document.release", release_scope, rel_ctx, sig_hook=True, sod=sod
    ).allow
    return caps


async def _can_request_leadership_authorization(
    session: AsyncSession, caller: AppUser, doc: DocumentedInformation, *, source_ip: str | None
) -> bool:
    """CX-1: the pure authz probe behind the FE "Request Top-Management authorization" button —
    does the caller hold ``document.approve`` at this document's scope, the EXACT gate the request
    endpoint enforces (``_approve`` = ``require("document.approve")`` over ``_document_scope``: NO
    sig_hook, NO process_ids; the SoD overlay is inert here — the scope carries no author/approver —
    so a plain ``authorize`` matches the endpoint's effective result). ``source_ip`` is threaded the
    way the PEP's ``evaluate`` builds it (``request.client.host``) so an ``ip_allow``-narrowed grant
    evaluates IDENTICALLY to the POST gate (Codex P2: omitting it made the probe over-strict — the
    PDP rejects an ``ip_allow`` predicate when ``source_ip`` is None, while the POST set it from the
    request → a hidden button the server would actually allow). The AUTHZ answer ONLY — the FE ANDs
    it with the runtime is_leadership / required / Approved / in-flight state already in the status
    payload (the obsolete-capability-vs-§7.3-runtime-gate split). A capability probe writes no
    authz-audit row, so it uses the pure PDP (gather_grants + authorize), never enforce."""
    base = await _document_scope_by_id(session, doc.id)
    ctx = RequestContext(
        now=datetime.datetime.now(datetime.UTC),
        source_ip=source_ip,
        actor_user_id=str(caller.id),
    )
    grants = await gather_grants(session, caller.id, caller.org_id, "document.approve")
    return authorize(grants, "document.approve", base, ctx).allow


async def _load_document(
    session: AsyncSession, caller: AppUser, raw_id: uuid.UUID, *, for_update: bool = False
) -> DocumentedInformation:
    if for_update:
        doc = (
            await session.execute(
                select(DocumentedInformation)
                .where(DocumentedInformation.id == raw_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    else:
        doc = await vault_repo.get_document(session, raw_id)
    # kind-scoping (S-rec-1): a Record shares the documented_information PK + is Effective, so a
    # record id would otherwise resolve here and let a document sub-resource operate on a record.
    if doc is None or doc.org_id != caller.org_id or doc.kind != DocumentKind.DOCUMENT:
        raise ProblemException(status=404, code="not_found", title="Document not found")
    return doc


_read = require("document.read", async_scope_resolver=_document_scope)
_read_draft = require("document.read_draft", async_scope_resolver=_document_scope)
_checkout = require("document.checkout", async_scope_resolver=_document_scope)
_edit = require("document.edit", async_scope_resolver=_document_scope)
_manage_metadata = require("document.manage_metadata", async_scope_resolver=_document_scope)
# S-ack-1 (R42): distribution-list management + the named coverage matrix (doc 04 §8.1/§8.2).
# The scope carries process_ids so a PROCESS-scoped grant (legal via the R35 content-tier
# permission.grant) can match (Codex P2).
_distribute = require("document.distribute", async_scope_resolver=_document_scope_with_processes)
# Lifecycle actions. submit-review (S4) instantiates the approval workflow; approve/request-changes
# route through POST /tasks/{id}/decision now (S5, removed from here). release is a flat sig-hook
# action but enforces imperatively in-handler (its SoD-2 scope needs the body's version_id, which a
# path-only dependency cannot see). obsolete is a flat sig-hook action; start-revision reuses
# ``document.edit`` (no ``document.revise`` key exists).
_submit = require("document.submit", async_scope_resolver=_document_scope)
# S-leadership-1: gate the "request Top-Management release authorization" act. Only the REQUEST
# reuses document.approve at the doc's scope; the SIGN is keyless candidate-pool authority (no SoD
# enrichment here — _document_scope carries no author/approver, so this is a plain "holds approve"
# gate, not the approval SoD overlay).
_approve = require("document.approve", async_scope_resolver=_document_scope)
_obsolete = require("document.obsolete", async_scope_resolver=_document_scope, sig_hook=True)
# S7d export/print. The cheap cached controlled-copy presign stays on document.read (/download). The
# per-request UNCONTROLLED-when-printed export is gated on the SoD-sensitive document.export; the
# in-app controlled print on document.print_controlled (doc 07 §3.1 keys, both already seeded).
_export = require("document.export", async_scope_resolver=_document_scope)
_print = require("document.print_controlled", async_scope_resolver=_document_scope)


# --- documents --------------------------------------------------------------------------


# --- GET /documents list filtering (S10, doc 15 §3.2 bracketed grammar) -------------------
# Only these (field, op) pairs are accepted; anything else matching filter[…][…] → 400
# unknown_filter (doc 15 §3.2). The list still ROW-FILTERS by document.read in Python (§9.3) — these
# SQL filters just narrow the candidate set first; pagination (limit/offset) slices the POST-authz
# set, with ``_LIST_SCAN_CAP`` the pre-authz candidate cap (S-web-2).
_FILTER_KEY_RE = re.compile(r"^filter\[([^\]]+)\]\[([^\]]+)\]$")
_FILTER_ALLOW: frozenset[tuple[str, str]] = frozenset(
    {
        ("clause_refs", "has"),
        ("current_state", "eq"),
        ("document_type", "eq"),
        ("owner_user_id", "eq"),
        ("classification", "eq"),
        # S-doc-filters: the CREATE-implement picker narrows server-side (default-off).
        ("has_effective_version", "eq"),
        ("managed_subtype", "eq"),
        # S-web-2: the library "Effective date" facet — bounds on the CURRENT effective version's
        # effective_from (via the current_effective_version join). The client maps relative buckets
        # (Last 30 days / This quarter / …) to a gte ISO timestamp.
        ("effective_from", "gte"),
        ("effective_from", "lte"),
    }
)

# S-web-2: scan up to this many candidates pre-authz, authz-filter in Python, then slice the page.
# The document.read scope filter is per-row (folder_path/document_level), not SQL-expressible, so a
# naive SQL OFFSET would page the wrong pre-authz set. Fine for v1 (hundreds of docs); a bigger
# install must push the scope filter into SQL (R34 simple-correct-first posture). has_more is exact
# up to this cap.
_LIST_SCAN_CAP = 2000


def _ilike_escape(term: str) -> str:
    """Escape ILIKE wildcards so a user-supplied term matches literally (mirrors the search
    indexer's escaping, services/search/indexer.py). Backslash first (the escape char itself), then
    ``%``/``_``; the caller wraps the result as ``%escaped%`` for a case-insensitive substring."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_filter_bool(field: str, value: str) -> bool:
    """Parse a boolean filter value ("true"/"false"); anything else → 422."""
    if value == "true":
        return True
    if value == "false":
        return False
    raise ProblemException(
        status=422, code="validation_error", title=f"Invalid {field} filter value"
    )


def _filter_condition(field: str, op: str, value: str) -> ColumnElement[bool]:
    """Build one SQL WHERE condition for an allow-listed (field, op); bad value → 422."""
    # filter[has_effective_version][eq]=true|false — narrow to (never-)released docs (S-doc-filters,
    # the CREATE-implement picker). false → never released; true → has an effective version.
    if field == "has_effective_version":
        flag = _parse_filter_bool(field, value)
        col = DocumentedInformation.current_effective_version_id
        return col.is_not(None) if flag else col.is_(None)
    # filter[managed_subtype][eq]=true|false — include/exclude OBJ/MR shared-PK subtypes via NOT
    # EXISTS (immune to document-type-code renames; S-doc-filters F2). false → exclude managed.
    if field == "managed_subtype":
        flag = _parse_filter_bool(field, value)
        is_managed = or_(
            select(1).where(QualityObjective.id == DocumentedInformation.id).exists(),
            select(1).where(ManagementReview.id == DocumentedInformation.id).exists(),
        )
        return is_managed if flag else ~is_managed
    # filter[effective_from][gte|lte]=<ISO> — bound on the current effective version
    if field == "effective_from":
        try:
            ts = datetime.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title="Invalid effective_from filter value"
            ) from exc
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.UTC)
        bound = (
            DocumentVersion.effective_from >= ts
            if op == "gte"
            else DocumentVersion.effective_from <= ts
        )
        return (
            select(1)
            .select_from(DocumentVersion)
            .where(
                DocumentVersion.id == DocumentedInformation.current_effective_version_id,
                DocumentVersion.effective_from.is_not(None),
                bound,
            )
            .exists()
        )
    if field == "clause_refs":  # filter[clause_refs][has]=8.4 — exact clause-number membership
        # Constrain to the document's OWN framework (clause.number is unique only per framework —
        # uq_clause_framework_id_number): multi-standard safety (D3), matching the clause-map write
        # guard + the checklist query. Today the map guard already keeps a doc's mappings
        # framework-consistent, so this is defense-in-depth against a future second seeded standard.
        return (
            select(1)
            .select_from(ClauseMapping)
            .join(Clause, ClauseMapping.clause_id == Clause.id)
            .where(
                ClauseMapping.documented_information_id == DocumentedInformation.id,
                Clause.framework_id == DocumentedInformation.framework_id,
                Clause.number == value,
            )
            .exists()
        )
    if field == "current_state":
        try:
            return DocumentedInformation.current_state == DocumentCurrentState(value)
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title="Invalid current_state filter value"
            ) from exc
    if field == "classification":
        try:
            return DocumentedInformation.classification == Classification(value)
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title="Invalid classification filter value"
            ) from exc
    # document_type / owner_user_id — UUID-valued
    column = (
        DocumentedInformation.document_type_id
        if field == "document_type"
        else DocumentedInformation.owner_user_id
    )
    try:
        return column == uuid.UUID(value)
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title=f"Invalid {field} filter value"
        ) from exc


def _parse_document_filters(request: Request) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    for raw_key, value in request.query_params.multi_items():
        match = _FILTER_KEY_RE.match(raw_key)
        if match is None:
            continue  # not a filter[…][…] param (e.g. limit) — ignored
        field, op = match.group(1), match.group(2)
        if (field, op) not in _FILTER_ALLOW:
            raise ProblemException(
                status=400, code="unknown_filter", title=f"Unknown filter: {raw_key}"
            )
        conditions.append(_filter_condition(field, op, value))
    return conditions


@router.get("/documents")
async def list_documents(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
    offset: int = 0,
    q: str | None = Query(
        None,
        description="Free-text, case-insensitive SUBSTRING match over identifier/title "
        "(a typeahead narrowing; the document.read row-filter remains the security boundary). "
        "Trimmed; blank is ignored.",
    ),
) -> dict[str, Any]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    filters = _parse_document_filters(request)
    # q is a top-level free-text param (a sibling of limit/offset), NOT part of the filter[…][…]
    # eq/has/gte/lte grammar (so _FILTER_ALLOW is untouched). A blank/whitespace q adds no condition
    # → the endpoint stays byte-identical for callers that never send it (Library, etc.).
    search = (q or "").strip()
    if search:
        pat = f"%{_ilike_escape(search)}%"
        filters.append(
            or_(
                DocumentedInformation.identifier.ilike(pat, escape="\\"),
                DocumentedInformation.title.ilike(pat, escape="\\"),
            )
        )
    grants = await gather_grants(session, caller.id, caller.org_id, "document.read")
    docs = (
        (
            await session.execute(
                select(DocumentedInformation)
                # kind-scoping (S-rec-1): exclude Records (kind=RECORD) — they share the table + are
                # Effective, so without this they would leak into the documents list.
                .where(
                    DocumentedInformation.org_id == caller.org_id,
                    DocumentedInformation.kind == DocumentKind.DOCUMENT,
                    *filters,
                )
                .order_by(desc(DocumentedInformation.created_at))
                # S-web-2: scan a bounded candidate window, then authz-filter + slice in Python.
                .limit(_LIST_SCAN_CAP)
            )
        )
        .scalars()
        .all()
    )
    type_ids = {d.document_type_id for d in docs if d.document_type_id}
    levels: dict[uuid.UUID, str] = {}
    if type_ids:
        for dt in (
            (await session.execute(select(DocumentType).where(DocumentType.id.in_(type_ids))))
            .scalars()
            .all()
        ):
            levels[dt.id] = dt.document_level.value
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC))
    visible: list[DocumentedInformation] = []
    for d in docs:
        resource = ResourceContext(
            artifact_id=str(d.id),
            folder_path=d.folder_path,
            document_level=levels.get(d.document_type_id) if d.document_type_id else None,
        )
        if authorize(grants, "document.read", resource, ctx).allow:
            visible.append(d)
    # S-web-2: pagination is applied AFTER the per-row authz filter so page boundaries are correct
    # for scoped users (no exact total — has_more is derived, no COUNT(*)).
    page_rows = visible[offset : offset + limit]
    # clause_refs (S10) + effective_from (S-web-2): batch over the visible page only (no N+1).
    refs = await vault_repo.clause_numbers_for_docs(session, [d.id for d in page_rows])
    eff = await _effective_from_map(session, page_rows)
    return {
        "data": [
            _document(d, clause_refs=refs.get(d.id, []), effective_from=eff.get(d.id))
            for d in page_rows
        ],
        "page": {
            "limit": limit,
            "offset": offset,
            "returned": len(page_rows),
            "has_more": len(visible) > offset + len(page_rows),
        },
    }


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def create_document_endpoint(
    body: DocumentCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    dt = await session.get(DocumentType, body.document_type_id)
    level = dt.document_level.value if dt else None
    resource = ResourceContext(folder_path=body.folder_path, document_level=level)
    await enforce(session, authz_sink, request, caller, "document.create", resource)
    doc = await create_document(
        session,
        vault_sink,
        caller,
        title=body.title,
        document_type_id=body.document_type_id,
        area_code=body.area_code,
        folder_path=body.folder_path,
        classification=body.classification,
    )
    return _document(doc)


@router.get("/documents/{document_id}")
async def get_document_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    rows = await vault_repo.list_clause_mappings(session, doc.id)
    eff = await _effective_from_map(session, [doc])
    caps = await _document_capabilities(session, caller, doc)
    return _document(
        doc,
        clause_refs=[c.number for _, c in rows],
        effective_from=eff.get(doc.id),
        capabilities=caps,
    )


@router.patch("/documents/{document_id}")
async def update_metadata_endpoint(
    document_id: uuid.UUID,
    body: MetadataUpdate,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from ..db.models._vault_enums import Classification

    # Load once, lock-aware up front: if review_period_months is being set we need a FOR UPDATE
    # lock so the recompute of next_review_due can't race a concurrent cutover that commits a new
    # current_effective_version_id between our read and our write.
    # ⚠ The authz dependency (_manage_metadata → _document_scope → _document_scope_by_id) calls
    # session.get(DocumentedInformation, doc_id) BEFORE this handler runs, so the row is already
    # in the session's identity map. Without populate_existing=True the locked SELECT acquires the
    # DB lock but returns the pre-lock attribute snapshot — a concurrent commit (e.g. a cutover or
    # review-confirm) that landed while we waited for the lock stays invisible and the recompute
    # clobbers the fresh next_review_due. populate_existing forces SQLAlchemy to overwrite the
    # identity-map entry with the locked row's current column values. This applies to ALL
    # for_update=True callers of _load_document (unmap_clause, submit_review, this handler).
    wants_review_update = "review_period_months" in body.model_fields_set
    doc = await _load_document(session, caller, document_id, for_update=wants_review_update)
    if body.title is not None:
        doc.title = body.title
    if body.folder_path is not None:
        doc.folder_path = body.folder_path
    if body.classification is not None:
        try:
            doc.classification = Classification(body.classification)
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title="Invalid classification"
            ) from exc
    if wants_review_update:
        doc.review_period_months = body.review_period_months
        eff_from = None
        if doc.current_effective_version_id is not None:
            ver = await session.get(DocumentVersion, doc.current_effective_version_id)
            eff_from = ver.effective_from if ver is not None else None
        doc.next_review_due = compute_next_review_due(
            doc.review_period_months, doc.last_reviewed_at, eff_from
        )
    doc.updated_by = caller.id
    await session.commit()
    await session.refresh(doc)
    return _document(doc)


# --- form-template schema (S-rec-3, doc 06 §4.2): the Mode-B structured-form authoring surface ----
# A Form/Template (document_type FRM) carries an editable working ``field_schema`` (the bespoke
# field-list DSL). ``form-schema:checkin`` freezes it into an immutable version (its content IS the
# schema); the standard submit-review → approve → release then drives it Effective. Mode-B capture
# (POST /records) validates form_field_values against the schema pinned in the Effective version.


@router.put("/documents/{document_id}/form-schema")
async def set_form_schema_endpoint(
    document_id: uuid.UUID,
    body: FormSchemaUpdate,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """Set/replace a Form/Template's editable working field schema (Draft/UnderRevision only; the
    service hard-blocks non-FRM / non-editable in-handler — the SYSTEM override carries no lifecycle
    predicate). Validates the bespoke DSL; 422 on a malformed schema. Needs
    ``document.manage_metadata``."""
    doc = await _load_document(session, caller, document_id)
    ft = await set_working_schema(session, vault_sink, caller, doc, body.field_schema)
    return {"document_id": str(doc.id), "field_schema": ft.field_schema}


@router.get("/documents/{document_id}/form-schema")
async def get_form_schema_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The current editable working field schema (null if none authored yet). Needs
    ``document.read``."""
    doc = await _load_document(session, caller, document_id)
    return {"document_id": str(doc.id), "field_schema": await get_working_schema(session, doc)}


@router.get("/documents/{document_id}/effective-form-schema")
async def get_effective_form_schema_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The schema pinned in the template's in-force version — the form to render (doc 06 §4.2 GET
    /templates/{id}/effective-version). Read from the VERSION snapshot (never the mutable working
    copy), honoring the org's pre-release-capture toggle. 422 if no resolvable version. Needs
    ``document.read``."""
    doc = await _load_document(session, caller, document_id)
    allow_pre = await vault_repo.capture_pre_release_enabled(session, caller.org_id)
    return await get_effective_schema(session, doc, allow_pre_release=allow_pre)


@router.post("/documents/{document_id}/form-schema:checkin", status_code=status.HTTP_201_CREATED)
async def checkin_form_schema_endpoint(
    document_id: uuid.UUID,
    body: FormSchemaCheckin,
    caller: AppUser = Depends(_edit),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """Freeze the working schema into an immutable Draft version (its WORM source blob IS the
    canonical-serialized schema; the schema is pinned into metadata_snapshot). Then submit-review →
    approve → release drives it Effective. Needs ``document.edit``."""
    doc = await _load_document(session, caller, document_id)
    version = await checkin_form_schema(
        session,
        vault_sink,
        caller,
        doc,
        change_reason=body.change_reason,
        change_significance=body.change_significance,
    )
    return _version(version)


# --- clause mappings (S9): the M:N document↔clause link satisfying the submit gate --------


@router.get("/documents/{document_id}/clause-mappings")
async def list_clause_mappings_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    doc = await _load_document(session, caller, document_id)
    rows = await vault_repo.list_clause_mappings(session, doc.id)
    return [_clause_mapping(m, c) for m, c in rows]


@router.post("/documents/{document_id}/clause-mappings", status_code=status.HTTP_201_CREATED)
async def map_clause_endpoint(
    document_id: uuid.UUID,
    body: ClauseMappingCreate,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    clause = await vault_repo.get_clause(session, body.clause_id)
    if clause is None:
        raise ProblemException(status=404, code="not_found", title="Clause not found")
    # Multi-standard safety (D3): a document may only map to clauses of its own framework.
    if clause.framework_id != doc.framework_id:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Clause belongs to a different framework",
            errors=[
                {
                    "field": "clause_id",
                    "code": "framework_mismatch",
                    "message": "the clause and the document must share a framework",
                }
            ],
        )
    if await vault_repo.get_clause_mapping(session, doc.id, clause.id) is not None:
        raise ProblemException(status=409, code="conflict", title="Clause already mapped")
    mapping = ClauseMapping(
        org_id=doc.org_id,
        framework_id=doc.framework_id,
        clause_id=clause.id,
        documented_information_id=doc.id,
        is_requirement_level=body.is_requirement_level,
        created_by=caller.id,
    )
    session.add(mapping)
    try:
        await session.flush()  # the UNIQUE backstop for a concurrent duplicate map
    except IntegrityError:
        await session.rollback()
        raise ProblemException(status=409, code="conflict", title="Clause already mapped") from None
    _emit_clause_event(
        session,
        caller,
        EventType.CLAUSE_MAPPED,
        doc,
        after={
            "clause_id": str(clause.id),
            "clause_number": clause.number,
            "is_requirement_level": mapping.is_requirement_level,
        },
    )
    await session.commit()
    return _clause_mapping(mapping, clause)


@router.delete(
    "/documents/{document_id}/clause-mappings/{clause_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unmap_clause_endpoint(
    document_id: uuid.UUID,
    clause_id: uuid.UUID,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # FOR UPDATE on the document row serializes this against a concurrent submit-review (which also
    # locks the doc row): the submit's mapping count and a last-mapping delete can't interleave to
    # leave an InReview document with zero mappings.
    doc = await _load_document(session, caller, document_id, for_update=True)
    mapping = await vault_repo.get_clause_mapping(session, doc.id, clause_id)
    if mapping is None:
        raise ProblemException(status=404, code="not_found", title="Clause mapping not found")
    clause = await vault_repo.get_clause(session, clause_id)
    await session.delete(mapping)
    _emit_clause_event(
        session,
        caller,
        EventType.CLAUSE_UNMAPPED,
        doc,
        before={
            "clause_id": str(clause_id),
            "clause_number": clause.number if clause else None,
        },
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- process links (S9c, doc 02 §6.2) — the M:N document↔process join, the clause-mappings shape --


@router.get("/documents/{document_id}/process-links")
async def list_process_links_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    doc = await _load_document(session, caller, document_id)
    rows = await vault_repo.list_process_links(session, doc.id)
    return [_process_link(link, p) for link, p in rows]


@router.post("/documents/{document_id}/process-links", status_code=status.HTTP_201_CREATED)
async def link_process_endpoint(
    document_id: uuid.UUID,
    body: ProcessLinkCreate,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    process = await vault_repo.get_process(session, body.process_id)
    if process is None or process.org_id != caller.org_id:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Process does not exist",
            errors=[
                {"field": "process_id", "code": "not_found", "message": "process does not exist"}
            ],
        )
    if await vault_repo.get_process_link(session, process.id, doc.id) is not None:
        raise ProblemException(status=409, code="conflict", title="Process already linked")
    link = ProcessLink(
        org_id=doc.org_id,
        process_id=process.id,
        documented_information_id=doc.id,
        created_by=caller.id,
    )
    session.add(link)
    try:
        await session.flush()  # the UNIQUE backstop for a concurrent duplicate link
    except IntegrityError:
        await session.rollback()
        raise ProblemException(
            status=409, code="conflict", title="Process already linked"
        ) from None
    _emit_process_link_event(
        session,
        caller,
        EventType.PROCESS_LINKED,
        doc,
        after={"process_id": str(process.id), "process_name": process.name},
    )
    await session.commit()
    return _process_link(link, process)


@router.delete(
    "/documents/{document_id}/process-links/{process_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_process_endpoint(
    document_id: uuid.UUID,
    process_id: uuid.UUID,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
) -> Response:
    doc = await _load_document(session, caller, document_id)
    link = await vault_repo.get_process_link(session, process_id, doc.id)
    # The link is keyed to the already-org-checked doc, so it's this org's by construction; the
    # explicit org guard is belt-and-suspenders (mirrors processes.remove_edge_endpoint).
    if link is None or link.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Process link not found")
    process = await vault_repo.get_process(session, process_id)
    await session.delete(link)
    _emit_process_link_event(
        session,
        caller,
        EventType.PROCESS_UNLINKED,
        doc,
        before={
            "process_id": str(process_id),
            "process_name": process.name if process else None,
        },
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- document↔document links + where-used (S-dcr-2, doc 05 §7, doc 14 §5.6) --------------


class DocumentLinkCreate(BaseModel):
    to_document_id: uuid.UUID
    link_type: DocumentLinkType


def _document_link(link: DocumentLink) -> dict[str, Any]:
    return {
        "id": str(link.id),
        "from_document_id": str(link.from_document_id),
        "to_document_id": str(link.to_document_id),
        "link_type": link.link_type.value,
        "created_at": link.created_at.isoformat(),
    }


@router.get("/documents/{document_id}/links")
async def list_document_links_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Every document_link touching this document (outbound + inbound). Needs ``document.read``."""
    await _load_document(session, caller, document_id)
    rows = (
        (
            await session.execute(
                select(DocumentLink)
                .where(
                    (DocumentLink.from_document_id == document_id)
                    | (DocumentLink.to_document_id == document_id)
                )
                .order_by(DocumentLink.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_document_link(link) for link in rows]


@router.post("/documents/{document_id}/links", status_code=status.HTTP_201_CREATED)
async def create_document_link_endpoint(
    document_id: uuid.UUID,
    body: DocumentLinkCreate,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a directed ``from → to`` document link of ``link_type`` (doc 14 §5.6). Both must be
    in-org controlled Documents; no self-link; 409 on a duplicate (from,to,type). Needs
    ``document.manage_metadata``."""
    doc = await _load_document(session, caller, document_id)
    if body.to_document_id == document_id:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="A document cannot link to itself",
            errors=[{"field": "to_document_id", "code": "no_self", "message": "self-link"}],
        )
    target = await session.get(DocumentedInformation, body.to_document_id)
    if target is None or target.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Target document not found")
    if target.kind != DocumentKind.DOCUMENT:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="A link target must be a controlled Document (not a Record)",
            errors=[{"field": "to_document_id", "code": "not_a_document", "message": "not a doc"}],
        )
    link = DocumentLink(
        org_id=doc.org_id,
        from_document_id=doc.id,
        to_document_id=body.to_document_id,
        link_type=body.link_type,
        created_by=caller.id,
    )
    session.add(link)
    try:
        await session.flush()  # the UNIQUE(from,to,type) backstop for a concurrent duplicate
    except IntegrityError:
        await session.rollback()
        raise ProblemException(status=409, code="conflict", title="Link already exists") from None
    _emit_clause_event(
        session,
        caller,
        EventType.DOCUMENT_LINKED,
        doc,
        after={"to_document_id": str(body.to_document_id), "link_type": body.link_type.value},
    )
    await session.commit()
    return _document_link(link)


@router.delete("/documents/{document_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document_link_endpoint(
    document_id: uuid.UUID,
    link_id: uuid.UUID,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove a document link touching this document. Needs ``document.manage_metadata``."""
    doc = await _load_document(session, caller, document_id)
    link = await session.get(DocumentLink, link_id)
    if (
        link is None
        or link.org_id != caller.org_id
        or document_id not in (link.from_document_id, link.to_document_id)
    ):
        raise ProblemException(status=404, code="not_found", title="Document link not found")
    before = {
        "to_document_id": str(link.to_document_id),
        "from_document_id": str(link.from_document_id),
        "link_type": link.link_type.value,
    }
    await session.delete(link)
    _emit_clause_event(session, caller, EventType.DOCUMENT_UNLINKED, doc, before=before)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}/where-used")
async def where_used_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The doc 05 §7.2 where-used panel for this document (processes · child/parent documents ·
    referenced-by · forms/templates · records-produced-under · clauses · related CAPAs/findings) +
    the §7.3 ``obsoletion_safety`` advisory. Needs ``document.read``."""
    doc = await _load_document(session, caller, document_id)
    return await build_where_used(session, caller.org_id, doc.id)


# --- distribution & acknowledgements (S-ack-1, doc 04 §8.1/§8.2, R42/R43) -----------------


_V1_TARGET_KINDS = {DistributionTargetType.user, DistributionTargetType.org_role}


def _emit_distribution_event(
    session: AsyncSession,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a ``DISTRIBUTION_UPDATED`` audit_event BEFORE commit (the ``_emit_clause_event``
    shape) — but WITH ``scope_ref=identifier``: the per-document trail filters on
    ``scope_ref == doc.identifier`` (audit.py::document_audit_events), so the clause helper's
    NULL scope_ref would hide distribution changes from GET /documents/{id}/audit-events
    (the decide.py DOCUMENT_ACKNOWLEDGED / sweep.py STAGE_FAILED precedent)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.DISTRIBUTION_UPDATED,
            object_type=AuditObjectType.document,
            object_id=doc.id,
            scope_ref=doc.identifier,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


def _distribution_entry(e: DistributionEntry) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "target_type": e.target_type.value,
        "target_id": str(e.target_id),
        "ack_required": e.ack_required,
        "created_at": e.created_at.isoformat(),
    }


async def _distribution_payload(
    session: AsyncSession, doc: DocumentedInformation
) -> dict[str, Any]:
    entries = await ack_queries.list_entries(session, doc.id)
    coverage = await ack_queries.coverage_counts(session, doc)
    return {
        "acknowledgement_required": doc.acknowledgement_required,
        "entries": [_distribution_entry(e) for e in entries],
        "coverage": coverage,
    }


@router.get("/documents/{document_id}/distribution")
async def get_distribution_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The distribution list + the doc flag + a counts-only coverage rollup (doc 15 §8.5; gated
    ``document.read`` — Sam-safe, no names)."""
    doc = await _load_document(session, caller, document_id)
    return await _distribution_payload(session, doc)


@router.post("/documents/{document_id}/distribution")
async def update_distribution_endpoint(
    document_id: uuid.UUID,
    body: DistributionUpdate,
    caller: AppUser = Depends(_distribute),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Add entries and/or set the doc-level ack flag (R42 ``document.distribute``). 422
    ``target_kind_deferred`` for the deferred ``process``/``folder`` kinds (R43); targets are
    validated in-org. Post-commit the doc-scoped ack sweep reconciles obligations."""
    # A no-op body writes no audit row and enqueues no sweep.
    if not body.add_entries and body.acknowledgement_required is None:
        doc_ro = await _load_document(session, caller, document_id)
        return await _distribution_payload(session, doc_ro)
    # FOR UPDATE (the update_metadata_endpoint precedent): the flag flip must not race a
    # concurrent cutover/sweep recompute reading acknowledgement_required mid-flight.
    doc = await _load_document(session, caller, document_id, for_update=True)
    before = {"acknowledgement_required": doc.acknowledgement_required}
    # Two-pass: validate everything BEFORE adding — an autoflush during a later item's
    # session.get would otherwise surface a pre-existing-duplicate IntegrityError outside
    # the guarded flush (500, not 409).
    validated: list[tuple[DistributionTargetType, Any]] = []
    for item in body.add_entries:
        try:
            kind = DistributionTargetType(item.target_type)
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title="Unknown target_type"
            ) from exc
        if kind not in _V1_TARGET_KINDS:
            raise ProblemException(
                status=422,
                code="target_kind_deferred",
                title="process/folder targets are deferred until owner-assignment lands (R43)",
            )
        if kind is DistributionTargetType.user:
            target_user = await session.get(AppUser, item.target_id)
            if target_user is None or target_user.org_id != caller.org_id:
                raise ProblemException(status=404, code="not_found", title="Target user not found")
        else:
            role = await session.get(Role, item.target_id)
            if role is None or role.org_id != caller.org_id:
                raise ProblemException(status=404, code="not_found", title="Target role not found")
        validated.append((kind, item))
    added: list[dict[str, Any]] = []
    for kind, item in validated:
        session.add(
            DistributionEntry(
                org_id=doc.org_id,
                document_id=doc.id,
                target_type=kind,
                target_id=item.target_id,
                ack_required=item.ack_required,
                created_by=caller.id,
            )
        )
        added.append(
            {
                "target_type": kind.value,
                "target_id": str(item.target_id),
                "ack_required": item.ack_required,
            }
        )
    if body.acknowledgement_required is not None:
        doc.acknowledgement_required = body.acknowledgement_required
        doc.updated_by = caller.id
    try:
        await session.flush()  # the UNIQUE(document_id, target_type, target_id) backstop
    except IntegrityError:
        await session.rollback()
        raise ProblemException(
            status=409, code="conflict", title="Distribution entry already exists"
        ) from None
    _emit_distribution_event(
        session,
        caller,
        doc,
        before=before,
        after={
            "acknowledgement_required": doc.acknowledgement_required,
            "added_entries": added,
        },
    )
    await session.commit()
    # Post-commit: the doc-scoped sweep mints/cancels against the new audience (best-effort; the
    # daily Beat run is the self-heal). Session stays usable (expire_on_commit=False).
    get_ack_enqueue_sink().enqueue(str(doc.id), trigger="distribution_updated")
    return await _distribution_payload(session, doc)


@router.delete(
    "/documents/{document_id}/distribution/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_distribution_entry_endpoint(
    document_id: uuid.UUID,
    entry_id: uuid.UUID,
    caller: AppUser = Depends(_distribute),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove one entry (R42). The doc-scoped sweep cancels lapsed obligations post-commit."""
    # FOR UPDATE: the doc row is the serialization point decide_doc_ack's live obligation check
    # locks — without it a removal can commit between an in-flight ack's audience read and its
    # INSERT, minting an acknowledgement for a just-removed recipient (the POST path already
    # serializes the same way).
    doc = await _load_document(session, caller, document_id, for_update=True)
    entry = await session.get(DistributionEntry, entry_id)
    if entry is None or entry.org_id != caller.org_id or entry.document_id != doc.id:
        raise ProblemException(status=404, code="not_found", title="Distribution entry not found")
    before = _distribution_entry(entry)
    await session.delete(entry)
    _emit_distribution_event(session, caller, doc, before=before)
    await session.commit()
    get_ack_enqueue_sink().enqueue(str(doc.id), trigger="distribution_updated")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}/acknowledgements")
async def get_acknowledgements_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_distribute),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The NAMED per-user status matrix for the current Effective version (doc 13 §6.3: Mara sees
    the full matrix; Sam's own status rides his tasks + the counts rollup). Gated R42
    ``document.distribute``."""
    doc = await _load_document(session, caller, document_id)
    return await ack_queries.coverage_matrix(session, doc)


# --- check-out / upload / check-in ------------------------------------------------------


@router.post("/documents/{document_id}/checkout")
async def checkout_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_checkout),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    wd = await checkout(session, vault_sink, caller, doc)
    return _working_draft(wd)


@router.post("/documents/{document_id}/versions:init-upload")
async def init_upload_endpoint(
    document_id: uuid.UUID,
    body: InitUpload,
    caller: AppUser = Depends(_checkout),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    return await init_upload(session, caller, doc, body.sha256, body.content_type)


@router.post("/documents/{document_id}/checkin", status_code=status.HTTP_201_CREATED)
async def checkin_endpoint(
    document_id: uuid.UUID,
    body: CheckIn,
    caller: AppUser = Depends(_edit),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    version, change_detected = await checkin(
        session,
        vault_sink,
        caller,
        doc,
        sha256=body.sha256,
        change_reason=body.change_reason,
        change_significance=body.change_significance,
        mime_type=body.mime_type,
    )
    return {**_version(version), "change_detected": change_detected}


@router.post("/documents/{document_id}/break-lock")
async def break_lock_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_checkout),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    await break_lock(session, vault_sink, caller, doc)
    return {"document_id": str(doc.id), "lock_broken": True}


@router.post("/documents/{document_id}/heartbeat")
async def heartbeat_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_checkout),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    remaining = await heartbeat(session, caller, doc)
    return {"document_id": str(doc.id), "lock_ttl_seconds": remaining}


# --- lifecycle (S4): named POST action sub-resources; never PATCH status= -----------------


@router.post("/documents/{document_id}/submit-review")
async def submit_review_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_submit),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    # T2/T9 + the approval workflow instantiation + the audit row all commit together (in-txn
    # audit, S6). Approval routes through POST /tasks/{id}/decision (C7) — no direct /approve.
    # FOR UPDATE serializes concurrent submit-review on the same document, so two callers cannot
    # both pass the Draft→InReview FSM check and create duplicate workflow instances/tasks.
    doc = await _load_document(session, caller, document_id, for_update=True)
    # S-obj-4 (O-5): a generic submit on an OBJ would advance a version AROUND the content-aware
    # commitment freeze (objective submit re-freezes when the working commitment changed) — the
    # guard lives HERE, not in submit_review, which the objective endpoint also calls.
    await reject_objective_byte_path(session, doc)
    result = await submit_review(session, caller, doc)
    await instantiate_approval(session, result.doc, caller)
    audit_transition(session, vault_sink, result, caller)
    await session.commit()  # T2/T9 + workflow instantiation + audit commit atomically
    return _document(result.doc)


@router.post("/documents/{document_id}/release")
async def release_endpoint(
    document_id: uuid.UUID,
    body: Release,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    # Enforce imperatively (not via a path-only dependency): the SoD-2 scope must resolve for the
    # SAME version the cutover will promote (body.version_id, else the latest Approved). The cutover
    # then re-reads the document authoritatively under a row lock in its own SERIALIZABLE session.
    await _load_document(session, caller, document_id)  # 404 + org guard
    resource = await _release_scope(session, document_id, body.version_id)
    await enforce(session, authz_sink, request, caller, "document.release", resource, sig_hook=True)
    doc = await release(caller, document_id, vault_sink, sig_sink, version_id=body.version_id)
    return _document(doc)


# --- S-leadership-1: Top-Management release authorization (POL/OBJ/MR) ---------------------


class LeadershipAuthorizationRequest(BaseModel):
    comment: str | None = None


def _leadership_authorization(instance: WorkflowInstance, tasks: list[Task]) -> dict[str, Any]:
    """The current Top-Management release-authorization cycle for a leadership artifact — the latest
    workflow instance + its tasks (the ``GET /documents/{id}/approval`` analogue). ``current_state``
    is the pending stage key, ``COMPLETED`` (the version is authorized → release is permitted),
    ``REJECTED``, or ``NEEDS_ATTENTION`` (no Top-Management member assigned)."""
    return {
        "instance_id": str(instance.id),
        "subject_id": str(instance.subject_id),
        "current_state": instance.current_state,
        "started_at": instance.started_at.isoformat() if instance.started_at else None,
        "tasks": [
            {
                "id": str(t.id),
                "stage_key": t.stage_key,
                "state": t.state.value,
                "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None,
                "candidate_pool": t.candidate_pool,
                "action_expected": t.action_expected,
            }
            for t in tasks
        ],
    }


@router.post(
    "/documents/{document_id}/request-leadership-authorization",
    status_code=status.HTTP_201_CREATED,
)
async def request_leadership_authorization_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_approve),
    session: AsyncSession = Depends(get_session),
    body: LeadershipAuthorizationRequest | None = None,
) -> dict[str, Any]:
    """Request a Top-Management RELEASE authorization for an Approved leadership artifact
    (POL/OBJ/MR) — gate ``document.approve`` at the doc's scope (the requester; the SIGN is
    candidate-pool authority, not a key). Opens an engine workflow routed to the reserved "Top
    Management" role; the Approved version becomes releasable only when a member signs
    (``meaning=verify``). 409 unless a leadership type / unless Approved / if an authorization is
    already in flight; ``NEEDS_ATTENTION`` when no Top-Management member is assigned. The welded
    approve/release path is unchanged."""
    instance = await request_leadership_authorization(
        session, caller, document_id, comment=body.comment if body else None
    )
    tasks = await wf_repo.list_instance_tasks(session, instance.id)
    return _leadership_authorization(instance, tasks)


@router.get("/documents/{document_id}/leadership-authorization")
async def get_leadership_authorization_endpoint(
    document_id: uuid.UUID,
    request: Request,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The leadership release-authorization status for a document (gate ``document.read``):
    ``is_leadership_artifact`` (POL/OBJ/MR), ``required`` (the org flag is on AND it is a leadership
    type → release is gated), the current Approved ``version_id``, whether that version is already
    ``authorized``, ``can_request`` (CX-1: whether THIS caller holds ``document.approve`` at the
    doc's scope → may start a cycle; the request endpoint's gate, ABAC-aware), and the latest
    authorization cycle (``instance``) or ``null``. Never 404 for a no-cycle document (the
    ``GET /documents/{id}/approval`` analogue)."""
    doc = await _load_document(session, caller, document_id)
    state = await release_authorization_status(session, doc)
    can_request = await _can_request_leadership_authorization(
        session, caller, doc, source_ip=request.client.host if request.client else None
    )
    instance = await wf_repo.latest_instance_for_subject(
        session, caller.org_id, WorkflowSubjectType.LEADERSHIP_AUTHORIZATION, doc.id
    )
    cycle = None
    if instance is not None:
        tasks = await wf_repo.list_instance_tasks(session, instance.id)
        cycle = _leadership_authorization(instance, tasks)
    return {**state, "can_request": can_request, "instance": cycle}


@router.post("/documents/{document_id}/start-revision")
async def start_revision_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_edit),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    # S-obj-4 (O-5): objective revisions ride POST /objectives/{id}/start-revision
    # (objective.manage — the QMS Owner holds no document.edit); same guard placement rationale.
    await reject_objective_byte_path(session, doc)
    return _document(await start_revision(session, vault_sink, caller, doc))


@router.post("/documents/{document_id}/obsolete")
async def obsolete_endpoint(
    document_id: uuid.UUID,
    body: Obsolete,
    caller: AppUser = Depends(_obsolete),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
    return _document(
        await obsolete(
            session,
            vault_sink,
            sig_sink,
            caller,
            doc,
            reason=body.reason,
            version_id=body.version_id,
            force_retire=body.force_retire,
            override_justification=body.override_justification,
        )
    )


@router.get("/documents/{document_id}/download")
async def download_document_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The Effective version's controlled-copy PDF (doc 15 §8.5). Presigns the cached watermarked
    rendition (``rendition:"controlled_copy"``) when it exists; otherwise the source blob
    (``rendition:"source"`` — the controlled PDF is still rendering or the format is non-renderable,
    R26). Distinct from ``/versions/{vid}/download`` (a specific version's source)."""
    doc = await _load_document(session, caller, document_id)
    if doc.current_effective_version_id is None:
        raise ProblemException(
            status=404, code="not_found", title="No effective version to download"
        )
    version = await session.get(DocumentVersion, doc.current_effective_version_id)
    if version is None:  # pragma: no cover - defensive (the FK is set at the cutover)
        raise ProblemException(status=404, code="not_found", title="Effective version not found")

    if version.rendition_blob_sha256 is not None:
        rendition = await vault_repo.get_blob(session, version.rendition_blob_sha256)
        if rendition is not None:
            url = await storage.presign_get(rendition.object_key, bucket=rendition.bucket)
            return {
                "download_url": url,
                "content_type": "application/pdf",
                "rendition": "controlled_copy",
                "sha256": rendition.sha256,
            }

    blob = await vault_repo.get_blob(session, version.source_blob_sha256)
    if blob is None:  # pragma: no cover - defensive
        raise ProblemException(status=404, code="not_found", title="Blob not found")
    url = await storage.presign_get(blob.object_key, bucket=blob.bucket)
    return {
        "download_url": url,
        "content_type": blob.mime_type,
        "rendition": "source",
        "sha256": blob.sha256,
    }


@router.get("/documents/{document_id}/export")
async def export_document_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_export),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> Response:
    """A FRESH, per-request export rendition of the Effective version, stamped "UNCONTROLLED WHEN
    PRINTED — valid as of {date}" + "Exported {ts} by {user}" (doc 04 §11.2). Streamed as an
    ``application/pdf`` attachment (NOT the JSON presign that ``/download`` returns) and audited as
    an ``EXPORTED`` event — distinct from the cached, deterministic controlled copy. 409
    ``no_controlled_rendition`` if no controlled PDF exists yet (R26/pending → use ``/download``).
    Needs ``document.export``."""
    doc = await _load_document(session, caller, document_id)
    pdf, filename = await render_dynamic_copy(session, vault_sink, caller, doc, intent="export")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/documents/{document_id}/print")
async def print_document_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_print),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> Response:
    """A FRESH, per-request print rendition of the Effective version, stamped "CONTROLLED COPY —
    valid on {date} only" + "Printed {ts} by {user}" (doc 04 §11.2). Streamed ``inline`` as an
    ``application/pdf`` for the browser print dialog and audited as a ``PRINTED`` event. 409
    ``no_controlled_rendition`` if no controlled PDF exists yet (R26/pending). Needs
    ``document.print_controlled``."""
    doc = await _load_document(session, caller, document_id)
    pdf, filename = await render_dynamic_copy(session, vault_sink, caller, doc, intent="print")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# --- versions ---------------------------------------------------------------------------


@router.get("/documents/{document_id}/versions")
async def list_versions_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read_draft),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _load_document(session, caller, document_id)
    rows = (
        (
            await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(desc(DocumentVersion.version_seq))
            )
        )
        .scalars()
        .all()
    )
    return [_version(v) for v in rows]


async def _load_version(
    session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID
) -> DocumentVersion:
    version = await session.get(DocumentVersion, version_id)
    if version is None or version.document_id != document_id:
        raise ProblemException(status=404, code="not_found", title="Version not found")
    return version


@router.get("/documents/{document_id}/versions/{version_id}")
async def get_version_endpoint(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    caller: AppUser = Depends(_read_draft),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _load_document(session, caller, document_id)
    return _version(await _load_version(session, document_id, version_id))


@router.get("/documents/{document_id}/versions/{version_id}/diff")
async def diff_versions_endpoint(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    from_version_id: uuid.UUID = Query(alias="from"),
    caller: AppUser = Depends(_read_draft),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The doc 05 §8 redline of ``from`` → ``version_id`` (both versions of THIS document): the
    metadata diff (frozen snapshots) + the text redline (on-demand Tika extraction + line-LCS;
    degrades to ``unavailable`` if text can't be extracted) + both provenance headers. Read-only.
    Gated on ``document.read_draft`` (the diff exposes non-released version content, like the other
    version-read endpoints — `document.read` alone must NOT leak Draft text). The visual page-image
    diff is S-dcr-3b."""
    await _load_document(session, caller, document_id)
    to_version = await _load_version(session, document_id, version_id)
    from_version = await _load_version(session, document_id, from_version_id)
    return await build_version_diff(session, from_version, to_version)


def _visual_diff_status(vd: VisualDiff) -> dict[str, Any]:
    return {
        "status": vd.status.value,
        "page_count": vd.page_count,
        "reason": vd.reason,
        "pages": (
            [{"page": p["page"], "changed": p["changed"]} for p in vd.pages] if vd.pages else None
        ),
    }


@router.post(
    "/documents/{document_id}/versions/{version_id}/visual-diff",
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_visual_diff_endpoint(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    response: Response,
    from_version_id: uuid.UUID = Query(alias="from"),
    caller: AppUser = Depends(_read_draft),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Request the doc 05 §8.1 visual page-image diff of ``from`` → ``version_id`` (worker-async,
    since the API can't render). Idempotent — UPSERTs the cached ``visual_diff`` row + enqueues the
    worker task when not already terminal. 202 while Pending, 200 once Ready (poll GET). Needs
    ``document.read_draft``."""
    doc = await _load_document(session, caller, document_id)
    to_version = await _load_version(session, document_id, version_id)
    from_version = await _load_version(session, document_id, from_version_id)
    vd, should_enqueue = await get_or_create_visual_diff(
        session,
        org_id=caller.org_id,
        document_id=doc.id,
        from_version_id=from_version.id,
        to_version_id=to_version.id,
    )
    if should_enqueue:
        visual_diff_task.delay(str(vd.id))
    if vd.status is not VisualDiffStatus.Pending:
        response.status_code = status.HTTP_200_OK
    return _visual_diff_status(vd)


@router.get("/documents/{document_id}/versions/{version_id}/visual-diff")
async def get_visual_diff_endpoint(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    response: Response,
    from_version_id: uuid.UUID = Query(alias="from"),
    caller: AppUser = Depends(_read_draft),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Poll the cached visual-diff result (no side effect). 404 if not yet requested (POST first);
    202 while Pending; 200 once terminal. Needs ``document.read_draft``."""
    await _load_document(session, caller, document_id)
    to_version = await _load_version(session, document_id, version_id)
    from_version = await _load_version(session, document_id, from_version_id)
    vd = await get_visual_diff(session, from_version.id, to_version.id)
    if vd is None or vd.org_id != caller.org_id:
        raise ProblemException(
            status=404, code="not_found", title="No visual diff requested (POST to compute)"
        )
    if vd.status is VisualDiffStatus.Pending:
        response.status_code = status.HTTP_202_ACCEPTED
    return _visual_diff_status(vd)


@router.get("/documents/{document_id}/versions/{version_id}/visual-diff/page/{page}")
async def get_visual_diff_page_endpoint(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    page: int,
    from_version_id: uuid.UUID = Query(alias="from"),
    layer: str = Query(default="diff"),
    caller: AppUser = Depends(_read_draft),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stream one page's rendered PNG (``layer`` = from | to | diff). 404 unless the visual diff is
    Ready and the page/layer exists. Needs ``document.read_draft``."""
    if layer not in ("from", "to", "diff"):
        raise ProblemException(
            status=422, code="validation_error", title="layer must be from|to|diff"
        )
    await _load_document(session, caller, document_id)
    to_version = await _load_version(session, document_id, version_id)
    from_version = await _load_version(session, document_id, from_version_id)
    vd = await get_visual_diff(session, from_version.id, to_version.id)
    if (
        vd is None
        or vd.org_id != caller.org_id
        or vd.status is not VisualDiffStatus.Ready
        or not vd.pages
        or page < 0
        or page >= len(vd.pages)
    ):
        raise ProblemException(status=404, code="not_found", title="Visual-diff page not available")
    sha = vd.pages[page].get(f"{layer}_blob_sha")
    if sha is None:
        raise ProblemException(status=404, code="not_found", title="No image for this page/layer")
    blob = await vault_repo.get_blob(session, sha)
    if blob is None:  # pragma: no cover - defensive (the cache wrote the row)
        raise ProblemException(status=404, code="not_found", title="Page image blob missing")
    png = await storage.fetch_bytes(blob.object_key, bucket=blob.bucket)
    return Response(content=png, media_type="image/png")


@router.get("/documents/{document_id}/versions/{version_id}/download")
async def download_version_endpoint(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    caller: AppUser = Depends(_read_draft),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _load_document(session, caller, document_id)
    version = await _load_version(session, document_id, version_id)
    blob = await vault_repo.get_blob(session, version.source_blob_sha256)
    if blob is None:
        raise ProblemException(status=404, code="not_found", title="Blob not found")
    url = await storage.presign_get(blob.object_key, bucket=blob.bucket)
    return {"download_url": url, "content_type": blob.mime_type, "sha256": blob.sha256}
