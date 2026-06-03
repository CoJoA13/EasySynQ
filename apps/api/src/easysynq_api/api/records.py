"""The records surface (slice S-rec-1, doc 06, doc 15 §8.9): immutable capture, evidence download,
correction, and the evidence-for links.

Records are documented information of ``kind=RECORD`` (the shared-PK ``record`` subtype) — *proof an
activity happened*, immutable post-capture: deliberately **no PATCH/PUT/DELETE on a record**
(corrections capture a successor; only the evidence-link sub-resource has a DELETE — an annotation,
not a content edit). Capture/correction/linking gate on ``record.create`` (the record write family,
**seeded but held by no role at a records-reachable scope** → grant via a SYSTEM override until the
role UI, the ``document.export``/``process.create`` precedent); reads gate on ``record.read``. The
list is row-filtered to what the caller may ``record.read`` (doc 15 §9.3), not a hard 403.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._record_enums import RecordDispositionState, RecordType
from ..db.models.app_user import AppUser
from ..db.models.blob import Blob
from ..db.models.documented_information import DocumentedInformation
from ..db.models.evidence_blob import EvidenceBlob
from ..db.models.evidence_for_link import EvidenceForLink
from ..db.models.record import Record
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..problems import ProblemException
from ..services.authz import gather_grants, require
from ..services.records import (
    capture_correction,
    capture_record,
    link_evidence,
    record_init_upload,
    unlink_evidence,
)
from ..services.records import repository as records_repo
from ..services.vault import repository as vault_repo
from ..services.vault import storage

router = APIRouter(prefix="/api/v1", tags=["records"])


# --- request bodies ---------------------------------------------------------------------


class RecordInitUpload(BaseModel):
    sha256: str
    content_type: str = "application/octet-stream"


class EvidenceRef(BaseModel):
    sha256: str
    content_type: str = "application/octet-stream"


class RecordCreate(BaseModel):
    record_type: str
    title: str
    classification: str = "Internal"
    area_code: str | None = None
    source_document_id: uuid.UUID | None = None
    source_version_id: uuid.UUID | None = None
    evidence: list[EvidenceRef] = []
    form_field_values: dict[str, Any] | None = None
    retention_policy_id: uuid.UUID | None = None  # per-record override (doc 06 §5.1 tier 1)


class CorrectionCreate(RecordCreate):
    """Same body as a capture — the ``correction_of`` link comes from the path."""


class EvidenceLinkCreate(BaseModel):
    target_type: Literal["clause", "process", "document"]
    target_id: uuid.UUID
    link_reason: str | None = None


# --- serializers ------------------------------------------------------------------------


def _evidence_blob(eb: EvidenceBlob, b: Blob) -> dict[str, Any]:
    return {
        "sha256": eb.blob_sha256,
        "is_original": eb.is_original,
        "filename": eb.filename,
        "content_type": eb.content_type or b.mime_type,
        "size_bytes": b.size_bytes,
        "created_at": eb.created_at.isoformat() if eb.created_at else None,
    }


def _evidence_link(link: EvidenceForLink) -> dict[str, Any]:
    return {
        "id": str(link.id),
        "record_id": str(link.record_id),
        "target_type": link.target_type.value,
        "target_id": str(link.target_id),
        "link_reason": link.link_reason,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


def _record(
    record: Record,
    base: DocumentedInformation,
    *,
    evidence_blobs: list[dict[str, Any]] | None = None,
    evidence_links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(record.id),
        "identifier": base.identifier,
        "kind": base.kind.value,
        "record_type": record.record_type.value,
        "title": base.title,
        "classification": base.classification.value,
        "framework_id": str(base.framework_id),
        "captured_at": record.captured_at.isoformat() if record.captured_at else None,
        "captured_by": str(record.captured_by),
        "content_hash": record.content_hash,
        "source_document_id": (
            str(record.source_document_id) if record.source_document_id else None
        ),
        "source_version_id": str(record.source_version_id) if record.source_version_id else None,
        "form_field_values": record.form_field_values,
        "retention_policy_id": str(record.retention_policy_id),
        "retention_basis_date": (
            record.retention_basis_date.isoformat() if record.retention_basis_date else None
        ),
        "disposition_state": record.disposition_state.value,
        "legal_hold": record.legal_hold,
        "correction_of": str(record.correction_of) if record.correction_of else None,
        "superseded_by_correction": (
            str(record.superseded_by_correction) if record.superseded_by_correction else None
        ),
        "created_at": base.created_at.isoformat() if base.created_at else None,
    }
    if evidence_blobs is not None:
        out["evidence_blobs"] = evidence_blobs
    if evidence_links is not None:
        out["evidence_links"] = evidence_links
    return out


# --- helpers + gates --------------------------------------------------------------------


async def _record_scope(request: Any, session: AsyncSession) -> ResourceContext:
    """Resolve a record's ARTIFACT authz scope from the path id (SYSTEM grants always match)."""
    raw = request.path_params.get("record_id")
    if not raw:
        return ResourceContext.system()
    try:
        record_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    base = await session.get(DocumentedInformation, record_id)
    if base is None:
        return ResourceContext(artifact_id=str(record_id))
    return ResourceContext(artifact_id=str(base.id), folder_path=base.folder_path)


async def _load(
    session: AsyncSession, caller: AppUser, record_id: uuid.UUID
) -> tuple[Record, DocumentedInformation]:
    record = await records_repo.get_record(session, record_id)
    base = await records_repo.get_base(session, record_id)
    if record is None or base is None or record.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Record not found")
    return record, base


_read = require("record.read", async_scope_resolver=_record_scope)
_create = require("record.create")  # SYSTEM scope (create/init-upload — no path id)
_create_scoped = require("record.create", async_scope_resolver=_record_scope)  # per-record writes


async def _serialize_full(
    session: AsyncSession, record: Record, base: DocumentedInformation
) -> dict[str, Any]:
    blobs = await records_repo.list_evidence_blobs(session, record.id)
    links = await records_repo.list_evidence_links(session, record.id)
    return _record(
        record,
        base,
        evidence_blobs=[_evidence_blob(eb, b) for eb, b in blobs],
        evidence_links=[_evidence_link(link) for link in links],
    )


# --- endpoints --------------------------------------------------------------------------


@router.post("/records:init-upload")
async def init_upload_endpoint(
    body: RecordInitUpload,
    caller: AppUser = Depends(_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await record_init_upload(session, caller, body.sha256, body.content_type)


@router.post("/records", status_code=status.HTTP_201_CREATED)
async def capture_endpoint(
    body: RecordCreate,
    caller: AppUser = Depends(_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    record = await capture_record(
        session,
        caller,
        record_type=body.record_type,
        title=body.title,
        classification=body.classification,
        area_code=body.area_code,
        source_document_id=body.source_document_id,
        source_version_id=body.source_version_id,
        evidence=[(e.sha256, e.content_type) for e in body.evidence],
        form_field_values=body.form_field_values,
        retention_policy_id=body.retention_policy_id,
    )
    _, base = await _load(session, caller, record.id)
    return await _serialize_full(session, record, base)


@router.get("/records")
async def list_records_endpoint(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
    record_type: str | None = None,
    source_document_id: uuid.UUID | None = None,
    captured_by: uuid.UUID | None = None,
    disposition_state: str | None = None,
    legal_hold: bool | None = None,
) -> list[dict[str, Any]]:
    filters: list[ColumnElement[bool]] = []
    if record_type is not None:
        try:
            filters.append(Record.record_type == RecordType(record_type))
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title="Invalid record_type filter"
            ) from exc
    if source_document_id is not None:
        filters.append(Record.source_document_id == source_document_id)
    if captured_by is not None:
        filters.append(Record.captured_by == captured_by)
    if disposition_state is not None:
        try:
            filters.append(Record.disposition_state == RecordDispositionState(disposition_state))
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title="Invalid disposition_state filter"
            ) from exc
    if legal_hold is not None:
        filters.append(Record.legal_hold == legal_hold)
    rows = await records_repo.list_records(
        session, caller.org_id, filters=filters, limit=min(limit, 100)
    )
    # Filter-not-403 (doc 15 §9.3): drop rows the caller may not record.read.
    grants = await gather_grants(session, caller.id, caller.org_id, "record.read")
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC))
    visible: list[tuple[Record, DocumentedInformation]] = []
    for record, base in rows:
        resource = ResourceContext(artifact_id=str(record.id), folder_path=base.folder_path)
        if authorize(grants, "record.read", resource, ctx).allow:
            visible.append((record, base))
    return [_record(r, b) for r, b in visible]


@router.get("/records/{record_id}")
async def get_record_endpoint(
    record_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    record, base = await _load(session, caller, record_id)
    return await _serialize_full(session, record, base)


@router.get("/records/{record_id}/evidence/{sha256}/download")
async def download_evidence_endpoint(
    record_id: uuid.UUID,
    sha256: str,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _load(session, caller, record_id)  # 404 if the record is missing / not the caller's org
    eb = await records_repo.get_evidence_blob(session, record_id, sha256.lower())
    if eb is None:
        raise ProblemException(
            status=404, code="not_found", title="Evidence not attached to this record"
        )
    blob = await vault_repo.get_blob(session, eb.blob_sha256)
    if blob is None:  # pragma: no cover - defensive (the FK guarantees it)
        raise ProblemException(status=404, code="not_found", title="Evidence blob not found")
    url = await storage.presign_get(blob.object_key, bucket=blob.bucket)
    return {
        "download_url": url,
        "sha256": eb.blob_sha256,
        "content_type": eb.content_type or blob.mime_type,
    }


@router.post("/records/{record_id}/correction", status_code=status.HTTP_201_CREATED)
async def correction_endpoint(
    record_id: uuid.UUID,
    body: CorrectionCreate,
    caller: AppUser = Depends(_create_scoped),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    new_record = await capture_correction(
        session,
        caller,
        record_id,
        record_type=body.record_type,
        title=body.title,
        classification=body.classification,
        area_code=body.area_code,
        source_document_id=body.source_document_id,
        source_version_id=body.source_version_id,
        evidence=[(e.sha256, e.content_type) for e in body.evidence],
        form_field_values=body.form_field_values,
        retention_policy_id=body.retention_policy_id,
    )
    _, base = await _load(session, caller, new_record.id)
    return await _serialize_full(session, new_record, base)


@router.get("/records/{record_id}/evidence-links")
async def list_evidence_links_endpoint(
    record_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _load(session, caller, record_id)
    links = await records_repo.list_evidence_links(session, record_id)
    return [_evidence_link(link) for link in links]


@router.post("/records/{record_id}/evidence-links", status_code=status.HTTP_201_CREATED)
async def link_evidence_endpoint(
    record_id: uuid.UUID,
    body: EvidenceLinkCreate,
    caller: AppUser = Depends(_create_scoped),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    link = await link_evidence(
        session,
        caller,
        record_id,
        target_type=body.target_type,
        target_id=body.target_id,
        link_reason=body.link_reason,
    )
    return _evidence_link(link)


@router.delete(
    "/records/{record_id}/evidence-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def unlink_evidence_endpoint(
    record_id: uuid.UUID,
    link_id: uuid.UUID,
    caller: AppUser = Depends(_create_scoped),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await unlink_evidence(session, caller, record_id, link_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
