"""The vault use-case layer: document creation + the check-out → upload → check-in cycle.

Orchestrates the domain (identifier), the object store (storage, presigned/WORM), the
check-out lock (locks, Redis), and the DB (repository) — and emits a vault audit event for
each action. Enforces the load-bearing S3 invariants: atomic identifier allocation, the Redis
exclusive lock (409 ``lock_conflict``), content-addressed dedup ("no change detected"), INV-3
(mandatory ``change_reason`` + ``change_significance``), and WORM-before-version-complete.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import re
import uuid
from collections.abc import Sequence
from typing import Any, Literal

import rfc8785
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._vault_enums import (
    ChangeSignificance,
    Classification,
    DocumentCurrentState,
    DocumentKind,
    VersionState,
)
from ...db.models.app_user import AppUser
from ...db.models.blob import Blob
from ...db.models.distribution_entry import DistributionEntry
from ...db.models.document_version import DocumentVersion as DocumentVersionModel
from ...db.models.documented_information import DocumentedInformation
from ...db.models.form_template import FormTemplate
from ...db.models.management_review import ManagementReview
from ...db.models.process import Process
from ...db.models.process_link import ProcessLink
from ...db.models.quality_objective import QualityObjective
from ...db.models.working_draft import WorkingDraft
from ...domain.records.form_schema import FieldError, validate_schema
from ...domain.vault import format_identifier, revision_label
from ...logging import request_id_var
from ...problems import ProblemException
from . import locks, repository, storage, watermark
from .audit import VaultAuditEvent, VaultAuditSink
from .review import REVIEW_PERIOD_DEFAULT_MONTHS

logger = logging.getLogger("easysynq.vault")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _release_after_commit(document_id: uuid.UUID, token: str) -> None:
    """Release the check-out lock AFTER a successful commit. A False CAS result means the 8h
    lock had lapsed and was retaken — log it rather than clobbering the new holder's lock."""
    if token and not await locks.release(document_id, token):
        logger.warning("vault.checkin: lock token no longer matched (lock had lapsed)")


def _emit(
    session: AsyncSession,
    sink: VaultAuditSink,
    event_type: str,
    actor: AppUser,
    obj_type: str,
    obj_id: uuid.UUID,
    *,
    identifier: str | None = None,
    reason: str | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append the vault ``audit_event`` row to ``session`` BEFORE its commit, so the row commits (or
    rolls back) atomically with the action it records (doc 12 §4.4 / AC#6)."""
    sink.record(
        session,
        VaultAuditEvent(
            occurred_at=_now(),
            event_type=event_type,
            actor_id=str(actor.id),
            org_id=str(actor.org_id),
            object_type=obj_type,
            object_id=str(obj_id),
            identifier=identifier,
            reason=reason,
            request_id=request_id_var.get(),
            after=after,
        ),
    )


def _snapshot(
    doc: DocumentedInformation,
    *,
    field_schema: dict[str, Any] | None = None,
    distribution: list[dict[str, Any]] | None = None,
    objective_commitment: dict[str, Any] | None = None,
    mgmt_review_minutes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Metadata as it was at check-in (doc 14 §5.3) — frozen onto the immutable version. Ordinary
    documents call this with no ``field_schema`` (the snapshot shape is unchanged). A Form/Template
    check-in (S-rec-3) passes the working ``field_schema`` so the version pins it (the Mode-B
    capture validator reads it from here, never the mutable ``form_template`` row). S-ack-1 (doc 04
    §6.1): the version self-describes its audience/ack policy — ``acknowledgement_required`` + the
    serialized distribution entries are frozen here (the metadata diff's SNAPSHOT_FIELDS allowlist
    deliberately excludes them in v1; revisiting is S-ack-2's call). S-obj-3 (clause 6.2): an OBJ
    check-in passes ``objective_commitment`` so the version pins the commitment dict."""
    snap: dict[str, Any] = {
        "identifier": doc.identifier,
        "title": doc.title,
        "document_type_id": str(doc.document_type_id) if doc.document_type_id else None,
        "owner_user_id": str(doc.owner_user_id),
        "folder_path": doc.folder_path,
        "classification": doc.classification.value,
        "framework_id": str(doc.framework_id),
        "review_period_months": doc.review_period_months,
        # S-ack-1 (doc 04 §6.1): the version self-describes its audience/ack policy.
        "acknowledgement_required": doc.acknowledgement_required,
        "distribution": distribution or [],
    }
    if field_schema is not None:
        snap["field_schema"] = field_schema
    # S-obj-3 (clause 6.2): the OBJ's versioned commitment is frozen here, the form_template
    # field_schema precedent — one optional kwarg, the shared body never branches on doc kind.
    if objective_commitment is not None:
        snap["objective_commitment"] = objective_commitment
    # S-mr-1 (clause 9.3): the Management Review's frozen minutes (compiled 9.3.2 inputs as-of +
    # the 9.3.3 decisions/outputs) — a NEW snapshot key (the FE detects the subject by snapshot-key
    # presence, so this must NOT reuse objective_commitment); the body never branches on doc kind.
    if mgmt_review_minutes is not None:
        snap["mgmt_review_minutes"] = mgmt_review_minutes
    return snap


async def _distribution_snapshot(
    session: AsyncSession, document_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The doc 04 §6.1 'Recipients + ack requirement' fold — serialized for metadata_snapshot."""
    rows = (
        (
            await session.execute(
                select(DistributionEntry)
                .where(DistributionEntry.document_id == document_id)
                .order_by(DistributionEntry.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "target_type": e.target_type.value,
            "target_id": str(e.target_id),
            "ack_required": e.ack_required,
        }
        for e in rows
    ]


async def create_document(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    *,
    title: str,
    document_type_id: uuid.UUID,
    area_code: str | None = None,
    folder_path: str | None = None,
    classification: str = "Internal",
    processes: Sequence[Process] = (),
) -> DocumentedInformation:
    dt = await repository.get_document_type(session, document_type_id)
    if dt is None or dt.org_id != actor.org_id:
        raise ProblemException(
            status=422, code="validation_error", title="Unknown document_type_id"
        )
    framework = await repository.get_framework(session, actor.org_id)
    if framework is None:
        raise ProblemException(status=422, code="validation_error", title="No framework configured")
    try:
        klass = Classification(classification)
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title="Invalid classification"
        ) from exc

    area = area_code or "GEN"
    seq = await repository.allocate_seq(session, actor.org_id, dt.code, area)
    identifier = format_identifier(dt.code, seq, area)
    doc = DocumentedInformation(
        org_id=actor.org_id,
        framework_id=framework.id,
        kind=DocumentKind.DOCUMENT,
        identifier=identifier,
        title=title,
        document_type_id=dt.id,
        area_code=area,
        owner_user_id=actor.id,
        folder_path=folder_path,
        current_state=DocumentCurrentState.Draft,
        is_singleton=dt.is_singleton,
        classification=klass,
        review_period_months=REVIEW_PERIOD_DEFAULT_MONTHS,
        created_by=actor.id,
    )
    session.add(doc)
    await session.flush()  # populate doc.id for the audit row's object_id
    _emit(session, sink, "DOCUMENT_CREATED", actor, "document", doc.id, identifier=doc.identifier)
    # S-process-scope-1: link the declared processes in the SAME txn so the doc + its ProcessLinks +
    # the PROCESS_LINKED audit commit atomically — a half-linked doc (created but unlinked) would be
    # invisible to a process-scoped owner who can no longer read it. The api layer already validated
    # each process (existence + org) and authorized the link (document.manage_metadata over the
    # target). The PROCESS_LINKED row carries identifier=doc.identifier so it lands on the
    # per-document audit trail, byte-compatible with the standalone link endpoint's event.
    for process in processes:
        session.add(
            ProcessLink(
                org_id=doc.org_id,
                process_id=process.id,
                documented_information_id=doc.id,
                created_by=actor.id,
            )
        )
        _emit(
            session,
            sink,
            "PROCESS_LINKED",
            actor,
            "document",
            doc.id,
            identifier=doc.identifier,
            after={"process_id": str(process.id), "process_name": process.name},
        )
    if processes:
        await session.flush()
    await session.commit()
    await session.refresh(doc)
    return doc


async def reject_objective_byte_path(session: AsyncSession, doc: DocumentedInformation) -> None:
    """S-obj-4 (O-5) / S-mr-1: a content-managed DOCUMENT subtype's content IS its frozen snapshot —
    a Quality Objective's commitment OR a Management Review's minutes — so the generic byte path
    (checkout/checkin) and the generic lifecycle writers (start-revision/submit-review, see
    api/documents.py) must not touch one: a byte-version would show the approver a stale snapshot,
    and a generic submit/release would advance a version around the content-aware freeze (and, for a
    review, skip the MR_ACTION spawn + ``close_state`` hook, leaving an Effective review that cannot
    be closed). Kind guard = satellite existence (the S-rec-1 posture); a PK probe. Reads stay open.
    (Name kept for the OBJ call sites; it now guards both subtypes — Codex #4.)"""
    if await session.get(QualityObjective, doc.id) is not None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Quality Objectives are managed via /objectives",
            errors=[
                {
                    "field": "document_id",
                    "code": "objective_managed_via_objectives",
                    "message": "use the /objectives lifecycle (edit/start-revision/submit-review)",
                }
            ],
        )
    if await session.get(ManagementReview, doc.id) is not None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Management Reviews are managed via /management-reviews",
            errors=[
                {
                    "field": "document_id",
                    "code": "management_review_managed_via_reviews",
                    "message": "use the /management-reviews lifecycle "
                    "(outputs/submit-review/release/close)",
                }
            ],
        )


async def checkout(
    session: AsyncSession, sink: VaultAuditSink, actor: AppUser, doc: DocumentedInformation
) -> WorkingDraft:
    await reject_objective_byte_path(session, doc)  # S-obj-4 O-5 — before the lock, deterministic
    token = await locks.acquire(doc.id)
    if token is None:
        existing = await repository.get_working_draft(session, doc.id)
        holder = str(existing.checked_out_by) if existing else "another user"
        raise ProblemException(
            status=409,
            code="lock_conflict",
            title="Document is checked out",
            detail=f"checked out by {holder}",
        )
    latest = await repository.latest_version(session, doc.id)
    wd = await repository.get_working_draft(session, doc.id)
    if wd is None:
        wd = WorkingDraft(
            org_id=actor.org_id,
            document_id=doc.id,
            checked_out_by=actor.id,
            source_version_id=latest.id if latest else None,
            lock_token=token,
        )
        session.add(wd)
    else:  # stale row from an expired lock — this acquirer takes over
        wd.checked_out_by = actor.id
        wd.source_version_id = latest.id if latest else None
        wd.lock_token = token
        wd.checked_out_at = _now()
    _emit(session, sink, "CHECKOUT", actor, "document", doc.id, identifier=doc.identifier)
    await session.commit()
    await session.refresh(wd)
    return wd


async def init_upload(
    session: AsyncSession,
    actor: AppUser,
    doc: DocumentedInformation,
    sha256: str,
    content_type: str,
) -> dict[str, Any]:
    # Record the in-progress scratch ref on the check-out mirror so break-lock preserves it (R9).
    wd = await repository.get_working_draft(session, doc.id)
    if wd is not None:
        wd.scratch_blob_ref = sha256
        await session.commit()
    existing = await repository.get_blob(session, sha256)
    if existing is not None:
        # storage-level dedup: the bytes are already vaulted, no upload needed.
        return {"dedup": True, "object_key": existing.object_key, "upload_url": None}
    url = await storage.presign_put(sha256, content_type)
    return {"dedup": False, "object_key": sha256, "upload_url": url}


async def checkin(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    sha256: str,
    change_reason: str,
    change_significance: str,
    mime_type: str = "application/octet-stream",
) -> tuple[DocumentVersionModel, bool]:
    await reject_objective_byte_path(session, doc)  # S-obj-4 O-5 — before WD check, deterministic
    wd = await repository.get_working_draft(session, doc.id)
    if wd is None or wd.checked_out_by != actor.id:
        raise ProblemException(
            status=409,
            code="lock_conflict",
            title="You do not hold the check-out for this document",
        )

    # INV-3: change_reason (non-empty) + change_significance (MAJOR|MINOR) are mandatory.
    if not change_reason or not change_reason.strip():
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires a change reason (INV-3)",
            errors=[{"field": "change_reason", "code": "required", "message": "must be non-empty"}],
        )
    try:
        significance = ChangeSignificance(change_significance)
    except ValueError as exc:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires change_significance MAJOR or MINOR (INV-3)",
            errors=[{"field": "change_significance", "code": "invalid", "message": "MAJOR|MINOR"}],
        ) from exc

    token = wd.lock_token or ""

    # Content-addressed dedup: identical bytes to the current latest version → no new version.
    latest = await repository.latest_version(session, doc.id)
    if latest is not None and latest.source_blob_sha256 == sha256:
        await session.delete(wd)
        _emit(session, sink, "NO_CHANGE", actor, "document", doc.id, identifier=doc.identifier)
        await session.commit()
        await _release_after_commit(doc.id, token)
        return latest, False

    # Promote the staged upload into the WORM documents bucket BEFORE the version commits.
    blob = await repository.get_blob(session, sha256)
    if blob is None:
        promoted = await storage.finalize_worm(sha256)
        if not promoted.exists:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="Uploaded object not found — upload via init-upload before check-in",
            )
        if promoted.retain_until is None:
            raise ProblemException(
                status=423, code="worm_required", title="Object is not WORM-locked"
            )
        # Dedup is GLOBAL (sha256 PK), so two check-ins of *different* documents with identical
        # bytes race here under different locks — ON CONFLICT DO NOTHING treats "already vaulted"
        # as the dedup success it is, instead of a 500 on the loser.
        await session.execute(
            pg_insert(Blob)
            .values(
                sha256=sha256,
                org_id=actor.org_id,
                size_bytes=promoted.size or 0,
                # Prefer the Content-Type MinIO recorded from the client PUT (drives S7b render
                # routing); fall back to the declared default. Set once on insert (content-hashed).
                mime_type=promoted.content_type or mime_type,
                bucket=get_settings().s3_bucket_documents,
                object_key=sha256,
                worm_locked=True,
                worm_retain_until=promoted.retain_until,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )
        await session.flush()

    # version_seq is allocated under the per-document check-out lock (the normal serializer);
    # UNIQUE(document_id, version_seq) is the hard backstop if a lapsed-and-retaken lock ever
    # lets two check-ins race (a rare, retriable conflict — the atomic per-doc counter is S4).
    seq = await repository.next_version_seq(session, doc.id)
    dist_snap = await _distribution_snapshot(session, doc.id)
    version = DocumentVersionModel(
        org_id=actor.org_id,
        document_id=doc.id,
        version_seq=seq,
        revision_label=revision_label(seq),
        change_significance=significance,
        change_reason=change_reason.strip(),
        version_state=VersionState.Draft,
        source_blob_sha256=sha256,
        metadata_snapshot=_snapshot(doc, distribution=dist_snap),
        author_user_id=actor.id,
        created_by=actor.id,
    )
    session.add(version)
    await session.delete(wd)
    # Flush BEFORE _emit: the uuid PK is a FLUSH-time default — a pending instance reads
    # version.id as None, which would persist the CHECKIN audit row with object_id=NULL
    # (the create_document precedent; doc 12 §4.4 wants the real version linkage).
    await session.flush()
    _emit(
        session,
        sink,
        "CHECKIN",
        actor,
        "document_version",
        version.id,
        identifier=doc.identifier,
        reason=change_reason.strip(),
    )
    await session.commit()  # version + audit commit atomically; release the lock only on success
    await session.refresh(version)
    await _release_after_commit(doc.id, token)
    return version, True


async def break_lock(
    session: AsyncSession, sink: VaultAuditSink, actor: AppUser, doc: DocumentedInformation
) -> None:
    """Release the lock WITHOUT check-in, preserving the displaced editor's scratch (R9)."""
    await locks.force_release(doc.id)
    # The working_draft row (and its scratch_blob_ref) is deliberately NOT deleted. LOCK_BROKEN has
    # no SQL state-change to be atomic with, so the audit row gets its own dedicated one-row commit
    # (the request session is clean here — break_lock issues no other SQL). doc 12 §4.4 carve-out.
    _emit(session, sink, "LOCK_BROKEN", actor, "document", doc.id, identifier=doc.identifier)
    await session.commit()


async def heartbeat(session: AsyncSession, actor: AppUser, doc: DocumentedInformation) -> int:
    """Refresh the holder's 8h check-out lock (R24) so a long edit does not lapse. Returns the
    new remaining TTL; 409 if the caller no longer holds the check-out."""
    wd = await repository.get_working_draft(session, doc.id)
    if wd is None or wd.checked_out_by != actor.id:
        raise ProblemException(
            status=409,
            code="lock_conflict",
            title="You do not hold the check-out for this document",
        )
    if not await locks.heartbeat(doc.id, wd.lock_token or ""):
        raise ProblemException(
            status=409, code="lock_conflict", title="The check-out lock has lapsed; check out again"
        )
    return await locks.ttl(doc.id)


# --- S7d: per-request export/print stamped rendition ------------------------------------

_EXPORT = "export"
_PRINT = "print"

# The download filename is interpolated into a Content-Disposition header, so keep it to a strict
# ASCII token (no quotes/semicolons/spaces/controls) — the identifier embeds the request-supplied
# area_code (and an admin-set type code), neither constrained to be header-safe.
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_pdf_filename(identifier: str, revision_label: str) -> str:
    stem = _FILENAME_UNSAFE.sub("_", f"{identifier}_{revision_label}").strip("_")
    return f"{stem or 'document'}.pdf"


# --- S-rec-3: Form/Template schema authoring + version resolution (doc 06 §4.2) ----------

_FRM_CODE = "FRM"  # the seeded Form/Template document_type code (0006_seed_vault)
_EDITABLE_STATES = frozenset({DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision})


def _schema_422(errors: list[FieldError]) -> ProblemException:
    return ProblemException(
        status=422,
        code="validation_error",
        title="Invalid form field schema",
        errors=[e.as_dict() for e in errors],
    )


async def _require_form_template_doc(
    session: AsyncSession, doc: DocumentedInformation, *, must_be_editable: bool
) -> None:
    """In-service guard (NOT an authz ``lifecycle_state`` predicate — the SYSTEM override that
    reaches a folderless FRM doc carries none): the doc must be a Form/Template (``kind=DOCUMENT``,
    ``document_type`` code ``FRM``); when ``must_be_editable`` also Draft/UnderRevision, so an
    Effective template's working schema can never be overwritten without a new version (doc 06 §4.2;
    AZ-INV-8). Mirrors the in-service FSM guards in ``lifecycle.py``."""
    dt = (
        await repository.get_document_type(session, doc.document_type_id)
        if doc.document_type_id
        else None
    )
    if doc.kind is not DocumentKind.DOCUMENT or dt is None or dt.code != _FRM_CODE:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Document is not a Form/Template",
            errors=[
                {
                    "field": "document_type",
                    "code": "not_form_template",
                    "message": "the document_type must be FRM (Form/Template)",
                }
            ],
        )
    if must_be_editable and doc.current_state not in _EDITABLE_STATES:
        raise ProblemException(
            status=409,
            code="not_editable",
            title="Edit a form template's schema only while Draft or UnderRevision",
            detail="Start a revision to author a new schema edition on an Effective template.",
        )


async def set_working_schema(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    field_schema: dict[str, Any],
) -> FormTemplate:
    """Set/replace a Form/Template's editable working ``field_schema`` (S-rec-3). Validates the
    schema *definition*, upserts the ``form_template`` row, audits FORM_SCHEMA_SET.
    Draft/UnderRevision only — a check-in then freezes it into a version (the pin for capture)."""
    await _require_form_template_doc(session, doc, must_be_editable=True)
    errors = validate_schema(field_schema)
    if errors:
        raise _schema_422(errors)
    ft = await repository.get_form_template(session, doc.id)
    if ft is None:
        ft = FormTemplate(id=doc.id, org_id=doc.org_id, field_schema=field_schema)
        session.add(ft)
    else:
        ft.field_schema = field_schema
    _emit(
        session,
        sink,
        "FORM_SCHEMA_SET",
        actor,
        "document",
        doc.id,
        identifier=doc.identifier,
        after={"field_count": len(field_schema.get("fields", []))},
    )
    await session.commit()
    await session.refresh(ft)
    return ft


async def get_working_schema(
    session: AsyncSession, doc: DocumentedInformation
) -> dict[str, Any] | None:
    ft = await repository.get_form_template(session, doc.id)
    return ft.field_schema if ft is not None else None


async def checkin_form_schema(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    change_reason: str,
    change_significance: str,
) -> DocumentVersionModel:
    """Freeze the working schema into an immutable ``DocumentVersion`` (S-rec-3). The controlled
    content of a Form/Template IS its schema: the canonical-serialized ``field_schema`` is the
    version's WORM source blob (server-side write — no client upload), and the SAME in-memory schema
    is pinned into ``metadata_snapshot`` in one transaction (so the bytes and the snapshot can never
    diverge). Then the standard submit-review → approve → release drives it Effective, unchanged."""
    await _require_form_template_doc(session, doc, must_be_editable=True)
    ft = await repository.get_form_template(session, doc.id)
    if ft is None or ft.field_schema is None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Set a form schema first via PUT /documents/{id}/form-schema",
        )
    schema = ft.field_schema
    errors = validate_schema(schema)  # defensive — set-time validated, but the seal must be sound
    if errors:
        raise _schema_422(errors)
    if not change_reason or not change_reason.strip():
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires a change reason (INV-3)",
            errors=[{"field": "change_reason", "code": "required", "message": "must be non-empty"}],
        )
    try:
        significance = ChangeSignificance(change_significance)
    except ValueError as exc:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires change_significance MAJOR or MINOR (INV-3)",
            errors=[{"field": "change_significance", "code": "invalid", "message": "MAJOR|MINOR"}],
        ) from exc

    payload = rfc8785.dumps(schema)  # JCS — deterministic; identical schema → identical source blob
    sha = hashlib.sha256(payload).hexdigest()
    if await repository.get_blob(session, sha) is None:
        await storage.put_staging_bytes(payload, sha, content_type="application/json")
        promoted = await storage.finalize_worm(sha)
        if not promoted.exists:  # pragma: no cover - defensive (we just wrote it)
            raise ProblemException(
                status=500, code="internal_error", title="Schema object upload failed"
            )
        if promoted.retain_until is None:
            raise ProblemException(
                status=423, code="worm_required", title="Schema object is not WORM-locked"
            )
        await session.execute(
            pg_insert(Blob)
            .values(
                sha256=sha,
                org_id=actor.org_id,
                size_bytes=promoted.size or len(payload),
                mime_type="application/json",
                bucket=get_settings().s3_bucket_documents,
                object_key=sha,
                worm_locked=True,
                worm_retain_until=promoted.retain_until,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )
        await session.flush()

    seq = await repository.next_version_seq(session, doc.id)
    dist_snap = await _distribution_snapshot(session, doc.id)
    version = DocumentVersionModel(
        org_id=actor.org_id,
        document_id=doc.id,
        version_seq=seq,
        revision_label=revision_label(seq),
        change_significance=significance,
        change_reason=change_reason.strip(),
        version_state=VersionState.Draft,
        source_blob_sha256=sha,
        # SAME schema object → bytes ≡ snapshot
        metadata_snapshot=_snapshot(doc, field_schema=schema, distribution=dist_snap),
        author_user_id=actor.id,
        created_by=actor.id,
    )
    session.add(version)
    # Flush BEFORE _emit — the uuid PK is a FLUSH-time default (see checkin); without it the
    # CHECKIN audit row persists object_id=NULL.
    await session.flush()
    _emit(
        session,
        sink,
        "CHECKIN",
        actor,
        "document_version",
        version.id,
        identifier=doc.identifier,
        reason=change_reason.strip(),
    )
    await session.commit()
    await session.refresh(version)
    return version


async def checkin_objective_commitment(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    commitment: dict[str, Any],
    change_reason: str,
    change_significance: str,
) -> DocumentVersionModel:
    """Freeze a Quality Objective's ``commitment`` (a pre-built JSON-safe dict) into an immutable
    ``DocumentVersion`` (S-obj-3 — the ``checkin_form_schema`` precedent). The canonical-serialized
    commitment is the version's WORM source blob (server-side write — no client upload,
    ``application/json`` → ``no_controlled_rendition`` R26), and the SAME dict is pinned into
    ``metadata_snapshot`` in one transaction. Unlike ``checkin_form_schema`` this FLUSHES (does not
    commit): the freeze is a sub-step of ``submit_objective_for_review``, which owns the
    submit/approval txn boundary."""
    if not change_reason or not change_reason.strip():
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires a change reason (INV-3)",
            errors=[{"field": "change_reason", "code": "required", "message": "must be non-empty"}],
        )
    try:
        significance = ChangeSignificance(change_significance)
    except ValueError as exc:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires change_significance MAJOR or MINOR (INV-3)",
            errors=[{"field": "change_significance", "code": "invalid", "message": "MAJOR|MINOR"}],
        ) from exc

    payload = rfc8785.dumps(commitment)  # JCS — identical commitment → identical source blob
    sha = hashlib.sha256(payload).hexdigest()
    if await repository.get_blob(session, sha) is None:
        await storage.put_staging_bytes(payload, sha, content_type="application/json")
        promoted = await storage.finalize_worm(sha)
        if not promoted.exists:  # pragma: no cover - defensive (we just wrote it)
            raise ProblemException(
                status=500, code="internal_error", title="Commitment object upload failed"
            )
        if promoted.retain_until is None:
            raise ProblemException(
                status=423, code="worm_required", title="Commitment object is not WORM-locked"
            )
        await session.execute(
            pg_insert(Blob)
            .values(
                sha256=sha,
                org_id=actor.org_id,
                size_bytes=promoted.size or len(payload),
                mime_type="application/json",
                bucket=get_settings().s3_bucket_documents,
                object_key=sha,
                worm_locked=True,
                worm_retain_until=promoted.retain_until,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )
        await session.flush()

    seq = await repository.next_version_seq(session, doc.id)
    dist_snap = await _distribution_snapshot(session, doc.id)
    version = DocumentVersionModel(
        org_id=actor.org_id,
        document_id=doc.id,
        version_seq=seq,
        revision_label=revision_label(seq),
        change_significance=significance,
        change_reason=change_reason.strip(),
        version_state=VersionState.Draft,
        source_blob_sha256=sha,
        # SAME commitment dict → bytes ≡ snapshot.
        metadata_snapshot=_snapshot(doc, objective_commitment=commitment, distribution=dist_snap),
        author_user_id=actor.id,
        created_by=actor.id,
    )
    session.add(version)
    # Flush BEFORE _emit — NOT commit (submit_objective_for_review owns the txn boundary). The
    # ``default=uuid.uuid4`` id is a FLUSH-time default (a pending instance reads ``id`` as None),
    # so the flush populates version.id for the audit row's object_id — the same flush-before-emit
    # contract as ``checkin`` and ``checkin_form_schema``.
    await session.flush()
    _emit(
        session,
        sink,
        "CHECKIN",
        actor,
        "document_version",
        version.id,
        identifier=doc.identifier,
        reason=change_reason.strip(),
    )
    return version


async def checkin_mgmt_review_minutes(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    minutes: dict[str, Any],
    change_reason: str,
    change_significance: str = "MAJOR",
) -> DocumentVersionModel:
    """Freeze a Management Review's ``minutes`` (a pre-built JSON-safe dict — the compiled 9.3.2
    inputs as-of + the 9.3.3 decisions/outputs) into an immutable ``DocumentVersion`` (S-mr-1,
    clause 9.3 — the ``checkin_objective_commitment`` precedent verbatim). The canonical-serialized
    minutes is the version's WORM source blob (server-side write — no client upload,
    ``application/json`` → ``no_controlled_rendition`` R26), and the SAME dict is pinned into
    ``metadata_snapshot`` under the NEW ``mgmt_review_minutes`` key (NOT ``objective_commitment`` —
    the FE detects the subject by snapshot-key presence). Like ``checkin_objective_commitment`` this
    FLUSHES (does not commit): the freeze is a sub-step of ``submit_review_for_review``, which owns
    the submit/approval txn boundary."""
    if not change_reason or not change_reason.strip():
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires a change reason (INV-3)",
            errors=[{"field": "change_reason", "code": "required", "message": "must be non-empty"}],
        )
    try:
        significance = ChangeSignificance(change_significance)
    except ValueError as exc:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires change_significance MAJOR or MINOR (INV-3)",
            errors=[{"field": "change_significance", "code": "invalid", "message": "MAJOR|MINOR"}],
        ) from exc

    payload = rfc8785.dumps(minutes)  # JCS, hashed BARE — NO preamble (a version source blob)
    sha = hashlib.sha256(payload).hexdigest()
    if await repository.get_blob(session, sha) is None:
        await storage.put_staging_bytes(payload, sha, content_type="application/json")
        promoted = await storage.finalize_worm(sha)
        if not promoted.exists:  # pragma: no cover - defensive (we just wrote it)
            raise ProblemException(
                status=500, code="internal_error", title="Minutes object upload failed"
            )
        if promoted.retain_until is None:
            raise ProblemException(
                status=423, code="worm_required", title="Minutes object is not WORM-locked"
            )
        await session.execute(
            pg_insert(Blob)
            .values(
                sha256=sha,
                org_id=actor.org_id,
                size_bytes=promoted.size or len(payload),
                mime_type="application/json",
                bucket=get_settings().s3_bucket_documents,
                object_key=sha,
                worm_locked=True,
                worm_retain_until=promoted.retain_until,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )
        await session.flush()

    seq = await repository.next_version_seq(session, doc.id)
    dist_snap = await _distribution_snapshot(session, doc.id)
    version = DocumentVersionModel(
        org_id=actor.org_id,
        document_id=doc.id,
        version_seq=seq,
        revision_label=revision_label(seq),
        change_significance=significance,
        change_reason=change_reason.strip(),
        version_state=VersionState.Draft,
        source_blob_sha256=sha,
        # SAME minutes dict → bytes ≡ snapshot.
        metadata_snapshot=_snapshot(doc, mgmt_review_minutes=minutes, distribution=dist_snap),
        author_user_id=actor.id,
        created_by=actor.id,
    )
    session.add(version)
    # Flush BEFORE _emit — NOT commit (submit_review_for_review owns the txn boundary). The
    # ``default=uuid.uuid4`` id is a FLUSH-time default (a pending instance reads ``id`` as None),
    # so the flush populates version.id for the audit row's object_id — the same flush-before-emit
    # contract as ``checkin_objective_commitment`` (the S-obj-3 object_id-None bug).
    await session.flush()
    _emit(
        session,
        sink,
        "CHECKIN",
        actor,
        "document_version",
        version.id,
        identifier=doc.identifier,
        reason=change_reason.strip(),
    )
    return version


def schema_from_version(version: DocumentVersionModel) -> dict[str, Any] | None:
    """The pinned ``field_schema`` from a version's immutable ``metadata_snapshot`` (None if absent
    — e.g. an ordinary document version). The Mode-B validator reads ONLY here (doc 06 §4.2)."""
    fs = (version.metadata_snapshot or {}).get("field_schema")
    return fs if isinstance(fs, dict) else None


async def resolve_template_version(
    session: AsyncSession, doc: DocumentedInformation, *, allow_pre_release: bool
) -> DocumentVersionModel:
    """Resolve the form-template version whose pinned schema a Mode-B capture (or the
    effective-form-schema read) validates against: the **Effective** version by default; the latest
    non-Obsolete version when ``allow_pre_release`` (the org toggle) and none is Effective. 422 when
    unresolved — never a crash on a freshly-created, never-checked-in template."""
    eff = await repository.effective_version(session, doc.id)
    if eff is not None:
        return eff
    if allow_pre_release:
        version = await repository.latest_non_obsolete_version(session, doc.id)
        if version is None:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="Form template has no resolvable version",
                errors=[
                    {
                        "field": "source_document_id",
                        "code": "no_resolvable_template_version",
                        "message": "the form template has no checked-in version yet",
                    }
                ],
            )
        return version
    raise ProblemException(
        status=422,
        code="validation_error",
        title="Form template is not Effective",
        errors=[
            {
                "field": "source_document_id",
                "code": "template_not_effective",
                "message": "the form template has no Effective version",
            }
        ],
    )


async def get_effective_schema(
    session: AsyncSession, doc: DocumentedInformation, *, allow_pre_release: bool
) -> dict[str, Any]:
    """The render-the-form read (doc 06 §4.2 ``GET /templates/{id}/effective-version``): resolve the
    in-force version + return its pinned schema. The same resolver capture uses, so the form the
    user fills is exactly the schema their record will validate + pin against."""
    await _require_form_template_doc(session, doc, must_be_editable=False)
    version = await resolve_template_version(session, doc, allow_pre_release=allow_pre_release)
    schema = schema_from_version(version)
    if schema is None:
        raise ProblemException(
            status=409,
            code="conflict",
            title="The resolved form-template version carries no field schema",
        )
    return {
        "source_version_id": str(version.id),
        "revision_label": version.revision_label,
        "version_state": version.version_state.value,
        "field_schema": schema,
    }


async def render_dynamic_copy(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    intent: Literal["export", "print"],
) -> tuple[bytes, str]:
    """Serve a FRESH, per-request stamped PDF of the document's Effective version (doc 04 §11.2,
    slice S7d) and audit the intent. Distinct from the mirror's cached, deterministic CONTROLLED
    COPY: it overlays a per-request banner + "{verb} {ts} by {user}" onto the cached rendition, so
    it carries a timestamp + actor and is therefore NEVER cached / content-addressed.

    ``intent="export"`` → an "UNCONTROLLED WHEN PRINTED — valid as of {date}" banner + an
    ``EXPORTED`` audit row; ``intent="print"`` → a "CONTROLLED COPY — valid on {date} only" banner +
    a ``PRINTED`` row. The permission gate (``document.export`` vs ``document.print_controlled``) is
    enforced by the endpoint's PEP dependency, not here.

    Reads the already-watermarked rendition bytes server-side (``storage.fetch_bytes``) and overlays
    in-process — the **one** place the api tier touches rendition bytes (it otherwise only presigns;
    D1). 404 if there is no Effective version; 409 ``no_controlled_rendition`` if the controlled PDF
    is unavailable (still rendering, or a non-renderable R26 format — the source is downloadable via
    ``/download`` instead). Emits the audit row + commits before returning the bytes."""
    if doc.current_effective_version_id is None:
        raise ProblemException(status=404, code="not_found", title="No effective version to render")
    version = await session.get(DocumentVersionModel, doc.current_effective_version_id)
    if version is None:  # pragma: no cover - defensive (the FK is set at the cutover)
        raise ProblemException(status=404, code="not_found", title="Effective version not found")
    rendition = (
        await repository.get_blob(session, version.rendition_blob_sha256)
        if version.rendition_blob_sha256 is not None
        else None
    )
    if rendition is None:
        # Pending or non-renderable (R26, doc 04 §11.4) are indistinguishable from the version row
        # alone (it carries no render-status), so one 409 covers both. The R26 "uncontrolled when
        # printed" warning rides in the ``notice`` member + the source stays downloadable via
        # /download (rendition:source); rendering the click-through page itself is the SPA's job
        # (deferred per the approved plan — this 409 carries everything it needs).
        raise ProblemException(
            status=409,
            code="no_controlled_rendition",
            title="No controlled PDF rendition is available yet",
            detail=(
                "The controlled-copy PDF is still being generated, or the source format is "
                "non-renderable (R26). Download the source via /documents/{id}/download."
            ),
            members={
                "notice": "UNCONTROLLED WHEN PRINTED — this source has no controlled rendition.",
                "source_download": f"/api/v1/documents/{doc.id}/download",
            },
        )
    base_pdf = await storage.fetch_bytes(rendition.object_key, bucket=rendition.bucket)

    now = _now()
    actor_label = actor.display_name or actor.email or str(actor.id)
    on_date = now.date().isoformat()
    stamped_at = now.isoformat(timespec="seconds")
    if intent == _EXPORT:
        banner = f"UNCONTROLLED WHEN PRINTED — valid as of {on_date}"
        footer_note = f"Exported {stamped_at} by {actor_label}"
        event_type, copy_status = "EXPORTED", "UNCONTROLLED IF PRINTED"
    else:
        banner = f"CONTROLLED COPY — valid on {on_date} only"
        footer_note = f"Printed {stamped_at} by {actor_label}"
        event_type, copy_status = "PRINTED", "CONTROLLED COPY"

    # Offload the CPU-bound reportlab/pypdf overlay off the event loop (mirrors the S7b sink's
    # asyncio.to_thread for stamp_controlled_copy). Done BEFORE _emit/commit so a stamping failure
    # raises before any audit row is written (no orphan row for an undelivered copy).
    stamped = await asyncio.to_thread(
        watermark.stamp_per_request_copy, base_pdf, banner=banner, footer_note=footer_note
    )

    _emit(
        session,
        sink,
        event_type,
        actor,
        "document_version",
        version.id,
        identifier=doc.identifier,
        after={"intent": intent, "copy_status": copy_status, "printed_by": actor_label},
    )
    await session.commit()

    return stamped, _safe_pdf_filename(doc.identifier, version.revision_label)
