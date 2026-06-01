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

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._signature_enums import SignatureMeaning
from ..db.models._vault_enums import VersionState
from ..db.models.app_user import AppUser
from ..db.models.document_type import DocumentType
from ..db.models.document_version import DocumentVersion
from ..db.models.documented_information import DocumentedInformation
from ..db.models.signature_event import SignatureEvent as SignatureEventRow
from ..db.models.working_draft import WorkingDraft
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
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


async def _release_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Release scope = the base document scope PLUS the SoD-2 inputs: the version about to go
    Effective (the latest Approved) and its immutable author + prior approval signers. SoD-2 blocks
    the author from releasing their own edit and (unless ``allow_approver_release``) the sole
    approver. Degrades to the base scope when there is no Approved version (the FSM 409 fires).
    """
    base = await _document_scope(request, session)
    if base.artifact_id is None:
        return base
    doc_id = uuid.UUID(base.artifact_id)
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
    session: AsyncSession, caller: AppUser, raw_id: uuid.UUID
) -> DocumentedInformation:
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
# route through POST /tasks/{id}/decision now (S5, removed from here). release/obsolete are flat
# sig-hook actions (C7); release uses the SoD-aware ``_release_scope``. start-revision reuses
# ``document.edit`` (no ``document.revise`` key exists).
_submit = require("document.submit", async_scope_resolver=_document_scope)
_release = require("document.release", async_scope_resolver=_release_scope, sig_hook=True)
_obsolete = require("document.obsolete", async_scope_resolver=_document_scope, sig_hook=True)


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
    # T2/T9 + the approval workflow instantiation commit together; the audit fires post-commit.
    # Approval itself routes through POST /tasks/{id}/decision (C7) — there is no direct /approve.
    doc = await _load_document(session, caller, document_id)
    result = await submit_review(session, caller, doc)
    await instantiate_approval(session, result.doc, caller)
    await session.commit()
    audit_transition(vault_sink, result, caller)
    return _document(result.doc)


@router.post("/documents/{document_id}/release")
async def release_endpoint(
    document_id: uuid.UUID,
    body: Release,
    caller: AppUser = Depends(_release),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    # _load_document is the 404/authz guard on the request session; the cutover re-reads the
    # document authoritatively under a row lock in its own SERIALIZABLE session.
    await _load_document(session, caller, document_id)
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
