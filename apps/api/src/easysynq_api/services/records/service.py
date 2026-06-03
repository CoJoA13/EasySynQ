"""The records use-case layer (S-rec-1, doc 06): immutable capture, correction, evidence-linking.

The load-bearing invariants (doc 06 §1.3): a record is **immutable post-capture** — no UPDATE path;
the only post-capture writes are advancing ``disposition_state`` (a later slice) and the
``superseded_by_correction`` pointer a correction flips (an audited annotation, not a content edit).
Capture is atomic: the base ``documented_information`` (kind=RECORD) row + the ``record`` subtype +
the WORM-sealed evidence blobs + the ``content_hash`` seal + the ``RECORD_CAPTURED`` audit row all
commit together. Records reuse the vault's blob/WORM/numbering primitives (the ``records`` bucket).

Records do NOT route through ``VaultAuditSink`` — its object-type map has no ``record`` entry — so
``emit_record_event`` adds the ``audit_event`` directly (the ``processes._emit_process_event``
pattern), ``object_type=record``, hashes NULL for the S6 linker.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models._record_enums import RecordType
from ...db.models._vault_enums import Classification, DocumentCurrentState, DocumentKind
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.blob import Blob
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_blob import EvidenceBlob
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.record import Record
from ...domain.records.content_hash import record_content_hash
from ...domain.records.retention import (
    PolicyCandidate,
    RetentionResolution,
    RetentionResolutionInput,
    resolve_retention,
)
from ...domain.vault import format_identifier
from ...logging import request_id_var
from ...problems import ProblemException
from ..vault import repository as vault_repo
from ..vault import storage
from . import repository as repo

_RECORD_TYPE_PREFIX = "REC"  # identifier {REC}-{AREA}-{SEQ}; record_type is the row discriminator


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def emit_record_event(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    record_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a record ``audit_event`` (object_type=record) BEFORE commit, so the mutation + its
    audit row commit atomically (doc 12 §4.4 / AC#6). Hashes NULL for the S6 linker."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.record,
            object_id=record_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


def _validation_error(field: str, code: str, message: str) -> ProblemException:
    return ProblemException(
        status=422,
        code="validation_error",
        title=message,
        errors=[{"field": field, "code": code, "message": message}],
    )


async def _load_record(
    session: AsyncSession, actor: AppUser, record_id: uuid.UUID, *, for_update: bool = False
) -> Record:
    if for_update:
        record = (
            await session.execute(select(Record).where(Record.id == record_id).with_for_update())
        ).scalar_one_or_none()
    else:
        record = await repo.get_record(session, record_id)
    if record is None or record.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Record not found")
    return record


# --- retention resolution ----------------------------------------------------------------


async def resolve_capture_retention(
    session: AsyncSession,
    org_id: uuid.UUID,
    record_type: str,
    *,
    override_policy_id: uuid.UUID | None,
    clause_ids: frozenset[str],
    process_ids: frozenset[str],
    captured_at: datetime.datetime,
) -> RetentionResolution:
    """Resolve + snapshot the applicable retention policy (doc 06 §5.1 precedence). The system
    default is ensure-created if missing (fresh-install ordering safety)."""
    system = await repo.ensure_default_policy(session, org_id)
    override: PolicyCandidate | None = None
    if override_policy_id is not None:
        policy = await repo.get_policy(session, override_policy_id, org_id)
        if policy is None:
            raise _validation_error("retention_policy_id", "not_found", "Unknown retention policy")
        override = PolicyCandidate(policy.id, policy.basis)
    rt = await repo.record_type_default_policy(session, org_id, record_type)
    cl = await repo.clause_default_policy(session, org_id, clause_ids)
    pr = await repo.process_default_policy(session, org_id, process_ids)
    return resolve_retention(
        RetentionResolutionInput(
            captured_at=captured_at,
            system_default=PolicyCandidate(system.id, system.basis),
            record_type_default=PolicyCandidate(rt.id, rt.basis) if rt else None,
            clause_default=PolicyCandidate(cl.id, cl.basis) if cl else None,
            process_default=PolicyCandidate(pr.id, pr.basis) if pr else None,
            override=override,
        )
    )


# --- capture -----------------------------------------------------------------------------


async def record_init_upload(
    session: AsyncSession, actor: AppUser, sha256: str, content_type: str
) -> dict[str, Any]:
    """Presign a PUT for an evidence blob into the plain ``staging`` bucket (capture promotes it to
    the WORM ``records`` bucket). Dedup only on an already records-bucket WORM-sealed blob (the same
    evidence on >1 record); bytes vaulted only in another bucket still need a fresh upload, since a
    record's evidence must be WORM-sealed in the records bucket (the capture-time fail-closed)."""
    existing = await vault_repo.get_blob(session, sha256)
    if (
        existing is not None
        and existing.worm_locked
        and existing.bucket == get_settings().s3_bucket_records
    ):
        return {"dedup": True, "object_key": existing.object_key, "upload_url": None}
    url = await storage.presign_put(sha256, content_type)
    return {"dedup": False, "object_key": sha256, "upload_url": url}


async def _attach_evidence(
    session: AsyncSession,
    actor: AppUser,
    record_id: uuid.UUID,
    evidence: Sequence[tuple[str, str]],
) -> list[str]:
    """WORM-seal each unique evidence blob into the records bucket + attach it (idempotent). Returns
    the de-duplicated, lowercased sha list (the content_hash manifest)."""
    settings = get_settings()
    seen: set[str] = set()
    shas: list[str] = []
    for raw_sha, content_type in evidence:
        sha256 = raw_sha.lower()
        if sha256 in seen:
            continue
        seen.add(sha256)
        blob = await vault_repo.get_blob(session, sha256)
        if blob is not None:
            # FAIL-CLOSED reuse (review fix): the global content-addressed Blob PK can't track the
            # same bytes in two buckets, so reuse ONLY a blob already WORM-sealed in the RECORDS
            # bucket (the legitimate "same evidence on >1 record" dedup). A blob in another bucket —
            # the WORM ``documents`` vault (a different retention domain) or, worse, the NON-WORM
            # ``renditions`` bucket — must never back a record's sealed evidence (doc 06 §4.4 / R3).
            # The operator uploads fresh evidence, or links to that document via evidence-for.
            if not (blob.worm_locked and blob.bucket == settings.s3_bucket_records):
                raise ProblemException(
                    status=423,
                    code="worm_required",
                    title="Evidence bytes are already vaulted outside the records bucket",
                    detail=(
                        "These exact bytes exist in another bucket and cannot back a record's "
                        "WORM-sealed evidence; upload fresh evidence, or link to that document via "
                        "POST /records/{id}/evidence-links."
                    ),
                )
        else:
            promoted = await storage.finalize_worm(sha256, bucket=storage._records_bucket())
            if not promoted.exists:
                raise _validation_error(
                    "evidence", "not_found", "Evidence object not found — upload via :init-upload"
                )
            if promoted.retain_until is None:
                raise ProblemException(
                    status=423, code="worm_required", title="Evidence object is not WORM-locked"
                )
            await session.execute(
                pg_insert(Blob)
                .values(
                    sha256=sha256,
                    org_id=actor.org_id,
                    size_bytes=promoted.size or 0,
                    mime_type=promoted.content_type or content_type,
                    bucket=settings.s3_bucket_records,
                    object_key=sha256,
                    worm_locked=True,
                    worm_retain_until=promoted.retain_until,
                )
                .on_conflict_do_nothing(index_elements=["sha256"])
            )
            await session.flush()
        await session.execute(
            pg_insert(EvidenceBlob)
            .values(
                org_id=actor.org_id,
                record_id=record_id,
                blob_sha256=sha256,
                is_original=True,
                content_type=content_type,
                created_by=actor.id,
            )
            .on_conflict_do_nothing(index_elements=["record_id", "blob_sha256"])
        )
        shas.append(sha256)
    await session.flush()
    return shas


async def capture_record(
    session: AsyncSession,
    actor: AppUser,
    *,
    record_type: str,
    title: str,
    classification: str = "Internal",
    area_code: str | None = None,
    source_document_id: uuid.UUID | None = None,
    source_version_id: uuid.UUID | None = None,
    evidence: Sequence[tuple[str, str]] = (),
    form_field_values: dict[str, Any] | None = None,
    retention_policy_id: uuid.UUID | None = None,
    _correction_of: uuid.UUID | None = None,
    _commit: bool = True,
) -> Record:
    """Capture an immutable record: base + subtype + WORM evidence + content_hash seal + audit, one
    commit. ``_correction_of``/``_commit`` are internal (the correction path captures the successor
    without committing, then flips the original's pointer in the same transaction)."""
    try:
        rtype = RecordType(record_type)
    except ValueError as exc:
        raise _validation_error("record_type", "invalid", "Unknown record_type") from exc
    try:
        klass = Classification(classification)
    except ValueError as exc:
        raise _validation_error("classification", "invalid", "Invalid classification") from exc

    framework = await vault_repo.get_framework(session, actor.org_id)
    if framework is None:
        raise ProblemException(status=422, code="validation_error", title="No framework configured")

    # R21: a record produced under a controlled document MUST pin the exact version; ad-hoc EVIDENCE
    # has neither. Validate the source document + the version-belongs-to-document + framework match.
    if source_document_id is not None:
        source_doc = await session.get(DocumentedInformation, source_document_id)
        if (
            source_doc is None
            or source_doc.org_id != actor.org_id
            or source_doc.kind != DocumentKind.DOCUMENT
        ):
            raise _validation_error("source_document_id", "not_found", "Source document not found")
        if source_doc.framework_id != framework.id:
            raise _validation_error(
                "source_document_id", "framework_mismatch", "Source document framework mismatch"
            )
        if source_version_id is None:
            raise _validation_error(
                "source_version_id",
                "source_version_required",
                "A record produced under a document must pin its version (R21)",
            )
        version = await session.get(DocumentVersion, source_version_id)
        if version is None or version.document_id != source_document_id:
            raise _validation_error(
                "source_version_id", "not_found", "Source version not found for that document"
            )
    elif source_version_id is not None:
        raise _validation_error(
            "source_version_id", "invalid", "source_version_id requires source_document_id"
        )

    captured_at = _now()
    resolution = await resolve_capture_retention(
        session,
        actor.org_id,
        rtype.value,
        override_policy_id=retention_policy_id,
        clause_ids=frozenset(),  # links are added after capture in S-rec-1 (clause/process tiers
        process_ids=frozenset(),  # apply once capture-with-links lands; resolver is ready for them)
        captured_at=captured_at,
    )

    area = area_code or "GEN"
    seq = await vault_repo.allocate_seq(session, actor.org_id, _RECORD_TYPE_PREFIX, area)
    identifier = format_identifier(_RECORD_TYPE_PREFIX, seq, area)
    base = DocumentedInformation(
        org_id=actor.org_id,
        framework_id=framework.id,
        kind=DocumentKind.RECORD,
        identifier=identifier,
        title=title,
        owner_user_id=actor.id,
        # A captured record is "in force" the instant it exists; Effective is the only doc state
        # that reads right for an immutable, already-final artifact. Safe against the R25 singleton
        # index because records set is_singleton=False AND document_type_id=None.
        current_state=DocumentCurrentState.Effective,
        is_singleton=False,
        classification=klass,
        area_code=area,
        created_by=actor.id,
    )
    session.add(base)
    await session.flush()  # populate base.id (the shared PK)

    record = Record(
        id=base.id,
        org_id=actor.org_id,
        record_type=rtype,
        captured_at=captured_at,
        captured_by=actor.id,
        source_document_id=source_document_id,
        source_version_id=source_version_id,
        form_field_values=form_field_values,
        retention_policy_id=resolution.policy_id,
        retention_basis_date=resolution.retention_basis_date,
        correction_of=_correction_of,
        content_hash=None,
    )
    session.add(record)
    await session.flush()

    shas = await _attach_evidence(session, actor, record.id, evidence)
    record.content_hash = record_content_hash(
        record_type=rtype.value,
        source_version_id=source_version_id,
        form_field_values=form_field_values,
        evidence_sha256s=shas,
    )

    emit_record_event(
        session,
        actor,
        EventType.RECORD_CAPTURED,
        record.id,
        after={
            "identifier": identifier,
            "record_type": rtype.value,
            "source_version_id": str(source_version_id) if source_version_id else None,
            "content_hash": record.content_hash,
            "evidence_count": len(shas),
            "retention_policy_id": str(resolution.policy_id),
            "retention_tier": resolution.tier,
            "correction_of": str(_correction_of) if _correction_of else None,
        },
    )
    if _commit:
        await session.commit()
        await session.refresh(record)
    return record


async def capture_correction(
    session: AsyncSession,
    actor: AppUser,
    original_id: uuid.UUID,
    *,
    record_type: str,
    title: str,
    classification: str = "Internal",
    area_code: str | None = None,
    source_document_id: uuid.UUID | None = None,
    source_version_id: uuid.UUID | None = None,
    evidence: Sequence[tuple[str, str]] = (),
    form_field_values: dict[str, Any] | None = None,
    retention_policy_id: uuid.UUID | None = None,
) -> Record:
    """Correct a record by capturing a NEW successor (correct, don't change — doc 06 §1.3). The
    original is flagged ``superseded_by_correction`` (the audited pointer write — never a content
    edit) and stays retrievable forever. 409 if it is already superseded."""
    original = await _load_record(session, actor, original_id, for_update=True)
    if original.superseded_by_correction is not None:
        raise ProblemException(
            status=409, code="conflict", title="Record already superseded by a correction"
        )
    new_record = await capture_record(
        session,
        actor,
        record_type=record_type,
        title=title,
        classification=classification,
        area_code=area_code,
        source_document_id=source_document_id,
        source_version_id=source_version_id,
        evidence=evidence,
        form_field_values=form_field_values,
        retention_policy_id=retention_policy_id,
        _correction_of=original.id,
        _commit=False,
    )
    original.superseded_by_correction = new_record.id
    emit_record_event(
        session,
        actor,
        EventType.RECORD_CORRECTED,
        original.id,
        before={"superseded_by_correction": None},
        after={"superseded_by_correction": str(new_record.id)},
    )
    await session.commit()
    await session.refresh(new_record)
    return new_record


# --- evidence-for links ------------------------------------------------------------------


async def link_evidence(
    session: AsyncSession,
    actor: AppUser,
    record_id: uuid.UUID,
    *,
    target_type: str,
    target_id: uuid.UUID,
    link_reason: str | None = None,
) -> EvidenceForLink:
    """Link a record as *evidence for* a clause / process / document (Mode-C, doc 06 §6). An audited
    annotation — never copies bytes. Framework-consistent (clause/doc of the record's framework)."""
    # Load the base (kind=RECORD) directly — it carries org + framework_id + is the 404 guard.
    base = await repo.get_base(session, record_id)
    if base is None or base.org_id != actor.org_id or base.kind != DocumentKind.RECORD:
        raise ProblemException(status=404, code="not_found", title="Record not found")
    try:
        ttype = EvidenceForTargetType(target_type)
    except ValueError as exc:
        raise _validation_error("target_type", "invalid", "Unknown target_type") from exc

    if ttype is EvidenceForTargetType.CLAUSE:
        clause = await vault_repo.get_clause(session, target_id)
        if clause is None:
            raise _validation_error("target_id", "not_found", "Clause not found")
        if clause.framework_id != base.framework_id:
            raise _validation_error(
                "target_id", "framework_mismatch", "Clause belongs to a different framework"
            )
    elif ttype is EvidenceForTargetType.PROCESS:
        process = await vault_repo.get_process(session, target_id)
        if process is None or process.org_id != actor.org_id:
            raise _validation_error("target_id", "not_found", "Process not found")
    elif ttype is EvidenceForTargetType.DOCUMENT:
        target = await session.get(DocumentedInformation, target_id)
        if target is None or target.org_id != actor.org_id or target.kind != DocumentKind.DOCUMENT:
            raise _validation_error("target_id", "not_found", "Document not found")
        if target.framework_id != base.framework_id:
            raise _validation_error(
                "target_id", "framework_mismatch", "Document belongs to a different framework"
            )
    else:  # finding / capa_stage — reserved enum values, no table yet (S-rec-1 API rejects them)
        raise _validation_error("target_type", "unsupported", "target_type not supported yet")

    if await repo.get_evidence_link(session, record_id, ttype, target_id) is not None:
        raise ProblemException(status=409, code="conflict", title="Evidence link already exists")
    link = EvidenceForLink(
        org_id=actor.org_id,
        record_id=record_id,
        target_type=ttype,
        target_id=target_id,
        link_reason=link_reason,
        created_by=actor.id,
    )
    session.add(link)
    try:
        await session.flush()  # the UNIQUE backstop for a concurrent duplicate link
    except IntegrityError:
        await session.rollback()
        raise ProblemException(
            status=409, code="conflict", title="Evidence link already exists"
        ) from None
    emit_record_event(
        session,
        actor,
        EventType.RECORD_EVIDENCE_LINKED,
        record_id,
        after={"target_type": ttype.value, "target_id": str(target_id)},
    )
    await session.commit()
    await session.refresh(link)
    return link


async def unlink_evidence(
    session: AsyncSession, actor: AppUser, record_id: uuid.UUID, link_id: uuid.UUID
) -> None:
    record = await _load_record(session, actor, record_id)
    link = await repo.get_evidence_link_by_id(session, link_id)
    if link is None or link.record_id != record.id or link.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Evidence link not found")
    before = {"target_type": link.target_type.value, "target_id": str(link.target_id)}
    await session.delete(link)
    emit_record_event(session, actor, EventType.RECORD_EVIDENCE_UNLINKED, record_id, before=before)
    await session.commit()
