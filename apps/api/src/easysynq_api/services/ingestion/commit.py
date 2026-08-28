"""Stage 7 — Commit into the vault (slice S-ing-5, doc 09 §10/§12.1/§13).

The capstone: turns a reviewed run's ``commit_ready`` keep-items (folded by
``review.fold_file_decisions``: included + kind-confirmed) into Effective ``Rev A`` controlled
documents
+ immutable Records, **item-by-item, each in its own transaction**, and **idempotently** (the
``import_commit_result`` ledger keyed UNIQUE(run_id, file_id) makes a re-commit a no-op). A per-item
failure isolates to that item (``result=failed``) — the run continues and resumes the remaining
queue
(§11.2); committed WORM items are never rolled back (§11.4).

Single-flight: each item's SUCCESS is recorded by an atomic ledger CLAIM (``INSERT … ON CONFLICT DO
UPDATE … WHERE result='failed' RETURNING id``) as the LAST write in its per-item txn — so two
concurrent workers (e.g. the reaper re-enqueues a slow run) commit each item exactly once: the
loser's insert blocks on the winner's uncommitted row, then loses the claim and rolls its half-built
rows back. No advisory lock needed; this makes ``reap_stalled_commits`` re-enqueue safe.

The import baseline is its OWN path (NOT vault create_document/checkin, which commit internally +
walk
the Draft→Approved→Effective FSM): a freshly imported document is brand-new, so the version is
created
DIRECTLY at ``version_state=Effective`` (INV-1 holds — no prior Effective to supersede; no
SERIALIZABLE
cutover needed) with a single ``signature_event(meaning=import_baseline)`` (R2). RECORD-kind items
reuse the S-rec ``capture_record`` (composed ``_commit=False``) with a cross-bucket evidence
promote.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import logging
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._ingestion_enums import ImportCommitResultStatus, ImportRunStatus
from ...db.models._record_enums import RecordType
from ...db.models._vault_enums import (
    ChangeSignificance,
    Classification,
    DocumentCurrentState,
    DocumentKind,
    VersionState,
)
from ...db.models.app_user import AppUser
from ...db.models.blob import Blob
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.import_classification import ImportClassification
from ...db.models.import_decision import ImportDecision
from ...db.models.import_file import ImportFile
from ...db.models.import_proposal_node import ImportProposalNode
from ...db.models.process_link import ProcessLink
from ...domain.ingestion.import_report import (
    CommittedItem,
    FailedItem,
    ImportReportData,
    render_import_report,
)
from ...domain.vault.identifier import format_identifier, parse_identifier, revision_label
from ...problems import ProblemException
from ..records import repository as records_repo
from ..records import service as records_svc
from ..records.service import EvidenceInput
from ..reports.checklist import compute_checklist
from ..vault import repository as vault_repo
from ..vault import storage, upload_rejection
from ..vault.mirror_sink import get_mirror_enqueue_sink
from ..vault.service import _snapshot
from ..vault.signature import SignatureEvent, get_vault_signature_sink
from ..vault.staged_identity import (
    IdentityRefusal,
    StagedObjectRef,
    StagedSourceChanged,
    StagedSourceUnavailable,
    StagingVersionRequired,
    StorageStage,
    StorageUnavailable,
    TargetIdentityConflict,
    UploadIdentityMismatch,
    WormNotApplied,
)
from . import repository as repo
from . import storage as ingestion_storage
from .review import EffectiveFileState, fold_file_decisions
from .service import _now, emit_import_event_system
from .source import FilesystemSourceProvider

logger = logging.getLogger("easysynq.ingestion")

# the Form/Template document_type — importing one is unsupported (it needs an authored schema)
_FRM_CODE = "FRM"
_RECORD_TYPE_VALUES = frozenset(rt.value for rt in RecordType)
_ITEM_COMMIT_REASONS = frozenset(
    {
        "blank_identifier",
        "context_register_import_unsupported",
        "evidence_bytes_already_vaulted",
        "form_template_import_unsupported",
        "interested_parties_register_import_unsupported",
        "no_staged_bytes",
        "owner_ambiguous",
        "owner_not_found",
        "restage_source_changed",
        "restage_source_unavailable",
        "risk_register_import_unsupported",
        "source_bytes_in_foreign_bucket",
        "staged_object_not_found",
        "unknown_document_type",
    }
)
_RESTAGEABLE_REASONS = frozenset(
    {
        "restage_source_unavailable",
        "upload_identity_digest_mismatch",
        "upload_identity_size_mismatch",
        "staged_source_missing",
        "staged_source_changed",
    }
)
_STORAGE_REASON_BY_STAGE = {
    StorageStage.STAGING_PUT: "storage_unavailable_staging_put",
    StorageStage.VERSIONING: "storage_unavailable_versioning",
    StorageStage.SOURCE_GET: "storage_unavailable_source_get",
    StorageStage.SOURCE_READ: "storage_unavailable_source_read",
    StorageStage.TARGET_HEAD: "storage_unavailable_target_head",
    StorageStage.TARGET_GET: "storage_unavailable_target_get",
    StorageStage.TARGET_READ: "storage_unavailable_target_read",
    StorageStage.COPY: "storage_unavailable_copy",
    StorageStage.RETENTION: "storage_unavailable_retention",
}


class _ItemCommitError(Exception):
    """A per-item commit failure with an honest, reportable reason (recorded as result=failed)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _LostRace(Exception):
    """A concurrent worker already committed this item (the ledger claim lost) — roll back this
    worker's half-built vault rows and treat it as a no-op (NOT a failure)."""


@dataclasses.dataclass(frozen=True, slots=True)
class _FailedRecordResult:
    won: bool
    audit_ref: upload_rejection.AuditEventRef | None = None


@dataclasses.dataclass(slots=True)
class _CommitAttempt:
    """Failure ownership generation plus any exact restage verified during this attempt."""

    expected_committed_at: datetime.datetime | None = None
    restage_requested: bool = False
    restaged_blob_uri: str | None = None


def _failure_reason(exc: BaseException) -> str:
    """Map item failures to the closed, identity-free import ledger vocabulary."""
    if isinstance(exc, _ItemCommitError):
        return exc.reason if exc.reason in _ITEM_COMMIT_REASONS else "internal_error"
    if isinstance(exc, StagingVersionRequired):
        return "staging_version_required"
    if isinstance(exc, UploadIdentityMismatch):
        return f"upload_identity_{exc.classification}"
    if isinstance(exc, StagedSourceUnavailable):
        return "staged_source_missing"
    if isinstance(exc, StagedSourceChanged):
        return "staged_source_changed"
    if isinstance(exc, StorageUnavailable):
        return _STORAGE_REASON_BY_STAGE.get(exc.stage, "internal_error")
    if isinstance(exc, WormNotApplied):
        return "worm_not_applied"
    if isinstance(exc, TargetIdentityConflict):
        return "target_identity_conflict"
    return "internal_error"


def _attempt_failure_reason(exc: BaseException, attempt: _CommitAttempt) -> str:
    """Keep an interrupted restage resumable without misclassifying later storage failures."""
    if (
        isinstance(exc, StorageUnavailable)
        and attempt.restage_requested
        and attempt.restaged_blob_uri is None
    ):
        return "restage_source_unavailable"
    return _failure_reason(exc)


async def _restage_source(file: ImportFile, source_root: str) -> StagedObjectRef:
    """Restage one confined source and change only its transient exact locator."""
    if file.sha256 is None:
        raise _ItemCommitError("restage_source_changed")
    stack = ExitStack()
    try:
        provider = FilesystemSourceProvider(Path(source_root))
        handle = stack.enter_context(provider.open_stream(file.rel_path))
    except (OSError, ValueError) as exc:
        stack.close()
        raise _ItemCommitError("restage_source_unavailable") from exc
    with stack:
        staged = await ingestion_storage.stage_stream(
            handle, content_type=file.mime_type or "application/octet-stream"
        )
    if staged.sha256 != file.sha256 or staged.size_bytes != file.size_bytes:
        raise _ItemCommitError("restage_source_changed")
    file.staged_blob_uri = staged.staged_blob_uri
    return staged.source


def _parse_import_source(file: ImportFile) -> StagedObjectRef:
    if file.sha256 is None:
        raise _ItemCommitError("no_staged_bytes")
    if file.staged_blob_uri is None:
        raise StagingVersionRequired
    return ingestion_storage.parse_staged_uri(
        file.staged_blob_uri,
        expected_sha256=file.sha256,
        content_type=file.mime_type or "application/octet-stream",
        expected_size=file.size_bytes,
    )


def _decided_by(decisions: list[ImportDecision]) -> str:
    """The §12.1 'engine-auto vs human-corrected' signal, from the raw decision rows (NOT the fold
    boolean — finding 16). A CORRECT decision means the human changed a dimension."""
    from ...db.models._ingestion_enums import ImportDecisionAction

    return (
        "human_corrected"
        if any(d.action is ImportDecisionAction.CORRECT for d in decisions)
        else "engine_confirmed"
    )


def _title_for(file: ImportFile) -> str:
    stem = Path(file.filename).stem
    return stem or file.filename or file.rel_path


def _provenance(
    file: ImportFile,
    run_id: uuid.UUID,
    classifier_version: str | None,
    cls: ImportClassification | None,
    decided_by: str,
) -> dict[str, Any]:
    return {
        "source_rel_path": file.rel_path,
        "source_sha256": file.sha256,
        "run_id": str(run_id),
        "classifier_version": classifier_version,
        "confidence": cls.type_conf if cls is not None else None,
        "decided_by": decided_by,
    }


async def _resolve_owner_user_id(
    session: AsyncSession,
    org_id: uuid.UUID,
    committer: AppUser,
    state: EffectiveFileState,
    snapshot_owner_id: str | None,
) -> uuid.UUID:
    """Materialize the folded owner without confusing authorship with ownership.

    A human-selected owner is load-bearing: unresolved or ambiguous references fail this item
    honestly instead of silently assigning the committer. Engine-proposed embedded-author hints are
    best-effort, so an unresolved hint retains the existing committer fallback. Initial commit
    preflight pins a validated human reference in the run's internal owner snapshot before review
    closes; lifecycle status changes after that decision do not rewrite the operator's choice.
    """
    if state.owner is None:
        return committer.id
    if snapshot_owner_id is not None:
        try:
            validated_id = uuid.UUID(snapshot_owner_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise _ItemCommitError("owner_not_found") from exc
        validated = await repo.get_import_owner_by_id(session, org_id, validated_id)
        # Status/is_guest were validated and row-locked at the review→commit boundary. Recheck only
        # immutable tenant/non-guest identity here: disabling/retiring the user after that boundary
        # must not strand the run.
        if validated is None:
            raise _ItemCommitError("owner_not_found")
        return validated.id
    candidates = await repo.resolve_import_owner_candidates(session, org_id, state.owner)
    if len(candidates) == 1:
        return candidates[0].id
    if state.owner_source == "human":
        reason = "owner_ambiguous" if candidates else "owner_not_found"
        raise _ItemCommitError(reason)
    return committer.id


async def run_commit(sm: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
    """The detached commit body. **Each item commits in its OWN fresh session/transaction**
    (per-item isolation — an item's failure never poisons the next), idempotent + single-flight via
    the ledger CLAIM. Entered with the run already in ``Committing`` (the API flips it there); a
    re-delivery / a non-Committing run is a no-op."""
    async with sm() as guard:
        run = await repo.get_run(guard, run_id, for_update=True)
        if run is None or run.status is not ImportRunStatus.COMMITTING:
            await guard.rollback()  # acks_late re-delivery of an absent/non-committing run → no-op
            return
        org_id = run.org_id
        committed_by = run.committed_by
        classifier_version = run.classifier_version
        source_root = run.source_root
        raw_owner_snapshot = run.commit_owner_snapshot
        owner_snapshot = dict(raw_owner_snapshot) if isinstance(raw_owner_snapshot, dict) else {}
        await guard.commit()  # release the run FOR UPDATE before the (long) per-item loop

    if committed_by is None:
        # A Committing run must carry its committer (the API sets it). Defensive: mark resumable.
        await _finalize_run(sm, run_id, committed=0, failed=0, error="missing_committer")
        return
    async with sm() as pre:
        committer = await pre.get(AppUser, committed_by)
        framework = await vault_repo.get_framework(pre, org_id)
        if committer is None or framework is None:
            await _finalize_run(sm, run_id, committed=0, failed=0, error="commit_preconditions")
            return
        framework_id = framework.id
        # R10: the caller-visible gate lives in start_import_commit (422 BEFORE any WORM write —
        # never accept a run under a promise this version does not keep). A flag set out-of-band
        # after that gate still reaches here, so log it and let the report record it.
        deferred = await _deferred_chain_families(pre, run_id)
        if deferred:
            logger.warning(
                "ingestion.commit.revision_chain_reconstruction_deferred",
                extra={
                    "extra_fields": {
                        "run_id": str(run_id),
                        "families": deferred,
                        "detail": "reconstruct_revision_chain is recorded but not materialized in "
                        "this version; only each family's effective member is imported",
                    }
                },
            )

    # Single-flight is the per-item ledger CLAIM (see claim_commit_result), not a lock — concurrent
    # workers are de-duplicated atomically per item.
    await _commit_items(
        sm,
        run_id,
        org_id,
        committer,
        framework_id,
        classifier_version,
        owner_snapshot,
        source_root,
    )


async def _commit_items(
    sm: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    org_id: uuid.UUID,
    committer: AppUser,
    framework_id: uuid.UUID,
    classifier_version: str | None,
    owner_snapshot: dict[str, str],
    source_root: str,
) -> None:
    async with sm() as reads:
        nodes = await repo.list_proposal_nodes(reads, run_id)
        rows = await repo.included_files_with_context(reads, run_id, classifier_version)
        file_by_id = {f.id: f for f, _e, _c in rows}
        cls_by_id = {f.id: c for f, _e, c in rows}
        all_decisions = await repo.list_decisions(reads, run_id)
    decisions_by_file: dict[uuid.UUID, list[ImportDecision]] = {}
    for d in all_decisions:  # newest-first already
        if d.file_id is not None:
            decisions_by_file.setdefault(d.file_id, []).append(d)

    for node in nodes:
        attempt = _CommitAttempt()
        try:
            # The whole per-node body (the pure preamble + the commit) is inside the try so a single
            # poison node is isolated as result=failed (per-item isolation) and the loop always
            # reaches _finalize — it never aborts the batch + strands the run in Committing.
            file = file_by_id.get(node.file_id)
            if file is None:
                continue  # a keep-item whose file is not an included row (shouldn't happen) — skip
            decisions = decisions_by_file.get(node.file_id, [])
            st = fold_file_decisions(decisions, node, cls_by_id.get(node.file_id))
            if not st.commit_ready:
                continue  # excluded/deferred — the fold wins over keep-item membership
            cls = cls_by_id.get(node.file_id)
            # Each item is its OWN session+transaction → no cross-item state, no reuse-after-error.
            async with sm() as s:
                existing = await repo.get_commit_result(s, run_id, node.file_id)
                attempt.expected_committed_at = (
                    existing.committed_at if existing is not None else None
                )
                if existing is not None and existing.result in (
                    ImportCommitResultStatus.SUCCESS,
                    ImportCommitResultStatus.NOOP,
                ):
                    continue  # idempotent: already committed (a resume / re-delivery)
                restage = bool(
                    existing is not None
                    and existing.result is ImportCommitResultStatus.FAILED
                    and existing.error in _RESTAGEABLE_REASONS
                )
                attempt.restage_requested = restage
                owner_user_id = await _resolve_owner_user_id(
                    s,
                    org_id,
                    committer,
                    st,
                    owner_snapshot.get(str(node.file_id)),
                )
                if st.kind == "DOCUMENT":
                    await _commit_document(
                        s,
                        run_id,
                        org_id,
                        committer,
                        owner_user_id,
                        framework_id,
                        file,
                        st,
                        cls,
                        node,
                        decisions,
                        classifier_version,
                        source_root=source_root,
                        restage=restage,
                        attempt=attempt,
                    )
                else:  # RECORD
                    await _commit_record(
                        s,
                        run_id,
                        org_id,
                        committer,
                        owner_user_id,
                        file,
                        st,
                        cls,
                        decisions,
                        classifier_version,
                        source_root=source_root,
                        restage=restage,
                        attempt=attempt,
                    )
                await s.commit()
        except _LostRace:
            pass  # a concurrent worker committed this item — the item session rolled back; no-op
        except IdentityRefusal as exc:
            reason = _failure_reason(exc)
            context = _import_rejection_context(org_id, cast(ImportFile, file))
            async with sm() as fs:
                recorded = await _record_failed(
                    fs,
                    run_id,
                    org_id,
                    node.file_id,
                    reason,
                    expected_committed_at=attempt.expected_committed_at,
                    rejection=(exc, context),
                )
            if recorded.won and recorded.audit_ref is not None:
                await _cleanup_rejected_import_source(exc, recorded.audit_ref)
                _log_item_failure(run_id, node.file_id, reason)
        except TargetIdentityConflict as exc:
            reason = _failure_reason(exc)
            context = _import_rejection_context(org_id, cast(ImportFile, file))
            async with sm() as fs:
                recorded = await _record_failed(
                    fs,
                    run_id,
                    org_id,
                    node.file_id,
                    reason,
                    expected_committed_at=attempt.expected_committed_at,
                    retained_staged_blob_uri=attempt.restaged_blob_uri,
                    rejection=(exc, context),
                )
            if recorded.won:
                upload_rejection.emit_upload_identity_metric(
                    metric="identity_mismatch",
                    operation="import_commit",
                    classification="target_identity_conflict",
                    domain=upload_rejection._failure_domain(exc),
                    stage="target_read",
                    outcome="retained",
                )
                _log_item_failure(run_id, node.file_id, reason)
        except Exception as exc:
            reason = _attempt_failure_reason(exc, attempt)
            if reason == "internal_error":
                logger.exception(
                    "ingestion.commit.item_internal_error",
                    extra={
                        "extra_fields": {
                            "run_id": str(run_id),
                            "file_id": str(node.file_id),
                        }
                    },
                )
            async with sm() as fs:  # a FRESH session for the failed-ledger write
                recorded = await _record_failed(
                    fs,
                    run_id,
                    org_id,
                    node.file_id,
                    reason,
                    expected_committed_at=attempt.expected_committed_at,
                    retained_staged_blob_uri=attempt.restaged_blob_uri,
                )
            if recorded.won:
                _log_item_failure(run_id, node.file_id, reason)

    # Terminal flip + the Import Report + the mirror enqueue.
    await _finalize(sm, run_id, org_id, committer, framework_id)


async def _commit_document(
    session: AsyncSession,
    run_id: uuid.UUID,
    org_id: uuid.UUID,
    committer: AppUser,
    owner_user_id: uuid.UUID,
    framework_id: uuid.UUID,
    file: ImportFile,
    st: EffectiveFileState,
    cls: ImportClassification | None,
    node: ImportProposalNode,
    decisions: list[ImportDecision],
    classifier_version: str | None,
    *,
    source_root: str,
    restage: bool,
    attempt: _CommitAttempt,
) -> None:
    settings = get_settings()
    sha = file.sha256
    if sha is None:
        raise _ItemCommitError("no_staged_bytes")
    blob = await vault_repo.get_blob(session, sha)
    # Revalidate the folded state at the write boundary. Older append-only decisions may predate
    # request validation, and must not promote bytes or mint a vault row with a blank identifier.
    if st.identifier is not None and not st.identifier.strip():
        raise _ItemCommitError("blank_identifier")
    if st.type_code is None:
        raise _ItemCommitError("unknown_document_type")
    dt_by_code = await repo.get_document_types_by_codes(session, org_id, {st.type_code})
    dt = dt_by_code.get(st.type_code)
    if dt is None:
        raise _ItemCommitError("unknown_document_type")
    if dt.code == _FRM_CODE:
        raise _ItemCommitError("form_template_import_unsupported")
    # S-risk-1: the Risk & Opportunity register is system-managed via /risks (single non-Obsolete
    # head, zero ProcessLinks). An import must not mint an RSK doc that _find_head would adopt
    # (Codex).
    if dt.code == "RSK":
        raise _ItemCommitError("risk_register_import_unsupported")
    # S-context-1: same for the Context register (system-managed via /context, single non-Obsolete
    # CTX head) — an import must not mint a CTX doc the context find_head would adopt.
    if dt.code == "CTX":
        raise _ItemCommitError("context_register_import_unsupported")
    # S-interested-parties-1: same for the Interested Parties register (system-managed via
    # /interested-parties, single non-Obsolete IPR head) — an import must not mint an IPR doc the
    # interested-parties find_head would adopt.
    if dt.code == "IPR":
        raise _ItemCommitError("interested_parties_register_import_unsupported")

    # Identifier: preserve the doc-code verbatim, else allocate a fresh {TYPE}-{AREA}-{SEQ}.
    legacy_identifier: str | None = None
    if st.identifier_collidable and st.identifier is not None:
        identifier = st.identifier
        area = parse_identifier(identifier).area_code or "GEN"
    else:
        area = "GEN"
        seq = await vault_repo.allocate_seq(session, org_id, dt.code, area)
        identifier = format_identifier(dt.code, seq, area)
        # Preserve the original source code as provenance when we had to allocate — but ONLY a REAL
        # code. Reaching this branch means the identifier was not collidable, so the only non-null
        # ``st.identifier`` here is the ``"{TYPE}-<new>"`` sentinel (``suggested_default``);
        # ``identifier_source is None`` implies ``identifier is None``. Persisting the sentinel put
        # garbage on documented_information.legacy_identifier — indexed into search (weight C) and
        # matched by vault_identifier_collisions, so it polluted provenance and could collide
        # across imports. The guard keeps the branch honest if a future non-collidable-but-real
        # source is ever added.
        if (
            st.identifier is not None
            and st.identifier != identifier
            and st.identifier_source not in (None, "suggested_default")
        ):
            legacy_identifier = st.identifier

    if blob is not None and (blob.bucket != settings.s3_bucket_documents or not blob.worm_locked):
        raise _ItemCommitError("source_bytes_in_foreign_bucket")

    # Promote the staged bytes into the documents WORM bucket (one server-side copy). Reuse an
    # existing
    # documents-bucket blob; refuse foreign-bucket bytes (the symmetric cross-kind sha collision).
    if blob is None:
        source = await _restage_source(file, source_root) if restage else _parse_import_source(file)
        if restage:
            attempt.restaged_blob_uri = file.staged_blob_uri
            await session.execute(
                update(ImportFile)
                .where(ImportFile.id == file.id)
                .values(staged_blob_uri=file.staged_blob_uri)
            )
        promoted = await storage.promote_worm(source, target_bucket=settings.s3_bucket_documents)
        await session.execute(
            pg_insert(Blob)
            .values(
                sha256=sha,
                org_id=org_id,
                size_bytes=promoted.size,
                mime_type=promoted.content_type or file.mime_type or "application/octet-stream",
                bucket=promoted.target_bucket,
                object_key=promoted.target_key,
                worm_locked=True,
                worm_retain_until=promoted.retain_until,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )
        await session.flush()
        # The global-sha insert may have lost to a concurrent records/renditions-domain writer.
        # Under READ COMMITTED this new statement sees the committed winner; never create the
        # immutable version from the stale pre-insert ``None`` assumption.
        blob = await vault_repo.get_blob(session, sha)
    if blob is None or blob.bucket != settings.s3_bucket_documents or not blob.worm_locked:
        raise _ItemCommitError("source_bytes_in_foreign_bucket")

    doc = DocumentedInformation(
        org_id=org_id,
        framework_id=framework_id,
        kind=DocumentKind.DOCUMENT,
        identifier=identifier,
        legacy_identifier=legacy_identifier,
        title=_title_for(file),
        document_type_id=dt.id,
        area_code=area,
        owner_user_id=owner_user_id,
        current_state=DocumentCurrentState.Effective,
        is_singleton=dt.is_singleton,
        classification=Classification.Internal,
        created_by=committer.id,
        import_provenance=_provenance(
            file, run_id, classifier_version, cls, _decided_by(decisions)
        ),
    )
    session.add(doc)
    await (
        session.flush()
    )  # populate doc.id (also raises 23505 on identifier/R25 collision → item-fail)

    version = DocumentVersion(
        org_id=org_id,
        document_id=doc.id,
        version_seq=1,
        revision_label=revision_label(1),
        change_significance=ChangeSignificance.MAJOR,
        change_reason="Imported baseline",
        version_state=VersionState.Effective,
        source_blob_sha256=sha,
        metadata_snapshot=_snapshot(doc),
        imported=True,
        effective_from=_now(),
        author_user_id=committer.id,
        created_by=committer.id,
    )
    session.add(version)
    await session.flush()
    doc.current_effective_version_id = version.id

    # Clause mappings (resolve the folded numbers → ids; skip unresolved) + process links.
    if st.clause_numbers:
        clause_ids = await repo.get_clauses_by_numbers(
            session, framework_id, set(st.clause_numbers)
        )
        for clause_id in clause_ids.values():
            await session.execute(
                pg_insert(ClauseMapping)
                .values(
                    org_id=org_id,
                    framework_id=framework_id,
                    clause_id=clause_id,
                    documented_information_id=doc.id,
                    created_by=committer.id,
                )
                .on_conflict_do_nothing(index_elements=["documented_information_id", "clause_id"])
            )
    if st.process_names:
        process_ids = await repo.get_processes_by_names(session, org_id, set(st.process_names))
        for process_id in process_ids.values():
            await session.execute(
                pg_insert(ProcessLink)
                .values(
                    org_id=org_id,
                    process_id=process_id,
                    documented_information_id=doc.id,
                    created_by=committer.id,
                )
                .on_conflict_do_nothing(index_elements=["process_id", "documented_information_id"])
            )

    # The import-baseline signature (R2) — binds to the source bytes; signer = the human committer.
    get_vault_signature_sink().record(
        session,
        SignatureEvent(
            org_id=org_id,
            signed_object_id=version.id,
            meaning="import_baseline",
            signer_user_id=committer.id,
            signed_object_type="document_version",
            content_digest=sha,
        ),
    )
    # Per-doc audit (AC#6): scope_ref=identifier so GET /documents/{id}/audit-events surfaces it.
    emit_import_event_system(
        session,
        org_id,
        EventType.IMPORT_ITEM_COMMITTED,
        run_id,
        object_type=AuditObjectType.document,
        object_id=doc.id,
        scope_ref=identifier,
        after={
            "file_id": str(file.id),
            "identifier": identifier,
            "kind": "DOCUMENT",
            "owner_user_id": str(owner_user_id),
        },
    )
    # The CLAIM is the last write — if a concurrent worker already committed this item, we lose it
    # and roll the whole item back (the doc/version/signature included). No duplicate, no seq leak.
    won = await repo.claim_commit_result(
        session,
        org_id=org_id,
        run_id=run_id,
        file_id=file.id,
        vault_document_id=doc.id,
        vault_version_id=version.id,
    )
    if not won:
        raise _LostRace


async def _commit_record(
    session: AsyncSession,
    run_id: uuid.UUID,
    org_id: uuid.UUID,
    committer: AppUser,
    owner_user_id: uuid.UUID,
    file: ImportFile,
    st: EffectiveFileState,
    cls: ImportClassification | None,
    decisions: list[ImportDecision],
    classifier_version: str | None,
    *,
    source_root: str,
    restage: bool,
    attempt: _CommitAttempt,
) -> None:
    settings = get_settings()
    sha = file.sha256
    if sha is None:
        raise _ItemCommitError("no_staged_bytes")
    blob = await vault_repo.get_blob(session, sha)
    if blob is not None and (blob.bucket != settings.s3_bucket_records or not blob.worm_locked):
        raise _ItemCommitError("evidence_bytes_already_vaulted")
    rtype = (
        st.type_code.upper()
        if st.type_code and st.type_code.upper() in _RECORD_TYPE_VALUES
        else RecordType.EVIDENCE.value
    )
    content_type = file.mime_type or "application/octet-stream"
    source = None
    if blob is None:
        source = await _restage_source(file, source_root) if restage else _parse_import_source(file)
        if restage:
            attempt.restaged_blob_uri = file.staged_blob_uri
            await session.execute(
                update(ImportFile)
                .where(ImportFile.id == file.id)
                .values(staged_blob_uri=file.staged_blob_uri)
            )
    try:
        rec = await records_svc.capture_record(
            session,
            committer,
            record_type=rtype,
            title=_title_for(file),
            evidence=[EvidenceInput(sha, content_type, source)],
            source_document_id=None,
            retention_policy_id=None,
            _commit=False,
        )
    except Exception as exc:
        # A cross-bucket sha collision (the bytes already vaulted elsewhere) surfaces as a 423 from
        # _attach_evidence; isolate it as an honest per-item failure (rare — exact-dup clustering
        # collapses same-sha files within a run).
        from ...problems import ProblemException

        if isinstance(exc, ProblemException) and exc.status == 423:
            raise _ItemCommitError("evidence_bytes_already_vaulted") from exc
        raise

    base = await repo.get_base(session, rec.id)
    if base is not None:
        base.owner_user_id = owner_user_id
        base.import_provenance = _provenance(
            file, run_id, classifier_version, cls, _decided_by(decisions)
        )
        base.legacy_identifier = file.rel_path
    emit_import_event_system(
        session,
        org_id,
        EventType.IMPORT_ITEM_COMMITTED,
        run_id,
        object_type=AuditObjectType.record,
        object_id=rec.id,
        scope_ref=base.identifier if base is not None else None,
        after={
            "file_id": str(file.id),
            "kind": "RECORD",
            "owner_user_id": str(owner_user_id),
        },
    )
    won = await repo.claim_commit_result(
        session,
        org_id=org_id,
        run_id=run_id,
        file_id=file.id,
        vault_document_id=rec.id,
        vault_version_id=None,
    )
    if not won:
        raise _LostRace


def _log_item_failure(run_id: uuid.UUID, file_id: uuid.UUID, reason: str) -> None:
    logger.warning(
        "ingestion.commit.item_failed",
        extra={
            "extra_fields": {
                "run_id": str(run_id),
                "file_id": str(file_id),
                "reason": reason,
            }
        },
    )


def _import_rejection_context(
    org_id: uuid.UUID, file: ImportFile
) -> upload_rejection.RejectionContext:
    return upload_rejection.RejectionContext(
        operation="import_commit",
        org_id=org_id,
        actor_id=None,
        actor_type=ActorType.system,
        scope_ref=file.rel_path,
        user_correctable=False,
    )


async def _cleanup_rejected_import_source(
    failure: IdentityRefusal, ref: upload_rejection.AuditEventRef
) -> None:
    """Delete only the audited refused version, with Task 3's bounded retry signal."""
    classification = upload_rejection._failure_classification(failure)
    domain = upload_rejection._failure_domain(failure)
    source = upload_rejection._source_from_failure(failure)
    locator = source.locator if hasattr(source, "locator") else source
    try:
        await storage.delete_staged_version(locator)
    except Exception:  # noqa: BLE001 -- cleanup failure must preserve the item refusal
        try:
            upload_rejection._enqueue_cleanup_retry(ref)
        except Exception:  # noqa: BLE001 -- bounded terminal signal; never broaden/retry inline
            upload_rejection.emit_upload_identity_metric(
                metric="cleanup_final_failure",
                operation="import_commit",
                classification=classification,
                domain=domain,
                stage="cleanup",
                outcome="publish_failed",
            )
        else:
            upload_rejection.emit_upload_identity_metric(
                metric="cleanup_retry",
                operation="import_commit",
                classification=classification,
                domain=domain,
                stage="cleanup",
                outcome="retry_scheduled",
            )
    else:
        upload_rejection.emit_upload_identity_metric(
            metric="cleanup_success",
            operation="import_commit",
            classification=classification,
            domain=domain,
            stage="cleanup",
            outcome="deleted",
        )
    upload_rejection.emit_upload_identity_metric(
        metric="identity_mismatch",
        operation="import_commit",
        classification=classification,
        domain=domain,
        stage="source_read",
        outcome="refused",
    )


async def _record_failed(
    session: AsyncSession,
    run_id: uuid.UUID,
    org_id: uuid.UUID,
    file_id: uuid.UUID,
    reason: str,
    *,
    expected_committed_at: datetime.datetime | None,
    retained_staged_blob_uri: str | None = None,
    rejection: tuple[IdentityRefusal | TargetIdentityConflict, upload_rejection.RejectionContext]
    | None = None,
) -> _FailedRecordResult:
    """Atomically record + audit an isolated failure after the item transaction rolled back."""
    won = await repo.record_failed_result(
        session,
        org_id=org_id,
        run_id=run_id,
        file_id=file_id,
        error=reason,
        expected_committed_at=expected_committed_at,
    )
    if not won:
        await session.rollback()
        return _FailedRecordResult(won=False)
    if retained_staged_blob_uri is not None:
        await session.execute(
            update(ImportFile)
            .where(ImportFile.id == file_id)
            .values(staged_blob_uri=retained_staged_blob_uri)
        )
    after: dict[str, Any] = {"file_id": str(file_id), "error": reason}
    if rejection is not None:
        failure, context = rejection
        after.update(upload_rejection.rejection_payload(failure, context))
    row = emit_import_event_system(
        session,
        org_id,
        EventType.IMPORT_ITEM_FAILED,
        run_id,
        after=after,
    )
    await session.flush()
    ref = upload_rejection.AuditEventRef(id=row.id, occurred_at=row.occurred_at)
    await session.commit()
    return _FailedRecordResult(won=True, audit_ref=ref)


async def _finalize(
    sm: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    org_id: uuid.UUID,
    committer: AppUser,
    framework_id: uuid.UUID,
) -> None:
    """Build + seal the §12.1 Import Report, flip the run terminal (Completed / PartiallyCommitted),
    then enqueue the mirror regen (post-commit, best-effort). ONE terminal txn; the report capture
    runs in a SAVEPOINT so its failure rolls back just the report (never strands the terminal)."""
    async with sm() as session:
        results = await repo.list_commit_results(session, run_id)
        committed = sum(1 for r in results if r.result is ImportCommitResultStatus.SUCCESS)
        failed = sum(1 for r in results if r.result is ImportCommitResultStatus.FAILED)

        run = await repo.get_run(session, run_id, for_update=True)
        if run is None or run.status is not ImportRunStatus.COMMITTING:
            await session.rollback()  # a cancel/peer won the race — respect it
            return

        # The Import Report (best-effort; a SAVEPOINT isolates a failure so the terminal flip still
        # commits — the report is regenerable evidence, never blocking).
        report_record_id: uuid.UUID | None = None
        try:
            async with session.begin_nested() as _report_savepoint:
                report_record_id = await _capture_report(
                    session, run, committer, results, committed, failed
                )
        except (IdentityRefusal, TargetIdentityConflict) as failure:
            # The savepoint context has finished rolling back the half-built Record/audit rows.
            # Only now may the fresh audit transaction record the generated-object refusal.
            try:
                await upload_rejection.reject_after_owner_rollback(
                    failure,
                    context=upload_rejection.RejectionContext(
                        operation="server_generated",
                        org_id=org_id,
                        actor_id=None,
                        actor_type=ActorType.system,
                        scope_ref=str(run_id),
                        user_correctable=False,
                    ),
                    rejection_sessionmaker=sm,
                )
            except ProblemException as rejection_problem:
                if (rejection_problem.status, rejection_problem.code) != (
                    503,
                    "storage_unavailable",
                ):
                    raise
            report_record_id = None
            logger.warning(
                "ingestion.commit.report_failed", extra={"extra_fields": {"run_id": str(run_id)}}
            )
        except Exception:  # noqa: BLE001 — savepoint rolled back; the outer txn stays usable
            report_record_id = None
            logger.warning(
                "ingestion.commit.report_failed", extra={"extra_fields": {"run_id": str(run_id)}}
            )

        final_status = (
            ImportRunStatus.COMPLETED if failed == 0 else ImportRunStatus.PARTIALLY_COMMITTED
        )
        run.status = final_status
        if report_record_id is not None:
            run.report_record_id = report_record_id
        run.counts = {**(run.counts or {}), "commit": {"committed": committed, "failed": failed}}
        if final_status is ImportRunStatus.COMPLETED:
            run.completed_at = _now()
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_COMPLETED
            if final_status is ImportRunStatus.COMPLETED
            else EventType.IMPORT_RUN_PARTIAL,
            run_id,
            before={"status": "Committing"},
            after={
                "status": final_status.value,
                "committed": committed,
                "failed": failed,
                "report_record_id": str(report_record_id) if report_record_id else None,
            },
        )
        await session.commit()
    # Post-commit (NOT in any txn): regenerate the read-only mirror from the now-Effective versions
    # + the _ImportReport/ section. Best-effort — the nightly reconcile backstops a dropped one.
    get_mirror_enqueue_sink().enqueue("import_commit")


async def _finalize_run(
    sm: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    committed: int,
    failed: int,
    error: str,
) -> None:
    """Flip a run that could not even start committing to PartiallyCommitted (resumable via re-POST
    once the precondition is fixed) — never leave it stuck in Committing."""
    async with sm() as session:
        run = await repo.get_run(session, run_id, for_update=True)
        if run is None or run.status is not ImportRunStatus.COMMITTING:
            await session.rollback()
            return
        run.status = ImportRunStatus.PARTIALLY_COMMITTED
        run.error = error
        run.counts = {**(run.counts or {}), "commit": {"committed": committed, "failed": failed}}
        emit_import_event_system(
            session,
            run.org_id,
            EventType.IMPORT_RUN_PARTIAL,
            run_id,
            after={"status": "PartiallyCommitted", "error": error},
        )
        await session.commit()


async def _deferred_chain_families(session: AsyncSession, run_id: uuid.UUID) -> list[str]:
    """Family keys that opted into the R10 ``reconstruct_revision_chain`` — which v1 does NOT
    materialize at commit. Surfaced in the import report (and logged at commit start) so the
    deferral is HONEST: the opt-in is stored on ``import_version_family`` for a future slice, while
    every member is imported as its own Effective document (the current-version-only default).
    Silently ignoring the flag would let an operator believe an approved revision history exists."""
    families = await repo.list_version_families(session, run_id)
    return [f.family_key for f in families if f.reconstruct_revision_chain]


async def _capture_report(
    session: AsyncSession,
    run: Any,
    committer: AppUser,
    results: Any,
    committed: int,
    failed: int,
) -> uuid.UUID:
    """Render the §12.1 markdown report, WORM-seal it as a RETAIN_PERMANENT EVIDENCE Record, and
    return the record id (the caller sets ``import_run.report_record_id``). Runs inside the terminal
    transaction (``capture_record(_commit=False)``)."""
    org_id = run.org_id
    rows = await repo.included_files_with_context(session, run.id, run.classifier_version)
    file_by_id = {f.id: f for f, _e, _c in rows}

    committed_items: list[CommittedItem] = []
    failed_items: list[FailedItem] = []
    all_decisions = await repo.list_decisions(session, run.id)
    decisions_by_file: dict[uuid.UUID, list[ImportDecision]] = {}
    for d in all_decisions:
        if d.file_id is not None:
            decisions_by_file.setdefault(d.file_id, []).append(d)

    for r in results:
        file = file_by_id.get(r.file_id)
        rel_path = file.rel_path if file is not None else str(r.file_id)
        if r.result is ImportCommitResultStatus.SUCCESS and r.vault_document_id is not None:
            doc = await repo.get_base(session, r.vault_document_id)
            committed_items.append(
                CommittedItem(
                    identifier=doc.identifier if doc is not None else "—",
                    kind=(doc.kind.value if doc is not None else "—"),
                    source_rel_path=rel_path,
                    decided_by=_decided_by(decisions_by_file.get(r.file_id, [])),
                )
            )
        elif r.result is ImportCommitResultStatus.FAILED:
            failed_items.append(FailedItem(source_rel_path=rel_path, error=r.error or "unknown"))

    star = await compute_checklist(session, org_id)
    md = render_import_report(
        ImportReportData(
            run_id=str(run.id),
            source_root=run.source_root,
            created_by=str(run.created_by),
            committed_by=str(run.committed_by) if run.committed_by else None,
            classifier_version=run.classifier_version,
            final_status="Completed" if failed == 0 else "PartiallyCommitted",
            counts=dict(run.counts or {}),
            committed=committed_items,
            failed=failed_items,
            star_coverage=star if isinstance(star, dict) else None,
            deferred_revision_chain_families=await _deferred_chain_families(session, run.id),
        )
    )
    md_bytes = md.encode("utf-8")
    md_sha = hashlib.sha256(md_bytes).hexdigest()
    report_source = await storage.put_staging_bytes(
        md_bytes,
        md_sha,
        content_type="text/markdown",
    )
    # Audit U7: the dedicated system-managed PERMANENT policy — the org-editable System Default
    # could be weakened to DESTROY, silently making the evidence-grade report disposable.
    permanent = await records_repo.ensure_import_report_policy(session, org_id)
    rec = await records_svc.capture_record(
        session,
        committer,
        record_type=RecordType.EVIDENCE.value,
        title=f"Import Report — {run.source_root} — {run.id}",
        evidence=[EvidenceInput(md_sha, "text/markdown", report_source)],
        retention_policy_id=permanent.id,
        _commit=False,
    )
    return rec.id
