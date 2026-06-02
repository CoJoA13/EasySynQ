"""The vault use-case layer: document creation + the check-out → upload → check-in cycle.

Orchestrates the domain (identifier), the object store (storage, presigned/WORM), the
check-out lock (locks, Redis), and the DB (repository) — and emits a vault audit event for
each action. Enforces the load-bearing S3 invariants: atomic identifier allocation, the Redis
exclusive lock (409 ``lock_conflict``), content-addressed dedup ("no change detected"), INV-3
(mandatory ``change_reason`` + ``change_significance``), and WORM-before-version-complete.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

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
from ...db.models.document_version import DocumentVersion as DocumentVersionModel
from ...db.models.documented_information import DocumentedInformation
from ...db.models.working_draft import WorkingDraft
from ...domain.vault import format_identifier, revision_label
from ...logging import request_id_var
from ...problems import ProblemException
from . import locks, repository, storage
from .audit import VaultAuditEvent, VaultAuditSink

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
        ),
    )


def _snapshot(doc: DocumentedInformation) -> dict[str, Any]:
    """Metadata as it was at check-in (doc 14 §5.3) — frozen onto the immutable version."""
    return {
        "identifier": doc.identifier,
        "title": doc.title,
        "document_type_id": str(doc.document_type_id) if doc.document_type_id else None,
        "owner_user_id": str(doc.owner_user_id),
        "folder_path": doc.folder_path,
        "classification": doc.classification.value,
        "framework_id": str(doc.framework_id),
    }


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
        created_by=actor.id,
    )
    session.add(doc)
    await session.flush()  # populate doc.id for the audit row's object_id
    _emit(session, sink, "DOCUMENT_CREATED", actor, "document", doc.id, identifier=doc.identifier)
    await session.commit()
    await session.refresh(doc)
    return doc


async def checkout(
    session: AsyncSession, sink: VaultAuditSink, actor: AppUser, doc: DocumentedInformation
) -> WorkingDraft:
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
    version = DocumentVersionModel(
        org_id=actor.org_id,
        document_id=doc.id,
        version_seq=seq,
        revision_label=revision_label(seq),
        change_significance=significance,
        change_reason=change_reason.strip(),
        version_state=VersionState.Draft,
        source_blob_sha256=sha256,
        metadata_snapshot=_snapshot(doc),
        author_user_id=actor.id,
        created_by=actor.id,
    )
    session.add(version)
    await session.delete(wd)
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
