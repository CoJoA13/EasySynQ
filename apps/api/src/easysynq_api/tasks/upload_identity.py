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
    RejectionClassification,
    UploadOperation,
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
    operation: UploadOperation
    classification: RejectionClassification
    domain: MetricDomain
    deleted: bool


@dataclasses.dataclass(frozen=True, slots=True)
class _CleanupEvidence:
    operation: UploadOperation
    classification: RejectionClassification
    domain: StagingDomain
    locator: StagedVersionLocator


def _parse_task_args(audit_event_id: int, occurred_at: str, attempt: int) -> datetime.datetime:
    if (
        isinstance(audit_event_id, bool)
        or not isinstance(audit_event_id, int)
        or audit_event_id <= 0
    ):
        raise ValueError("audit_event_id must be a positive integer")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 5:
        raise ValueError("attempt must be between 1 and 5")
    if not isinstance(occurred_at, str):
        raise ValueError("occurred_at must be a UTC ISO-8601 string")
    try:
        parsed = datetime.datetime.fromisoformat(occurred_at)
    except ValueError:
        raise ValueError("occurred_at must be a UTC ISO-8601 string") from None
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        raise ValueError("occurred_at must carry UTC offset zero")
    return parsed


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"audit evidence {name} must be an object")
    return value


def _validate_evidence(row: object | None) -> _CleanupEvidence:
    if row is None or getattr(row, "event_type", None) not in _ALLOWED_EVENTS:
        raise ValueError("audit event does not authorize upload cleanup")
    after = _mapping(getattr(row, "after", None), "after")
    operation = after.get("operation")
    classification = after.get("classification")
    if operation not in _ALLOWED_OPERATIONS:
        raise ValueError("audit evidence operation is not approved")
    if classification not in _ALLOWED_CLASSIFICATIONS:
        raise ValueError("audit evidence classification is not approved")
    cleanup = _mapping(after.get("cleanup"), "cleanup")
    if cleanup.get("policy") != _DELETE_POLICY:
        raise ValueError("audit evidence cleanup policy is not approved")
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
        raise ValueError("audit evidence key does not equal canonical expected sha256")
    if not isinstance(bucket, str):
        raise ValueError("audit evidence source bucket is not approved")
    try:
        domain = StagingDomain(bucket)
    except ValueError:
        raise ValueError("audit evidence source bucket is not approved") from None
    if not isinstance(version_id, str):
        raise ValueError("audit evidence lacks an exact source version")
    try:
        locator = StagedVersionLocator(
            domain=domain,
            object_key=object_key,
            version_id=version_id,
        )
    except (StagingVersionRequired, ValueError, TypeError):
        raise ValueError("audit evidence lacks a valid exact source version") from None
    return _CleanupEvidence(
        operation=cast(UploadOperation, operation),
        classification=cast(RejectionClassification, classification),
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
    async with session_factory() as session:
        result = await session.execute(
            select(AuditEvent).where(
                AuditEvent.id == audit_event_id,
                AuditEvent.occurred_at == partition_at,
            )
        )
        evidence = _validate_evidence(result.scalar_one_or_none())
    try:
        await storage.delete_staged_version(evidence.locator)
    except StorageUnavailable:
        deleted = False
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
    engine = create_async_engine(get_settings().database_url)
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        return await _cleanup_rejected_once(session_factory, audit_event_id, occurred_at, attempt)
    finally:
        await engine.dispose()


@task(name="easysynq.upload_identity.cleanup_rejected")
def cleanup_rejected(audit_event_id: int, occurred_at: str, attempt: int = 1) -> None:
    try:
        result = asyncio.run(_run_cleanup_rejected(audit_event_id, occurred_at, attempt))
    except ValueError:
        # Untrusted/malformed durable evidence cannot authorize a retry or any delete.
        return
    if result.deleted:
        emit_upload_identity_metric(
            metric="cleanup_success",
            operation=result.operation,
            classification=cast(MetricClassification, result.classification),
            domain=result.domain,
            stage="cleanup",
            outcome="deleted",
        )
        return
    if attempt < 5:
        cleanup_rejected.apply_async(
            args=(audit_event_id, occurred_at, attempt + 1),
            countdown=min(3600, 60 * 2 ** (attempt - 1)),
        )
        emit_upload_identity_metric(
            metric="cleanup_retry",
            operation=result.operation,
            classification=cast(MetricClassification, result.classification),
            domain=result.domain,
            stage="cleanup",
            outcome="retry_scheduled",
        )
        return
    emit_upload_identity_metric(
        metric="cleanup_final_failure",
        operation=result.operation,
        classification=cast(MetricClassification, result.classification),
        domain=result.domain,
        stage="cleanup",
        outcome="terminal",
    )
