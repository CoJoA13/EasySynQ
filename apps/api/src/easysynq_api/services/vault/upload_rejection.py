"""Durable rejection evidence and exact-version cleanup for staged uploads.

The ordering in this module is a safety boundary: owner rollback, fresh audit commit, then exact
staged-version deletion.  An audit failure never authorizes cleanup, and a target conflict always
retains its source for operator investigation.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid
from typing import Literal, NoReturn, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.audit_event import AuditEvent
from ...db.session import get_sessionmaker
from ...problems import ProblemException
from . import storage
from .staged_identity import (
    IdentityRefusal,
    PromotionResult,
    StagedObjectRef,
    StagedSourceChanged,
    StagedSourceUnavailable,
    StagedVersionLocator,
    StagingDomain,
    StagingVersionRequired,
    StorageStage,
    StorageUnavailable,
    TargetIdentityConflict,
    UploadIdentityMismatch,
    WormNotApplied,
)

logger = logging.getLogger("easysynq.upload_identity")

type UploadOperation = Literal[
    "document_checkin", "record_capture", "import_commit", "server_generated"
]
type MetricOperation = UploadOperation | Literal["unknown"]
type RejectionClassification = Literal[
    "digest_mismatch",
    "size_mismatch",
    "source_missing",
    "source_changed",
    "target_identity_conflict",
]
type MetricName = Literal[
    "identity_mismatch",
    "missing_version",
    "storage_failure",
    "cleanup_retry",
    "cleanup_success",
    "cleanup_final_failure",
]
type MetricDomain = Literal["staging", "import-staging", "documents", "records", "unknown", "none"]
type MetricClassification = Literal[
    "digest_mismatch",
    "size_mismatch",
    "source_missing",
    "source_changed",
    "target_identity_conflict",
    "none",
]
type MetricStage = Literal[
    "versioning",
    "staging_put",
    "source_get",
    "source_read",
    "target_head",
    "target_get",
    "target_read",
    "copy",
    "retention",
    "owner_rollback",
    "audit",
    "cleanup",
    "validation",
]
type MetricOutcome = Literal[
    "refused",
    "retained",
    "failed",
    "retry_scheduled",
    "deleted",
    "terminal",
    "publish_failed",
]

_METRICS = frozenset(
    {
        "identity_mismatch",
        "missing_version",
        "storage_failure",
        "cleanup_retry",
        "cleanup_success",
        "cleanup_final_failure",
    }
)
_OPERATIONS = frozenset(
    {"document_checkin", "record_capture", "import_commit", "server_generated", "unknown"}
)
_CLASSIFICATIONS = frozenset(
    {
        "digest_mismatch",
        "size_mismatch",
        "source_missing",
        "source_changed",
        "target_identity_conflict",
        "none",
    }
)
_DOMAINS = frozenset({"staging", "import-staging", "documents", "records", "unknown", "none"})
_STAGES = frozenset({stage.value for stage in StorageStage} | {"validation"})
_OUTCOMES = frozenset(
    {"refused", "retained", "failed", "retry_scheduled", "deleted", "terminal", "publish_failed"}
)


@dataclasses.dataclass(frozen=True, slots=True)
class RejectionContext:
    operation: UploadOperation
    org_id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_type: ActorType
    scope_ref: str | None
    user_correctable: bool


@dataclasses.dataclass(frozen=True, slots=True)
class AuditEventRef:
    id: int
    occurred_at: datetime.datetime


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def emit_upload_identity_metric(
    *,
    metric: MetricName,
    operation: MetricOperation,
    classification: MetricClassification,
    domain: MetricDomain,
    stage: MetricStage,
    outcome: MetricOutcome,
) -> None:
    """Emit the one fixed-schema signal; all dimensions are bounded enums, never identities."""
    values = (metric, operation, classification, domain, stage, outcome)
    allowed = (_METRICS, _OPERATIONS, _CLASSIFICATIONS, _DOMAINS, _STAGES, _OUTCOMES)
    if any(value not in choices for value, choices in zip(values, allowed, strict=True)):
        raise ValueError("upload identity metric dimension is outside its closed vocabulary")
    logger.info(
        "upload_identity.metric",
        extra={
            "extra_fields": {
                "metric": metric,
                "operation": operation,
                "classification": classification,
                "domain": domain,
                "stage": stage,
                "outcome": outcome,
                "count": 1,
            }
        },
    )


def _source_from_failure(failure: IdentityRefusal) -> StagedObjectRef | StagedVersionLocator:
    if isinstance(failure, (UploadIdentityMismatch, StagedSourceChanged)):
        return failure.source
    if isinstance(failure, StagedSourceUnavailable):
        return failure.source
    raise TypeError("unsupported identity refusal")


def _source_classification(failure: IdentityRefusal) -> RejectionClassification:
    if isinstance(failure, UploadIdentityMismatch):
        return failure.classification
    if isinstance(failure, StagedSourceUnavailable):
        return "source_missing"
    if isinstance(failure, StagedSourceChanged):
        return "source_changed"
    raise TypeError("unsupported identity refusal")


def _source_payload(failure: IdentityRefusal, context: RejectionContext) -> dict[str, object]:
    source = _source_from_failure(failure)
    if isinstance(source, StagedObjectRef):
        locator = source.locator
        expected_sha256 = source.expected_sha256
        expected_size = source.expected_size
    else:
        locator = source
        expected_sha256 = source.object_key
        expected_size = None
    mismatch = failure if isinstance(failure, UploadIdentityMismatch) else None
    if mismatch is not None:
        expected_sha256 = mismatch.expected_sha256
        expected_size = mismatch.expected_size
    return {
        "operation": context.operation,
        "classification": _source_classification(failure),
        "source": {
            "bucket": locator.domain.value,
            "object_key": locator.object_key,
            "version_id": locator.version_id,
            "etag": mismatch.etag if mismatch is not None else None,
        },
        "expected": {
            "sha256": expected_sha256,
            "size_bytes": expected_size,
        },
        "observed": {
            "sha256": mismatch.observed_sha256 if mismatch is not None else None,
            "size_bytes": mismatch.observed_size if mismatch is not None else None,
        },
        "cleanup": {"policy": "delete_exact_version_after_audit"},
    }


def _target_domain(bucket: str) -> MetricDomain:
    settings = get_settings()
    if bucket == settings.s3_bucket_documents:
        return "documents"
    if bucket == settings.s3_bucket_records:
        return "records"
    return "unknown"


def _target_payload(
    failure: TargetIdentityConflict, context: RejectionContext
) -> dict[str, object]:
    return {
        "operation": context.operation,
        "classification": "target_identity_conflict",
        "target": {
            "bucket": _target_domain(failure.target_bucket),
            "object_key": failure.target_key,
        },
        "expected": {
            "sha256": failure.source.expected_sha256,
            "size_bytes": failure.source.expected_size,
        },
        "observed": {
            "sha256": failure.observed_sha256,
            "size_bytes": failure.observed_size,
        },
        "cleanup": {"policy": "retain_source_operator_investigation"},
    }


def rejection_payload(
    failure: IdentityRefusal | TargetIdentityConflict, context: RejectionContext
) -> dict[str, object]:
    if isinstance(failure, TargetIdentityConflict):
        return _target_payload(failure, context)
    return _source_payload(failure, context)


class DbUploadRejectionSink:
    """Persist one rejection event in a fresh, short transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        context: RejectionContext,
        failure: IdentityRefusal | TargetIdentityConflict,
    ) -> AuditEventRef:
        occurred_at = _utc_now()
        is_generated = context.operation == "server_generated"
        actor_type = ActorType.system if is_generated else context.actor_type
        actor_id = None if is_generated or actor_type is ActorType.system else context.actor_id
        row = AuditEvent(
            org_id=context.org_id,
            occurred_at=occurred_at,
            actor_id=actor_id,
            actor_type=actor_type,
            event_type=EventType.BLOB_INTEGRITY_FAILED,
            object_type=AuditObjectType.config,
            object_id=context.org_id,
            scope_ref=context.scope_ref,
            after=rejection_payload(failure, context),
        )
        session_factory = self._session_factory or get_sessionmaker()
        async with session_factory() as session:
            session.add(row)
            await session.flush()
            await session.commit()
        return AuditEventRef(id=row.id, occurred_at=occurred_at)


def _problem(
    status: int,
    code: Literal["staged_source_unavailable", "storage_unavailable", "upload_identity_mismatch"],
    title: str,
) -> NoReturn:
    raise ProblemException(status=status, code=code, title=title)


def _raise_public_refusal(failure: IdentityRefusal, context: RejectionContext) -> NoReturn:
    if context.operation == "server_generated" or not context.user_correctable:
        _problem(503, "storage_unavailable", "Storage is unavailable")
    if isinstance(failure, UploadIdentityMismatch):
        _problem(422, "upload_identity_mismatch", "Upload identity did not match")
    _problem(409, "staged_source_unavailable", "Staged upload is no longer available")


def _failure_domain(
    failure: IdentityRefusal | TargetIdentityConflict,
) -> MetricDomain:
    if isinstance(failure, TargetIdentityConflict):
        return _target_domain(failure.target_bucket)
    source = _source_from_failure(failure)
    domain = source.locator.domain if isinstance(source, StagedObjectRef) else source.domain
    return domain.value


def _failure_classification(
    failure: IdentityRefusal | TargetIdentityConflict,
) -> MetricClassification:
    if isinstance(failure, TargetIdentityConflict):
        return "target_identity_conflict"
    return _source_classification(failure)


def _enqueue_cleanup_retry(ref: AuditEventRef) -> None:
    from ...tasks.upload_identity import cleanup_rejected

    cleanup_rejected.apply_async(args=(ref.id, ref.occurred_at.isoformat()))


async def reject_after_owner_rollback(
    failure: IdentityRefusal | TargetIdentityConflict,
    *,
    context: RejectionContext,
    rejection_sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> NoReturn:
    classification = _failure_classification(failure)
    domain = _failure_domain(failure)
    try:
        ref = await DbUploadRejectionSink(rejection_sessionmaker).record(context, failure)
    except Exception:  # noqa: BLE001 -- any audit failure must fail closed before cleanup
        emit_upload_identity_metric(
            metric="storage_failure",
            operation=context.operation,
            classification=classification,
            domain=domain,
            stage="audit",
            outcome="failed",
        )
        _problem(503, "storage_unavailable", "Storage is unavailable")

    if isinstance(failure, TargetIdentityConflict):
        emit_upload_identity_metric(
            metric="identity_mismatch",
            operation=context.operation,
            classification=classification,
            domain=domain,
            stage="target_read",
            outcome="retained",
        )
        _problem(503, "storage_unavailable", "Storage is unavailable")

    locator = _source_from_failure(failure)
    exact_locator = locator.locator if isinstance(locator, StagedObjectRef) else locator
    try:
        await storage.delete_staged_version(exact_locator)
    except Exception:  # noqa: BLE001 -- cleanup outages must preserve the original refusal
        try:
            _enqueue_cleanup_retry(ref)
        except Exception:  # noqa: BLE001 -- broker failure cannot replace the public refusal
            emit_upload_identity_metric(
                metric="cleanup_final_failure",
                operation=context.operation,
                classification=classification,
                domain=domain,
                stage="cleanup",
                outcome="publish_failed",
            )
        else:
            emit_upload_identity_metric(
                metric="cleanup_retry",
                operation=context.operation,
                classification=classification,
                domain=domain,
                stage="cleanup",
                outcome="retry_scheduled",
            )
    else:
        emit_upload_identity_metric(
            metric="cleanup_success",
            operation=context.operation,
            classification=classification,
            domain=domain,
            stage="cleanup",
            outcome="deleted",
        )
    emit_upload_identity_metric(
        metric="identity_mismatch",
        operation=context.operation,
        classification=classification,
        domain=domain,
        stage="source_read",
        outcome="refused",
    )
    _raise_public_refusal(failure, context)


async def promote_for_owner(
    owner_session: AsyncSession,
    source: StagedObjectRef,
    *,
    target_bucket: str,
    context: RejectionContext,
    rejection_sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> PromotionResult:
    try:
        return await storage.promote_worm(source, target_bucket=target_bucket)
    except (IdentityRefusal, TargetIdentityConflict, StorageUnavailable, WormNotApplied) as failure:
        try:
            await owner_session.rollback()
        except Exception:  # noqa: BLE001 -- an untrustworthy owner transaction forbids evidence
            classification: MetricClassification = (
                _failure_classification(failure)
                if isinstance(failure, (IdentityRefusal, TargetIdentityConflict))
                else "none"
            )
            domain: MetricDomain = (
                _failure_domain(failure)
                if isinstance(failure, (IdentityRefusal, TargetIdentityConflict))
                else source.locator.domain.value
            )
            emit_upload_identity_metric(
                metric="storage_failure",
                operation=context.operation,
                classification=classification,
                domain=domain,
                stage="owner_rollback",
                outcome="failed",
            )
            _problem(503, "storage_unavailable", "Storage is unavailable")

        if isinstance(failure, (IdentityRefusal, TargetIdentityConflict)):
            await reject_after_owner_rollback(
                failure,
                context=context,
                rejection_sessionmaker=rejection_sessionmaker,
            )
        stage = failure.stage.value if isinstance(failure, StorageUnavailable) else "retention"
        emit_upload_identity_metric(
            metric="storage_failure",
            operation=context.operation,
            classification="none",
            domain=source.locator.domain.value,
            stage=cast(MetricStage, stage),
            outcome="retained",
        )
        _problem(503, "storage_unavailable", "Storage is unavailable")


def require_staging_ref(
    *,
    domain: StagingDomain,
    sha256: str,
    version_id: str | None,
    content_type: str,
    operation: UploadOperation,
    expected_size: int | None = None,
) -> StagedObjectRef:
    if version_id is None or not version_id or version_id == "null":
        emit_upload_identity_metric(
            metric="missing_version",
            operation=operation,
            classification="none",
            domain=domain.value,
            stage="validation",
            outcome="refused",
        )
        raise ProblemException(
            status=422,
            code="staging_version_required",
            title="A staging version is required",
        )
    try:
        locator = StagedVersionLocator(domain=domain, object_key=sha256, version_id=version_id)
    except StagingVersionRequired:
        emit_upload_identity_metric(
            metric="missing_version",
            operation=operation,
            classification="none",
            domain=domain.value,
            stage="validation",
            outcome="refused",
        )
        raise ProblemException(
            status=422,
            code="staging_version_required",
            title="A staging version is required",
        ) from None
    return StagedObjectRef(
        locator=locator,
        expected_sha256=sha256,
        content_type=content_type,
        expected_size=expected_size,
    )
