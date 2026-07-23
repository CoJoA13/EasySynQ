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

import dataclasses
import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._evidence_enums import EvidenceForTargetType
from ..db.models._record_enums import RecordDispositionState, RecordType
from ..db.models.app_user import AppUser
from ..db.models.blob import Blob
from ..db.models.disposition_event import DispositionEvent
from ..db.models.documented_information import DocumentedInformation
from ..db.models.evidence_blob import EvidenceBlob
from ..db.models.evidence_for_link import EvidenceForLink
from ..db.models.record import Record
from ..db.models.worm_destroy_request import WormDestroyRequest
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..domain.records.retention import retention_until
from ..problems import ProblemException
from ..services.authz import AuthzAuditSink, enforce, gather_grants, get_authz_audit_sink, require
from ..services.records import (
    advance_disposition,
    approve_worm_destroy,
    cancel_worm_destroy,
    capture_correction,
    capture_record,
    link_evidence,
    place_legal_hold,
    record_init_upload,
    release_legal_hold,
    request_worm_destroy,
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
    # S-aud-2 enabled finding / capa_stage (reserved targets); clause/process/document from S-rec-1.
    target_type: Literal["clause", "process", "document", "finding", "capa_stage"]
    target_id: uuid.UUID
    link_reason: str | None = None


class DispositionAdvance(BaseModel):
    # ON_HOLD is intentionally not accepted here — legal hold uses POST /records/{id}/legal-hold.
    to_state: Literal["DUE_FOR_REVIEW", "ACTIVE", "DISPOSED"]
    reason: str | None = None


class LegalHoldAction(BaseModel):
    action: Literal["place", "release"]
    reason: str = Field(min_length=1, max_length=1000)


class WormDestroyRequestCreate(BaseModel):
    legal_basis: str = Field(min_length=1, max_length=1000)


class DispositionReason(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


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


def _worm_destroy_request(req: WormDestroyRequest) -> dict[str, Any]:
    if req.executed_at is not None:
        req_status = "executed"
    elif req.cancelled_at is not None:
        req_status = "cancelled"
    else:
        req_status = "open"
    return {
        "id": str(req.id),
        "record_id": str(req.record_id),
        "status": req_status,
        "legal_basis": req.legal_basis,
        "requested_by": str(req.requested_by),
        "requested_at": req.requested_at.isoformat() if req.requested_at else None,
        "approved_by": str(req.approved_by) if req.approved_by else None,
        "executed_at": req.executed_at.isoformat() if req.executed_at else None,
        "cancelled_by": str(req.cancelled_by) if req.cancelled_by else None,
        "cancelled_at": req.cancelled_at.isoformat() if req.cancelled_at else None,
    }


def _disposition_event(event: DispositionEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "action": event.action.value,
        "tombstone": event.tombstone,
        "policy_id": str(event.policy_id) if event.policy_id else None,
        "approved_by": str(event.approved_by) if event.approved_by else None,
        "requested_by": str(event.requested_by) if event.requested_by else None,
        "is_worm_destroy": event.is_worm_destroy,
        "legal_basis": event.legal_basis,
        "executed_at": event.executed_at.isoformat() if event.executed_at else None,
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
        "has_structured_pdf": record.structured_pdf_blob_sha256 is not None,
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


async def _record_process_scope(request: Any, session: AsyncSession) -> ResourceContext:
    """A record's FULL process-aware scope (S-records-R/W) — its context INCLUDING its process
    bindings (``record_process_ids`` leg A + leg B + the R3-1 correction fallback). Used by the
    ``record.read`` gate AND (S-records-W) the per-record ``record.create`` WRITE gate, so a bound
    Process-Owner can read AND author records bound to their process. A SYSTEM/ARTIFACT/FOLDER grant
    still matches via its own field — byte-identical. The binding-MINTING writes (correction +
    evidence-link) additionally re-authorize the TARGET process (``_enforce_target_process_record``)
    so a Process-Owner cannot escalate to an unowned process."""
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
    record = await records_repo.get_record(session, record_id)
    process_ids = (
        await records_repo.record_process_ids_effective(session, record)
        if record is not None
        else set()
    )
    return ResourceContext(
        artifact_id=str(base.id),
        kind="RECORD",
        folder_path=base.folder_path,
        framework_id=str(base.framework_id),
        process_ids=frozenset(process_ids),
    )


async def _record_base_scope(session: AsyncSession, record_id: uuid.UUID) -> ResourceContext:
    """The record's FULL NON-process scope (artifact/kind/folder/framework, NO process_ids) — the
    base for the per-target re-auth (``process_ids`` is then replaced with JUST the target)."""
    base = await session.get(DocumentedInformation, record_id)
    if base is None:
        return ResourceContext(artifact_id=str(record_id))
    return ResourceContext(
        artifact_id=str(base.id),
        kind="RECORD",
        folder_path=base.folder_path,
        framework_id=str(base.framework_id),
    )


async def _enforce_target_process_record(
    session: AsyncSession,
    sink: AuthzAuditSink,
    request: Request,
    caller: AppUser,
    record_id: uuid.UUID,
    target_process_ids: frozenset[str],
) -> None:
    """Re-authorize a binding-MINTING record write against the TARGET process(es) — the records
    analogue of ``documents._enforce_target_process``. S-records-W made the per-record WRITE gate
    process-aware (a Process-Owner reaches the write for a record bound to ANY of their processes),
    so re-enforce ``record.create`` over a scope whose ``process_ids`` is JUST the target: a SYSTEM/
    ARTIFACT/FOLDER holder still passes (record-level authority via the preserved fields), but a
    PROCESS-scoped holder must own the SPECIFIC target. An EMPTY target set → no PROCESS holder
    matches (only artifact/folder/SYSTEM), e.g. a CAPA stage whose CAPA carries no process."""
    base = await _record_base_scope(session, record_id)
    target_scope = dataclasses.replace(base, process_ids=frozenset(target_process_ids))
    await enforce(session, sink, request, caller, "record.create", target_scope)


async def _enforce_evidence_link_target(
    session: AsyncSession,
    sink: AuthzAuditSink,
    request: Request,
    caller: AppUser,
    record_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
) -> None:
    """Re-auth an evidence-link target (S-records-W; shared by add + remove). A Process-Owner's
    evidence-link write is restricted to **PROCESS targets they own** (the only target that mints a
    leg-A binding the records read gate honors): re-auth ``record.create`` over the target process.
    EVERY other target type — CLAUSE / DOCUMENT / FINDING / CAPA_STAGE — re-auths over an EMPTY
    process set, so a PROCESS-only holder is DENIED while a SYSTEM/ARTIFACT/FOLDER holder still
    passes (unchanged from pre-W). These cross-cutting compliance annotations (the CAPA-closure
    evidence gate — R3-2, the FINDING/DOCUMENT process surfaces — Codex W-CX-1) need broad
    authority, not a process-scoped grant; scoping a Process-Owner to a target's OWN process is a
    future refinement — deny is the converging safe floor."""
    target = (
        frozenset({str(target_id)})
        if target_type == EvidenceForTargetType.PROCESS.value
        else frozenset()
    )
    await _enforce_target_process_record(session, sink, request, caller, record_id, target)


async def _capture_scope(
    session: AsyncSession, caller: AppUser, source_document_id: uuid.UUID | None
) -> ResourceContext:
    """The ``record.create`` scope for a capture (S-rec-3 / [Fold 16], the S-pack-1 R28 lesson): a
    Mode-B record is bound to its source form template, so build the FULL ResourceContext from that
    template's framework + process-links + folder — a PROCESS/FOLDER-scoped ``record.create`` then
    authorizes correctly (a bare SYSTEM context would fail-closed mis-DENY it). Ad-hoc EVIDENCE (no
    source document) stays SYSTEM-only, as today (a SYSTEM override always matches)."""
    if source_document_id is None:
        return ResourceContext.system()
    doc = await session.get(DocumentedInformation, source_document_id)
    if doc is None or doc.org_id != caller.org_id:
        return ResourceContext.system()  # the service raises the real 404/422
    # #346: process_ids via the canonical satellite-aware loader (NOT list_process_links) so a
    # record captured against an objective source inherits its quality_objective.process_id
    # binding, and a PROCESS-scoped record.create DENY on that process participates (identical to
    # before for a form-template source — an objective binds its process on the satellite, not a
    # ProcessLink).
    return ResourceContext(
        kind="RECORD",
        folder_path=doc.folder_path,
        framework_id=str(doc.framework_id),
        process_ids=await vault_repo.process_ids_for_doc(session, doc.id),
    )


async def _load(
    session: AsyncSession, caller: AppUser, record_id: uuid.UUID
) -> tuple[Record, DocumentedInformation]:
    record = await records_repo.get_record(session, record_id)
    base = await records_repo.get_base(session, record_id)
    if record is None or base is None or record.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Record not found")
    return record, base


# S-records-W: BOTH the READ gate and the per-record binding-MINTING WRITE gate (correction +
# evidence-link add/remove) resolve the process-aware `_record_process_scope`, so a bound
# Process-Owner can read AND author records bound to their process; the writes additionally re-auth
# the TARGET process in-handler (`_enforce_target_process_record`).
_read = require("record.read", async_scope_resolver=_record_process_scope)
_create = require("record.create")  # SYSTEM scope (create/init-upload — no path id)
_create_scoped = require(  # per-record binding-minting writes (correction + evidence-link)
    "record.create", async_scope_resolver=_record_process_scope
)
# Disposition / legal-hold / dual-control destroy all gate on record.dispose (SoD-sensitive; doc 06
# §5.3, doc 15 §8.9). Resolve the FULL process-aware `_record_process_scope` (#335 Batch 2): the old
# partial scope carried only artifact_id + folder_path, so a kind / FRAMEWORK / PROCESS-scoped
# record.dispose DENY was silently dropped (deny-always-wins / R3 violated). The full tuple is both
# deny-wins-complete AND safe — record.dispose has NO process-scoped ALLOW (SYSTEM-override-only
# in v1), so including process_ids only ADDS DENY matches, never a new grant; and disposition mints
# no binding, so the S-records-W escalation channel (a writer minting a binding to gain access) does
# not exist on this path. SoD dual-control is unchanged.
_dispose = require("record.dispose", async_scope_resolver=_record_process_scope)


async def _retention_until_for(session: AsyncSession, record: Record) -> datetime.date | None:
    """The record's computed end-of-retention date (None = never expires / unknown basis / bad
    duration). Reads the snapshotted policy's duration against the frozen basis date."""
    policy = await records_repo.get_policy(session, record.retention_policy_id, record.org_id)
    if policy is None:
        return None
    try:
        return retention_until(record.retention_basis_date, policy.duration)
    except ValueError:
        return None


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
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    # Enforce in-handler (the create_document precedent): the record.create scope is derived from
    # the body's source template (a path-only dependency cannot see the body) so a process/folder-
    # scoped grant authorizes a Mode-B capture against its template ([Fold 16]).
    resource = await _capture_scope(session, caller, body.source_document_id)
    await enforce(session, authz_sink, request, caller, "record.create", resource)
    # S-records-W: a Mode-B capture inherits the source doc's processes (leg B), so re-enforce
    # record.create over EACH source process individually (mirror documents.create's per-process
    # loop, documents.py:800-824) — the base enforce matches if ANY source process is owned, so
    # without this a Process-Owner of P1 could mint a P1+P2-bound record under a shared doc. SYSTEM/
    # FOLDER holders still pass every iteration; a PROCESS holder must own EVERY source process.
    for pid in resource.process_ids:
        await enforce(
            session,
            authz_sink,
            request,
            caller,
            "record.create",
            dataclasses.replace(resource, process_ids=frozenset({pid})),
        )
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
    # S-records-R: batch-load each row's process binding so a bound Process-Owner's PROCESS-scoped
    # record.read matches (the decoupled read scope; the write gates stay process-blind). The R3-1
    # correction fallback runs only for the rare source-less corrected rows (empty union).
    process_ids_by_record = await records_repo.record_process_ids_for(session, [r for r, _ in rows])
    visible: list[tuple[Record, DocumentedInformation]] = []
    for record, base in rows:
        pids = process_ids_by_record.get(record.id) or set()
        if not pids and record.correction_of is not None:
            pids = await records_repo.record_process_ids_effective(session, record)
        resource = ResourceContext(
            artifact_id=str(record.id),
            kind="RECORD",
            folder_path=base.folder_path,
            framework_id=str(base.framework_id),
            process_ids=frozenset(pids),
        )
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


@router.get("/records/{record_id}/rendition")
async def get_rendition_endpoint(
    record_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Presign the structured-record PDF rendition (S-rec-3, doc 06 §4.2) — a derived, regenerable
    view of a Mode-B record's fielded data. 409 ``rendition_pending`` until the best-effort Stage-2
    build lands (or the record carries no structured values). Needs ``record.read``."""
    record, _base = await _load(session, caller, record_id)
    if record.structured_pdf_blob_sha256 is None:
        raise ProblemException(
            status=409,
            code="rendition_pending",
            title="No structured-record PDF rendition is available",
            detail="The rendition is still being generated, or the record is not structured.",
        )
    blob = await vault_repo.get_blob(session, record.structured_pdf_blob_sha256)
    if blob is None:  # pragma: no cover - defensive (the build sets the pointer + blob together)
        raise ProblemException(
            status=409, code="rendition_pending", title="Rendition not available"
        )
    url = await storage.presign_get(blob.object_key, bucket=blob.bucket)
    return {"download_url": url, "content_type": "application/pdf", "sha256": blob.sha256}


@router.post("/records/{record_id}/correction", status_code=status.HTTP_201_CREATED)
async def correction_endpoint(
    record_id: uuid.UUID,
    body: CorrectionCreate,
    request: Request,
    caller: AppUser = Depends(_create_scoped),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    # S-records-W: re-auth a correction over the UNION of the successor's new-source processes AND
    # the ORIGINAL's FULL effective binding, PER-PROCESS (own ALL; mirror capture; a single multi-
    # process scope would intersection-MATCH — Codex W-CX-1/3). The successor's effective source is
    # the original's OWN source when source-backed (``capture_correction`` FORCES it) else the body.
    # ⚠ Codex W round-4 P1: an earlier ``source_processes or effective(original)`` SHORT-CIRCUITED —
    # as soon as the successor had any source process it skipped the original's OTHER bindings, so a
    # P1-only owner could supersede a record co-bound to an unowned P2 (its source-doc P1 satisfied
    # the re-auth) and ``capture_correction`` (which does NOT carry the original's EvidenceForLinks)
    # minted a P1-only successor P2 owners can no longer read. The union forces the caller to own
    # EVERY process the original is currently bound to (leg A evidence + leg B source + the R3-1
    # correction walk) PLUS any new source — the converging deny-broader floor. The process-less-
    # source / source-less paths still resolve to the original's real binding (W-CX-2 / round-3: a
    # forced process-less source must NOT false-deny an owner of the original's real binding).
    # ⚠ Codex W round-5 P2 (TOCTOU): lock the Record row FOR UPDATE and HOLD it through
    # capture_correction's supersede (which re-acquires the same lock re-entrantly). The binding-
    # minting evidence-link writes now lock the SAME row, so a concurrent PROCESS link cannot commit
    # a new binding between this re-auth read and the supersede — without the lock a P1-only owner
    # could pass the union check while the record is P1-only and a P2 link lands before the cutover.
    # populate_existing refreshes the row the authz resolver may have cached into the identity map.
    original = (
        await session.execute(
            select(Record)
            .where(Record.id == record_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if original is not None:
        effective_source = (
            original.source_document_id
            if original.source_document_id is not None
            else body.source_document_id
        )
        source_processes: frozenset[str] = frozenset()
        if effective_source is not None:
            source_doc = await session.get(DocumentedInformation, effective_source)
            if source_doc is not None and source_doc.org_id == caller.org_id:
                # #346: canonical satellite-aware loader so an objective source contributes its
                # quality_objective.process_id to the deny-broader floor (identical for a normal
                # source — an objective binds its process on the satellite, not a ProcessLink).
                source_processes = await vault_repo.process_ids_for_doc(session, source_doc.id)
        inherited = source_processes | frozenset(
            await records_repo.record_process_ids_effective(session, original)
        )
        if inherited:
            for pid in inherited:
                await _enforce_target_process_record(
                    session, authz_sink, request, caller, record_id, frozenset({pid})
                )
        else:
            await _enforce_target_process_record(
                session, authz_sink, request, caller, record_id, frozenset()
            )
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
    request: Request,
    caller: AppUser = Depends(_create_scoped),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    await _enforce_evidence_link_target(
        session, authz_sink, request, caller, record_id, body.target_type, body.target_id
    )
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
    request: Request,
    caller: AppUser = Depends(_create_scoped),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> Response:
    # S-records-W: re-auth the target of the link being REMOVED (mirror the add) — the link's 404 +
    # the CAPA-freeze guard stay in unlink_evidence.
    link = await records_repo.get_evidence_link_by_id(session, link_id)
    if link is not None and link.record_id == record_id and link.org_id == caller.org_id:
        await _enforce_evidence_link_target(
            session, authz_sink, request, caller, record_id, link.target_type.value, link.target_id
        )
    await unlink_evidence(session, caller, record_id, link_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- disposition lifecycle (slice S-rec-2, doc 06 §5.3, doc 15 §8.9/§8.16) ---------------


@router.get("/records/{record_id}/disposition")
async def get_disposition_endpoint(
    record_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Disposition status + ``retention_until`` + ``legal_hold`` + the open destroy request + the
    tombstone history (doc 15 §8.16)."""
    record, _base = await _load(session, caller, record_id)
    until = await _retention_until_for(session, record)
    open_req = await records_repo.open_worm_destroy_request(session, record.id)
    events = await records_repo.list_disposition_events(session, record.id)
    return {
        "record_id": str(record.id),
        "disposition_state": record.disposition_state.value,
        "legal_hold": record.legal_hold,
        "retention_policy_id": str(record.retention_policy_id),
        "retention_basis_date": (
            record.retention_basis_date.isoformat() if record.retention_basis_date else None
        ),
        "retention_until": until.isoformat() if until else None,
        "open_worm_destroy_request": _worm_destroy_request(open_req) if open_req else None,
        "disposition_events": [_disposition_event(e) for e in events],
    }


@router.patch("/records/{record_id}/disposition")
async def advance_disposition_endpoint(
    record_id: uuid.UUID,
    body: DispositionAdvance,
    caller: AppUser = Depends(_dispose),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Advance ``disposition_state`` (ACTIVE↔DUE_FOR_REVIEW↔DISPOSED). A DESTROY physically removes
    the WORM bytes (fail-closed); blocked 409 while the lock is unexpired or a hold is active (the
    refusal is audited). Legal hold uses the dedicated endpoint."""
    record = await advance_disposition(
        session,
        caller,
        record_id,
        to_state=RecordDispositionState(body.to_state),
        reason=body.reason,
    )
    _, base = await _load(session, caller, record.id)
    return await _serialize_full(session, record, base)


@router.post("/records/{record_id}/legal-hold")
async def legal_hold_endpoint(
    record_id: uuid.UUID,
    body: LegalHoldAction,
    caller: AppUser = Depends(_dispose),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Place or release a legal hold — a preservation freeze that overrides retention expiry (doc 06
    §5.2). ``reason`` is mandatory (audit trail). Gated on ``record.dispose``."""
    if body.action == "place":
        record = await place_legal_hold(session, caller, record_id, reason=body.reason)
    else:
        record = await release_legal_hold(session, caller, record_id, reason=body.reason)
    _, base = await _load(session, caller, record.id)
    return await _serialize_full(session, record, base)


@router.get("/records/{record_id}/worm-destroy-requests")
async def list_worm_destroy_requests_endpoint(
    record_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _load(session, caller, record_id)
    reqs = await records_repo.list_worm_destroy_requests(session, record_id)
    return [_worm_destroy_request(r) for r in reqs]


@router.post("/records/{record_id}/worm-destroy-requests", status_code=status.HTTP_201_CREATED)
async def request_worm_destroy_endpoint(
    record_id: uuid.UUID,
    body: WormDestroyRequestCreate,
    caller: AppUser = Depends(_dispose),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """R27 dual-control destroy-under-legal-order — STEP 1 (request). A distinct second actor must
    approve before any WORM bytes are destroyed."""
    req = await request_worm_destroy(session, caller, record_id, legal_basis=body.legal_basis)
    return _worm_destroy_request(req)


@router.post("/records/{record_id}/worm-destroy-requests/{req_id}/approve")
async def approve_worm_destroy_endpoint(
    record_id: uuid.UUID,
    req_id: uuid.UUID,
    body: DispositionReason,
    caller: AppUser = Depends(_dispose),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """R27 dual-control destroy — STEP 2 (approve + execute). The approver must differ from the
    requester (409); the governance-bypass purge is fail-closed; COMPLIANCE mode is refused."""
    record = await approve_worm_destroy(session, caller, record_id, req_id, reason=body.reason)
    _, base = await _load(session, caller, record.id)
    return await _serialize_full(session, record, base)


@router.post("/records/{record_id}/worm-destroy-requests/{req_id}/cancel")
async def cancel_worm_destroy_endpoint(
    record_id: uuid.UUID,
    req_id: uuid.UUID,
    body: DispositionReason,
    caller: AppUser = Depends(_dispose),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Cancel an open dual-control destroy request (audited)."""
    req = await cancel_worm_destroy(session, caller, record_id, req_id, reason=body.reason)
    return _worm_destroy_request(req)
