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
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models._signature_enums import SignatureMeaning
from ..db.models._vault_enums import VersionState
from ..db.models.app_user import AppUser
from ..db.models.audit_event import AuditEvent
from ..db.models.clause import Clause
from ..db.models.clause_mapping import ClauseMapping
from ..db.models.document_type import DocumentType
from ..db.models.document_version import DocumentVersion
from ..db.models.documented_information import DocumentedInformation
from ..db.models.process import Process
from ..db.models.process_link import ProcessLink
from ..db.models.signature_event import SignatureEvent as SignatureEventRow
from ..db.models.working_draft import WorkingDraft
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..logging import request_id_var
from ..problems import ProblemException
from ..services.authz import AuthzAuditSink, enforce, gather_grants, get_authz_audit_sink, require
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    audit_transition,
    break_lock,
    checkin,
    checkout,
    create_document,
    get_vault_audit_sink,
    get_vault_signature_sink,
    heartbeat,
    init_upload,
    obsolete,
    release,
    render_dynamic_copy,
    start_revision,
    storage,
    submit_review,
)
from ..services.vault import repository as vault_repo
from ..services.vault.locks import LOCK_TTL_SECONDS
from ..services.workflow import instantiate_approval

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


class Release(BaseModel):
    version_id: uuid.UUID | None = None


# --- representations --------------------------------------------------------------------


def _document(d: DocumentedInformation) -> dict[str, Any]:
    return {
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
        "created_at": d.created_at.isoformat() if d.created_at else None,
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
    doc_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a clause-mapping ``audit_event`` (object_type=document, keyed to the mapped artifact)
    BEFORE commit, so the link change + its audit row commit atomically (mirrors
    ``users._emit_user_event``). Hashes stay NULL — the S6 linker stamps them off the hot path."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.document,
            object_id=doc_id,
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
    version: DocumentVersion | None
    if version_id is not None:
        version = await session.get(DocumentVersion, version_id)
        if version is None or version.document_id != doc_id:
            return base
    else:
        version = (
            await session.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc_id,
                    DocumentVersion.version_state == VersionState.Approved,
                )
                .order_by(DocumentVersion.version_seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if version is None:
        return base
    # approver_user_ids comes from the version's recorded approval signatures. The author-side block
    # (actor == author_user_id) is the robust, signature-independent backstop; the approver-side
    # (sole-approver release) is only as strong as in-band signature emission (the decide() path
    # always emits it). A signature-less Approved version (only reachable by direct DB seeding) thus
    # leaves the approver-set empty — acceptable since the author-side block still holds.
    signers = (
        (
            await session.execute(
                select(SignatureEventRow.signer_user_id).where(
                    SignatureEventRow.signed_object_id == version.id,
                    SignatureEventRow.meaning == SignatureMeaning.approval,
                )
            )
        )
        .scalars()
        .all()
    )
    return dataclasses.replace(
        base,
        version_id=str(version.id),
        author_user_id=str(version.author_user_id),
        approver_user_ids=frozenset(str(s) for s in signers if s is not None),
    )


async def _load_document(
    session: AsyncSession, caller: AppUser, raw_id: uuid.UUID, *, for_update: bool = False
) -> DocumentedInformation:
    if for_update:
        doc = (
            await session.execute(
                select(DocumentedInformation)
                .where(DocumentedInformation.id == raw_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
    else:
        doc = await vault_repo.get_document(session, raw_id)
    if doc is None or doc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Document not found")
    return doc


_read = require("document.read", async_scope_resolver=_document_scope)
_read_draft = require("document.read_draft", async_scope_resolver=_document_scope)
_checkout = require("document.checkout", async_scope_resolver=_document_scope)
_edit = require("document.edit", async_scope_resolver=_document_scope)
_manage_metadata = require("document.manage_metadata", async_scope_resolver=_document_scope)
# Lifecycle actions. submit-review (S4) instantiates the approval workflow; approve/request-changes
# route through POST /tasks/{id}/decision now (S5, removed from here). release is a flat sig-hook
# action but enforces imperatively in-handler (its SoD-2 scope needs the body's version_id, which a
# path-only dependency cannot see). obsolete is a flat sig-hook action; start-revision reuses
# ``document.edit`` (no ``document.revise`` key exists).
_submit = require("document.submit", async_scope_resolver=_document_scope)
_obsolete = require("document.obsolete", async_scope_resolver=_document_scope, sig_hook=True)
# S7d export/print. The cheap cached controlled-copy presign stays on document.read (/download). The
# per-request UNCONTROLLED-when-printed export is gated on the SoD-sensitive document.export; the
# in-app controlled print on document.print_controlled (doc 07 §3.1 keys, both already seeded).
_export = require("document.export", async_scope_resolver=_document_scope)
_print = require("document.print_controlled", async_scope_resolver=_document_scope)


# --- documents --------------------------------------------------------------------------


@router.get("/documents")
async def list_documents(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
) -> list[dict[str, Any]]:
    grants = await gather_grants(session, caller.id, caller.org_id, "document.read")
    docs = (
        (
            await session.execute(
                select(DocumentedInformation)
                .where(DocumentedInformation.org_id == caller.org_id)
                .order_by(desc(DocumentedInformation.created_at))
                .limit(min(limit, 100))
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
    out: list[dict[str, Any]] = []
    for d in docs:
        resource = ResourceContext(
            artifact_id=str(d.id),
            folder_path=d.folder_path,
            document_level=levels.get(d.document_type_id) if d.document_type_id else None,
        )
        if authorize(grants, "document.read", resource, ctx).allow:
            out.append(_document(d))
    return out


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
    return _document(doc)


@router.patch("/documents/{document_id}")
async def update_metadata_endpoint(
    document_id: uuid.UUID,
    body: MetadataUpdate,
    caller: AppUser = Depends(_manage_metadata),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from ..db.models._vault_enums import Classification

    doc = await _load_document(session, caller, document_id)
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
    doc.updated_by = caller.id
    await session.commit()
    await session.refresh(doc)
    return _document(doc)


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
        doc.id,
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
        doc.id,
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
        doc.id,
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
        doc.id,
        before={
            "process_id": str(process_id),
            "process_name": process.name if process else None,
        },
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.post("/documents/{document_id}/start-revision")
async def start_revision_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_edit),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    doc = await _load_document(session, caller, document_id)
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
