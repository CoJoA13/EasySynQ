"""Event-driven exact-version cleanup for durably audited upload refusals."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import re
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models._audit_enums import EventType
from ..db.models.audit_event import AuditEvent
from ..services.vault import storage
from ..services.vault.staged_identity import (
    StagedVersionLocator,
    StagingDomain,
    StagingVersionRequired,
    StorageUnavailable,
)
from ..services.vault.upload_rejection import (
    MetricClassification,
    MetricDomain,
    MetricOperation,
    MetricStage,
    emit_upload_identity_metric,
)
from .app import task

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_EVENTS = frozenset({EventType.BLOB_INTEGRITY_FAILED, EventType.IMPORT_ITEM_FAILED})
_ALLOWED_CLASSIFICATIONS = frozenset(
    {"digest_mismatch", "size_mismatch", "source_missing", "source_changed"}
)
_ALLOWED_OPERATIONS = frozenset(
    {"document_checkin", "record_capture", "import_commit", "server_generated"}
)
_DELETE_POLICY = "delete_exact_version_after_audit"


@dataclasses.dataclass(frozen=True, slots=True)
class CleanupAttemptResult:
    operation: MetricOperation
    classification: MetricClassification
    domain: MetricDomain
    deleted: bool
    stage: MetricStage = "cleanup"


@dataclasses.dataclass(frozen=True, slots=True)
class _CleanupEvidence:
    operation: MetricOperation
    classification: MetricClassification
    domain: StagingDomain
    locator: StagedVersionLocator


class CleanupEvidenceRejected(ValueError):
    """Untrusted task input or durable evidence cannot authorize deletion or retry."""


class CleanupInfrastructureFailure(Exception):
    """A retryable dependency failed before cleanup reached a terminal result."""

    def __init__(self, stage: MetricStage, cause: BaseException) -> None:
        super().__init__(f"upload cleanup infrastructure failed during {stage}")
        self.stage = stage
        self.__cause__ = cause


def _parse_task_args(audit_event_id: int, occurred_at: str, attempt: int) -> datetime.datetime:
    if (
        isinstance(audit_event_id, bool)
        or not isinstance(audit_event_id, int)
        or audit_event_id <= 0
    ):
        raise CleanupEvidenceRejected("audit_event_id must be a positive integer")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 5:
        raise CleanupEvidenceRejected("attempt must be between 1 and 5")
    if not isinstance(occurred_at, str):
        raise CleanupEvidenceRejected("occurred_at must be a UTC ISO-8601 string")
    try:
        parsed = datetime.datetime.fromisoformat(occurred_at)
    except ValueError:
        raise CleanupEvidenceRejected("occurred_at must be a UTC ISO-8601 string") from None
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        raise CleanupEvidenceRejected("occurred_at must carry UTC offset zero")
    return parsed


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CleanupEvidenceRejected(f"audit evidence {name} must be an object")
    return value


def _validate_evidence(row: object | None) -> _CleanupEvidence:
    if row is None or getattr(row, "event_type", None) not in _ALLOWED_EVENTS:
        raise CleanupEvidenceRejected("audit event does not authorize upload cleanup")
    after = _mapping(getattr(row, "after", None), "after")
    operation = after.get("operation")
    classification = after.get("classification")
    if operation not in _ALLOWED_OPERATIONS:
        raise CleanupEvidenceRejected("audit evidence operation is not approved")
    if classification not in _ALLOWED_CLASSIFICATIONS:
        raise CleanupEvidenceRejected("audit evidence classification is not approved")
    cleanup = _mapping(after.get("cleanup"), "cleanup")
    if cleanup.get("policy") != _DELETE_POLICY:
        raise CleanupEvidenceRejected("audit evidence cleanup policy is not approved")
    source = _mapping(after.get("source"), "source")
    expected = _mapping(after.get("expected"), "expected")
    bucket = source.get("bucket")
    object_key = source.get("object_key")
    version_id = source.get("version_id")
    expected_sha = expected.get("sha256")
    if (
        not isinstance(object_key, str)
        or not _SHA256_RE.fullmatch(object_key)
        or not isinstance(expected_sha, str)
        or object_key != expected_sha
    ):
        raise CleanupEvidenceRejected("audit evidence key does not equal canonical expected sha256")
    if not isinstance(bucket, str):
        raise CleanupEvidenceRejected("audit evidence source bucket is not approved")
    try:
        domain = StagingDomain(bucket)
    except ValueError:
        raise CleanupEvidenceRejected("audit evidence source bucket is not approved") from None
    if not isinstance(version_id, str):
        raise CleanupEvidenceRejected("audit evidence lacks an exact source version")
    try:
        locator = StagedVersionLocator(
            domain=domain,
            object_key=object_key,
            version_id=version_id,
        )
    except (StagingVersionRequired, ValueError, TypeError):
        raise CleanupEvidenceRejected("audit evidence lacks a valid exact source version") from None
    return _CleanupEvidence(
        operation=cast(MetricOperation, operation),
        classification=cast(MetricClassification, classification),
        domain=domain,
        locator=locator,
    )


async def _cleanup_rejected_once(
    session_factory: async_sessionmaker[AsyncSession],
    audit_event_id: int,
    occurred_at: str,
    attempt: int,
) -> CleanupAttemptResult:
    partition_at = _parse_task_args(audit_event_id, occurred_at, attempt)
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(AuditEvent).where(
                    AuditEvent.id == audit_event_id,
                    AuditEvent.occurred_at == partition_at,
                )
            )
            row = result.scalar_one_or_none()
    except Exception as exc:
        raise CleanupInfrastructureFailure("audit", exc) from exc
    evidence = _validate_evidence(row)
    try:
        await storage.delete_staged_version(evidence.locator)
    except StorageUnavailable:
        deleted = False
    except Exception as exc:
        raise CleanupInfrastructureFailure("cleanup", exc) from exc
    else:
        deleted = True
    return CleanupAttemptResult(
        operation=evidence.operation,
        classification=evidence.classification,
        domain=evidence.domain.value,
        deleted=deleted,
    )


async def _run_cleanup_rejected(
    audit_event_id: int, occurred_at: str, attempt: int
) -> CleanupAttemptResult:
    # Engine lifetime is scoped to this asyncio.run; no pooled connection crosses worker loops.
    try:
        engine = create_async_engine(get_settings().database_url)
    except Exception as exc:
        raise CleanupInfrastructureFailure("audit", exc) from exc
    evidence_rejected = False
    try:
        try:
            session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
                engine, expire_on_commit=False
            )
        except Exception as exc:
            raise CleanupInfrastructureFailure("audit", exc) from exc
        return await _cleanup_rejected_once(session_factory, audit_event_id, occurred_at, attempt)
    except CleanupEvidenceRejected:
        evidence_rejected = True
        raise
    finally:
        try:
            await engine.dispose()
        except Exception as exc:
            if not evidence_rejected:
                raise CleanupInfrastructureFailure("audit", exc) from exc


def _infrastructure_result(stage: MetricStage) -> CleanupAttemptResult:
    return CleanupAttemptResult(
        operation="unknown",
        classification="none",
        domain="none",
        deleted=False,
        stage=stage,
    )


def _signal_and_schedule(
    result: CleanupAttemptResult,
    *,
    audit_event_id: int,
    occurred_at: str,
    attempt: int,
) -> None:
    if result.deleted:
        emit_upload_identity_metric(
            metric="cleanup_success",
            operation=result.operation,
            classification=result.classification,
            domain=result.domain,
            stage=result.stage,
            outcome="deleted",
        )
        return
    if attempt < 5:
        try:
            cleanup_rejected.apply_async(
                args=(audit_event_id, occurred_at, attempt + 1),
                countdown=min(3600, 60 * 2 ** (attempt - 1)),
            )
        except Exception:  # noqa: BLE001 -- broker failure is a terminal operator signal
            emit_upload_identity_metric(
                metric="cleanup_final_failure",
                operation=result.operation,
                classification=result.classification,
                domain=result.domain,
                stage=result.stage,
                outcome="publish_failed",
            )
            return
        emit_upload_identity_metric(
            metric="cleanup_retry",
            operation=result.operation,
            classification=result.classification,
            domain=result.domain,
            stage=result.stage,
            outcome="retry_scheduled",
        )
        return
    emit_upload_identity_metric(
        metric="cleanup_final_failure",
        operation=result.operation,
        classification=result.classification,
        domain=result.domain,
        stage=result.stage,
        outcome="terminal",
    )


@task(name="easysynq.upload_identity.cleanup_rejected")
def cleanup_rejected(audit_event_id: int, occurred_at: str, attempt: int = 1) -> None:
    try:
        _parse_task_args(audit_event_id, occurred_at, attempt)
    except CleanupEvidenceRejected:
        return
    try:
        result = asyncio.run(_run_cleanup_rejected(audit_event_id, occurred_at, attempt))
    except CleanupEvidenceRejected:
        # Untrusted/malformed durable evidence cannot authorize a retry or any delete.
        return
    except CleanupInfrastructureFailure as failure:
        result = _infrastructure_result(failure.stage)
    except Exception:  # noqa: BLE001 -- no worker exception may create unbounded broker behavior
        result = _infrastructure_result("audit")
    _signal_and_schedule(
        result,
        audit_event_id=audit_event_id,
        occurred_at=occurred_at,
        attempt=attempt,
    )
