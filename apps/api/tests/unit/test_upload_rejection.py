from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.problems import ProblemException
from easysynq_api.services.vault import upload_rejection
from easysynq_api.services.vault.staged_identity import (
    StagedObjectRef,
    StagedSourceChanged,
    StagedSourceUnavailable,
    StagedVersionLocator,
    StagingDomain,
    StorageStage,
    StorageUnavailable,
    TargetIdentityConflict,
    UploadIdentityMismatch,
)

pytestmark = pytest.mark.unit

_SHA = "a" * 64
_OTHER_SHA = "b" * 64
_OCCURRED_AT = datetime.datetime(2026, 8, 6, 12, 0, tzinfo=datetime.UTC)


def _source() -> StagedObjectRef:
    return StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=_SHA,
            version_id="opaque-v1",
        ),
        expected_sha256=_SHA,
        expected_size=11,
        content_type="application/pdf",
    )


def _context(
    *, operation: upload_rejection.UploadOperation = "document_checkin"
) -> upload_rejection.RejectionContext:
    return upload_rejection.RejectionContext(
        operation=operation,
        org_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        actor_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        actor_type=ActorType.user,
        scope_ref="DOC-GEN-0001",
        user_correctable=True,
    )


def _mismatch() -> UploadIdentityMismatch:
    return UploadIdentityMismatch(
        source=_source(),
        expected_sha256=_SHA,
        observed_sha256=_OTHER_SHA,
        expected_size=11,
        observed_size=11,
        etag='"opaque-etag"',
        classification="digest_mismatch",
    )


class _OwnerSession:
    def __init__(self, calls: list[str], *, rollback_error: BaseException | None = None) -> None:
        self.calls = calls
        self.rollback_error = rollback_error

    async def rollback(self) -> None:
        self.calls.append("owner.rollback")
        if self.rollback_error is not None:
            raise self.rollback_error


class _AuditSession:
    def __init__(self, calls: list[str], *, commit_error: BaseException | None = None) -> None:
        self.calls = calls
        self.commit_error = commit_error
        self.added: list[Any] = []

    async def __aenter__(self) -> _AuditSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def add(self, row: Any) -> None:
        self.calls.append("audit.add")
        self.added.append(row)

    async def flush(self) -> None:
        self.added[-1].id = 73

    async def commit(self) -> None:
        self.calls.append("audit.commit")
        if self.commit_error is not None:
            raise self.commit_error


class _AuditSessionmaker:
    def __init__(self, session: _AuditSession) -> None:
        self.session = session

    def __call__(self) -> _AuditSession:
        return self.session


@pytest.mark.asyncio
async def test_refusal_order_rolls_back_then_commits_audit_before_exact_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    audit_session = _AuditSession(calls)

    async def refuse(
        source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        assert source == _source()
        assert target_bucket == "documents"
        raise _mismatch()

    async def delete_exact(locator: StagedVersionLocator) -> None:
        assert locator == _source().locator
        calls.append("delete.exact")

    monkeypatch.setattr(upload_rejection.storage, "promote_worm", refuse)
    monkeypatch.setattr(upload_rejection.storage, "delete_staged_version", delete_exact)

    with pytest.raises(ProblemException) as caught:
        await upload_rejection.promote_for_owner(
            _OwnerSession(calls),  # type: ignore[arg-type]
            _source(),
            target_bucket="documents",
            context=_context(),
            rejection_sessionmaker=_AuditSessionmaker(audit_session),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == (422, "upload_identity_mismatch")
    assert calls == ["owner.rollback", "audit.add", "audit.commit", "delete.exact"]


@pytest.mark.asyncio
async def test_owner_rollback_failure_prevents_audit_cleanup_and_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def refuse(
        _source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        assert target_bucket == "documents"
        raise _mismatch()

    def unexpected_enqueue(*_args: object) -> None:
        calls.append("enqueue")

    monkeypatch.setattr(upload_rejection.storage, "promote_worm", refuse)
    monkeypatch.setattr(upload_rejection, "_enqueue_cleanup_retry", unexpected_enqueue)

    with pytest.raises(ProblemException) as caught:
        await upload_rejection.promote_for_owner(
            _OwnerSession(calls, rollback_error=RuntimeError("db unavailable")),  # type: ignore[arg-type]
            _source(),
            target_bucket="documents",
            context=_context(),
            rejection_sessionmaker=_AuditSessionmaker(_AuditSession(calls)),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == (503, "storage_unavailable")
    assert calls == ["owner.rollback"]


@pytest.mark.asyncio
async def test_audit_commit_failure_prevents_cleanup_and_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def refuse(
        _source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        assert target_bucket == "documents"
        raise _mismatch()

    async def unexpected_delete(_locator: StagedVersionLocator) -> None:
        calls.append("delete")

    def unexpected_enqueue(*_args: object) -> None:
        calls.append("enqueue")

    monkeypatch.setattr(upload_rejection.storage, "promote_worm", refuse)
    monkeypatch.setattr(upload_rejection.storage, "delete_staged_version", unexpected_delete)
    monkeypatch.setattr(upload_rejection, "_enqueue_cleanup_retry", unexpected_enqueue)

    with pytest.raises(ProblemException) as caught:
        await upload_rejection.promote_for_owner(
            _OwnerSession(calls),  # type: ignore[arg-type]
            _source(),
            target_bucket="documents",
            context=_context(),
            rejection_sessionmaker=_AuditSessionmaker(
                _AuditSession(calls, commit_error=RuntimeError("partition unavailable"))
            ),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == (503, "storage_unavailable")
    assert calls == ["owner.rollback", "audit.add", "audit.commit"]


@pytest.mark.asyncio
async def test_delete_failure_enqueues_committed_reference_and_preserves_original_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    enqueued: list[tuple[int, datetime.datetime]] = []

    async def refuse(
        _source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        assert target_bucket == "documents"
        raise _mismatch()

    async def fail_delete(locator: StagedVersionLocator) -> None:
        assert locator == _source().locator
        calls.append("delete.exact")
        raise StorageUnavailable(StorageStage.CLEANUP)

    def enqueue(ref: upload_rejection.AuditEventRef) -> None:
        enqueued.append((ref.id, ref.occurred_at))

    monkeypatch.setattr(upload_rejection.storage, "promote_worm", refuse)
    monkeypatch.setattr(upload_rejection.storage, "delete_staged_version", fail_delete)
    monkeypatch.setattr(upload_rejection, "_enqueue_cleanup_retry", enqueue)
    monkeypatch.setattr(upload_rejection, "_utc_now", lambda: _OCCURRED_AT)

    with pytest.raises(ProblemException) as caught:
        await upload_rejection.promote_for_owner(
            _OwnerSession(calls),  # type: ignore[arg-type]
            _source(),
            target_bucket="documents",
            context=_context(),
            rejection_sessionmaker=_AuditSessionmaker(_AuditSession(calls)),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == (422, "upload_identity_mismatch")
    assert enqueued == [(73, _OCCURRED_AT)]
    assert calls == ["owner.rollback", "audit.add", "audit.commit", "delete.exact"]


@pytest.mark.asyncio
async def test_retry_publish_failure_preserves_original_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def refuse(
        _source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        raise _mismatch()

    async def fail_delete(_locator: StagedVersionLocator) -> None:
        calls.append("delete.exact")
        raise StorageUnavailable(StorageStage.CLEANUP)

    def fail_enqueue(_ref: upload_rejection.AuditEventRef) -> None:
        calls.append("enqueue")
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(upload_rejection.storage, "promote_worm", refuse)
    monkeypatch.setattr(upload_rejection.storage, "delete_staged_version", fail_delete)
    monkeypatch.setattr(upload_rejection, "_enqueue_cleanup_retry", fail_enqueue)

    with pytest.raises(ProblemException) as caught:
        await upload_rejection.promote_for_owner(
            _OwnerSession(calls),  # type: ignore[arg-type]
            _source(),
            target_bucket="documents",
            context=_context(),
            rejection_sessionmaker=_AuditSessionmaker(_AuditSession(calls)),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == (422, "upload_identity_mismatch")
    assert calls == ["owner.rollback", "audit.add", "audit.commit", "delete.exact", "enqueue"]


@pytest.mark.asyncio
async def test_infrastructure_failure_rolls_back_without_false_identity_evidence_or_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def unavailable(
        _source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        assert target_bucket == "documents"
        raise StorageUnavailable(StorageStage.COPY)

    monkeypatch.setattr(upload_rejection.storage, "promote_worm", unavailable)

    with pytest.raises(ProblemException) as caught:
        await upload_rejection.promote_for_owner(
            _OwnerSession(calls),  # type: ignore[arg-type]
            _source(),
            target_bucket="documents",
            context=_context(),
            rejection_sessionmaker=_AuditSessionmaker(_AuditSession(calls)),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == (503, "storage_unavailable")
    assert calls == ["owner.rollback"]


@pytest.mark.asyncio
async def test_target_conflict_commits_retain_source_evidence_without_cleanup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []
    session = _AuditSession(calls)
    failure = TargetIdentityConflict(
        source=_source(),
        target_bucket="documents",
        target_key=_SHA,
        target_version_id="must-not-be-persisted",
        observed_sha256=_OTHER_SHA,
        observed_size=11,
    )

    async def conflict(
        _source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        assert target_bucket == "documents"
        raise failure

    async def unexpected_delete(_locator: StagedVersionLocator) -> None:
        calls.append("delete")

    def unexpected_enqueue(_ref: upload_rejection.AuditEventRef) -> None:
        calls.append("enqueue")

    monkeypatch.setattr(upload_rejection.storage, "promote_worm", conflict)
    monkeypatch.setattr(upload_rejection.storage, "delete_staged_version", unexpected_delete)
    monkeypatch.setattr(upload_rejection, "_enqueue_cleanup_retry", unexpected_enqueue)
    caplog.set_level(logging.INFO, logger="easysynq.upload_identity")

    with pytest.raises(ProblemException) as caught:
        await upload_rejection.promote_for_owner(
            _OwnerSession(calls),  # type: ignore[arg-type]
            _source(),
            target_bucket="documents",
            context=_context(),
            rejection_sessionmaker=_AuditSessionmaker(session),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == (503, "storage_unavailable")
    assert calls == ["owner.rollback", "audit.add", "audit.commit"]
    assert session.added[0].after == {
        "operation": "document_checkin",
        "classification": "target_identity_conflict",
        "target": {"bucket": "documents", "object_key": _SHA},
        "expected": {"sha256": _SHA, "size_bytes": 11},
        "observed": {"sha256": _OTHER_SHA, "size_bytes": 11},
        "cleanup": {"policy": "retain_source_operator_investigation"},
    }
    assert "must-not-be-persisted" not in repr(session.added[0].after)
    assert caplog.records[-1].extra_fields == {
        "metric": "identity_mismatch",
        "operation": "document_checkin",
        "classification": "target_identity_conflict",
        "domain": "documents",
        "stage": "target_read",
        "outcome": "retained",
        "count": 1,
    }


@pytest.mark.asyncio
async def test_durable_db_sink_persists_private_fixed_payload_and_partition_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    session = _AuditSession(calls)
    context = _context()
    sink = upload_rejection.DbUploadRejectionSink(
        _AuditSessionmaker(session)  # type: ignore[arg-type]
    )
    monkeypatch.setattr(upload_rejection, "_utc_now", lambda: _OCCURRED_AT)

    ref = await sink.record(context, _mismatch())

    row = session.added[0]
    assert ref == upload_rejection.AuditEventRef(id=73, occurred_at=_OCCURRED_AT)
    assert (row.event_type, row.object_type, row.object_id) == (
        EventType.BLOB_INTEGRITY_FAILED,
        AuditObjectType.config,
        context.org_id,
    )
    assert (row.actor_type, row.actor_id, row.scope_ref) == (
        ActorType.user,
        context.actor_id,
        context.scope_ref,
    )
    assert row.after == {
        "operation": "document_checkin",
        "classification": "digest_mismatch",
        "source": {
            "bucket": "staging",
            "object_key": _SHA,
            "version_id": "opaque-v1",
            "etag": '"opaque-etag"',
        },
        "expected": {"sha256": _SHA, "size_bytes": 11},
        "observed": {"sha256": _OTHER_SHA, "size_bytes": 11},
        "cleanup": {"policy": "delete_exact_version_after_audit"},
    }
    forbidden = {"body", "url", "credentials", "filename", "user", "exception"}
    assert forbidden.isdisjoint(row.after)


@pytest.mark.asyncio
async def test_generated_rejection_uses_system_actor_and_private_503() -> None:
    calls: list[str] = []
    session = _AuditSession(calls)
    context = _context(operation="server_generated")

    async def refuse(
        _source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        raise _mismatch()

    async def delete_exact(_locator: StagedVersionLocator) -> None:
        calls.append("delete.exact")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(upload_rejection.storage, "promote_worm", refuse)
    monkeypatch.setattr(upload_rejection.storage, "delete_staged_version", delete_exact)
    try:
        with pytest.raises(ProblemException) as caught:
            await upload_rejection.promote_for_owner(
                _OwnerSession(calls),  # type: ignore[arg-type]
                _source(),
                target_bucket="documents",
                context=context,
                rejection_sessionmaker=_AuditSessionmaker(session),  # type: ignore[arg-type]
            )
    finally:
        monkeypatch.undo()

    assert (caught.value.status, caught.value.code) == (503, "storage_unavailable")
    assert (session.added[0].actor_type, session.added[0].actor_id) == (ActorType.system, None)


@pytest.mark.parametrize(
    ("failure_factory", "expected"),
    [
        (lambda: StagedSourceUnavailable(_source()), (409, "staged_source_unavailable")),
        (lambda: StagedSourceChanged(_source()), (409, "staged_source_unavailable")),
    ],
)
@pytest.mark.asyncio
async def test_source_refusal_maps_to_stable_restart_problem(
    monkeypatch: pytest.MonkeyPatch,
    failure_factory: Callable[[], BaseException],
    expected: tuple[int, str],
) -> None:
    calls: list[str] = []

    async def refuse(
        _source: StagedObjectRef, *, target_bucket: str, min_retain_until: Any = None
    ) -> Any:
        raise failure_factory()

    async def delete_exact(_locator: StagedVersionLocator) -> None:
        calls.append("delete.exact")

    monkeypatch.setattr(upload_rejection.storage, "promote_worm", refuse)
    monkeypatch.setattr(upload_rejection.storage, "delete_staged_version", delete_exact)

    with pytest.raises(ProblemException) as caught:
        await upload_rejection.promote_for_owner(
            _OwnerSession(calls),  # type: ignore[arg-type]
            _source(),
            target_bucket="documents",
            context=_context(),
            rejection_sessionmaker=_AuditSessionmaker(_AuditSession(calls)),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == expected


@pytest.mark.parametrize("version_id", [None, "", "null", "v" * 1025])
def test_require_staging_ref_rejects_missing_or_legacy_version_with_stable_problem(
    version_id: str | None,
) -> None:
    with pytest.raises(ProblemException) as caught:
        upload_rejection.require_staging_ref(
            domain=StagingDomain.STAGING,
            sha256=_SHA,
            version_id=version_id,
            content_type="application/pdf",
            operation="record_capture",
        )

    assert (caught.value.status, caught.value.code) == (422, "staging_version_required")


@pytest.mark.parametrize(
    ("sha256", "content_type", "expected_size"),
    [
        ("not-a-sha", "application/pdf", 12),
        (_SHA, "", 12),
        (_SHA, "application/pdf", -1),
    ],
)
def test_valid_version_does_not_misclassify_other_invalid_source_fields_as_missing_version(
    caplog: pytest.LogCaptureFixture,
    sha256: str,
    content_type: str,
    expected_size: int,
) -> None:
    caplog.set_level(logging.INFO, logger="easysynq.upload_identity")

    with pytest.raises(ValueError) as caught:
        upload_rejection.require_staging_ref(
            domain=StagingDomain.STAGING,
            sha256=sha256,
            version_id="valid-opaque-version",
            content_type=content_type,
            expected_size=expected_size,
            operation="record_capture",
        )

    assert not isinstance(caught.value, ProblemException)
    assert all(
        getattr(record, "extra_fields", {}).get("metric") != "missing_version"
        for record in caplog.records
    )


def test_metric_signal_has_only_bounded_dimensions(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="easysynq.upload_identity")

    upload_rejection.emit_upload_identity_metric(
        metric="cleanup_retry",
        operation="import_commit",
        classification="source_changed",
        domain="import-staging",
        stage="cleanup",
        outcome="retry_scheduled",
    )

    record = caplog.records[-1]
    assert record.getMessage() == "upload_identity.metric"
    assert record.extra_fields == {
        "metric": "cleanup_retry",
        "operation": "import_commit",
        "classification": "source_changed",
        "domain": "import-staging",
        "stage": "cleanup",
        "outcome": "retry_scheduled",
        "count": 1,
    }
    forbidden_values = {_SHA, "opaque-v1", "DOC-GEN-0001", "application/pdf"}
    assert forbidden_values.isdisjoint(record.extra_fields.values())
    assert {"object_key", "version_id", "user_id", "org_id", "filename"}.isdisjoint(
        record.extra_fields
    )
