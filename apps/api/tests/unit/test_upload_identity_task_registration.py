from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.services.vault import storage
from easysynq_api.services.vault.staged_identity import StorageStage, StorageUnavailable
from easysynq_api.tasks import upload_identity
from easysynq_api.tasks.app import app

pytestmark = pytest.mark.unit

_SHA = "a" * 64
_OCCURRED = datetime.datetime(2026, 8, 6, 14, 30, tzinfo=datetime.UTC)
_OCCURRED_TEXT = "2026-08-06T14:30:00+00:00"


def _row(
    *,
    event_type: EventType = EventType.BLOB_INTEGRITY_FAILED,
    classification: str = "digest_mismatch",
    policy: str = "delete_exact_version_after_audit",
    bucket: str = "staging",
    object_key: str = _SHA,
    version_id: object = "opaque-v1",
    expected_sha: object = _SHA,
) -> SimpleNamespace:
    return SimpleNamespace(
        event_type=event_type,
        after={
            "operation": "document_checkin",
            "classification": classification,
            "source": {
                "bucket": bucket,
                "object_key": object_key,
                "version_id": version_id,
                "etag": None,
            },
            "expected": {"sha256": expected_sha, "size_bytes": 12},
            "observed": {"sha256": "b" * 64, "size_bytes": 12},
            "cleanup": {"policy": policy},
        },
    )


class _ScalarResult:
    def __init__(self, row: object | None) -> None:
        self.row = row

    def scalar_one_or_none(self) -> object | None:
        return self.row


class _Session:
    def __init__(self, row: object | None) -> None:
        self.row = row
        self.queries: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: Any) -> _ScalarResult:
        self.queries.append(statement)
        return _ScalarResult(self.row)


class _Sessionmaker:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def __call__(self) -> _Session:
        return self.session


def test_cleanup_task_is_registered_without_beat_schedule() -> None:
    assert "easysynq.upload_identity.cleanup_rejected" in app.tasks
    assert all(
        entry["task"] != "easysynq.upload_identity.cleanup_rejected"
        for entry in app.conf.beat_schedule.values()
    )


@pytest.mark.parametrize(
    ("audit_id", "occurred_at", "attempt"),
    [
        (0, _OCCURRED_TEXT, 1),
        (-1, _OCCURRED_TEXT, 1),
        (True, _OCCURRED_TEXT, 1),
        (1, "not-a-datetime", 1),
        (1, "2026-08-06T14:30:00", 1),
        (1, "2026-08-06T15:30:00+01:00", 1),
        (1, _OCCURRED_TEXT, 0),
        (1, _OCCURRED_TEXT, 6),
    ],
)
@pytest.mark.asyncio
async def test_invalid_task_arguments_are_rejected_before_query_or_delete(
    monkeypatch: pytest.MonkeyPatch,
    audit_id: object,
    occurred_at: str,
    attempt: int,
) -> None:
    session = _Session(_row())
    deleted: list[object] = []

    async def delete(locator: object) -> None:
        deleted.append(locator)

    monkeypatch.setattr(storage, "delete_staged_version", delete)

    with pytest.raises(ValueError):
        await upload_identity._cleanup_rejected_once(  # type: ignore[arg-type]
            _Sessionmaker(session), audit_id, occurred_at, attempt
        )

    assert session.queries == []
    assert deleted == []


@pytest.mark.asyncio
async def test_cleanup_selects_by_global_id_and_partition_key_and_deletes_exact_locator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_row())
    deleted: list[object] = []

    async def delete(locator: object) -> None:
        deleted.append(locator)

    monkeypatch.setattr(storage, "delete_staged_version", delete)

    result = await upload_identity._cleanup_rejected_once(
        _Sessionmaker(session), 41, _OCCURRED_TEXT, 1
    )

    compiled = session.queries[0].compile(dialect=postgresql.dialect())
    assert "audit_event.id =" in str(compiled)
    assert "audit_event.occurred_at =" in str(compiled)
    assert 41 in compiled.params.values()
    assert _OCCURRED in compiled.params.values()
    assert result.deleted is True
    assert deleted[0].domain.value == "staging"
    assert deleted[0].object_key == _SHA
    assert deleted[0].version_id == "opaque-v1"


@pytest.mark.parametrize(
    "event_type", [EventType.BLOB_INTEGRITY_FAILED, EventType.IMPORT_ITEM_FAILED]
)
@pytest.mark.asyncio
async def test_cleanup_accepts_only_approved_integrity_event_families(
    monkeypatch: pytest.MonkeyPatch, event_type: EventType
) -> None:
    deleted: list[object] = []

    async def delete(locator: object) -> None:
        deleted.append(locator)

    monkeypatch.setattr(storage, "delete_staged_version", delete)
    result = await upload_identity._cleanup_rejected_once(
        _Sessionmaker(_Session(_row(event_type=event_type))), 41, _OCCURRED_TEXT, 1
    )

    assert result.deleted is True
    assert len(deleted) == 1


@pytest.mark.parametrize(
    "row",
    [
        _row(event_type=EventType.ACCESS_DENIED),
        _row(classification="target_identity_conflict"),
        _row(classification="unknown"),
        _row(policy="retain_source_operator_investigation"),
        _row(policy="unknown"),
        _row(bucket="documents"),
        _row(bucket="unknown"),
        _row(object_key="b" * 64),
        _row(object_key="not-a-sha", expected_sha="not-a-sha"),
        _row(version_id=None),
        _row(version_id="null"),
        _row(version_id=""),
        SimpleNamespace(event_type=EventType.BLOB_INTEGRITY_FAILED, after=None),
        None,
    ],
)
@pytest.mark.asyncio
async def test_untrusted_audit_evidence_never_authorizes_cleanup(
    monkeypatch: pytest.MonkeyPatch, row: object | None
) -> None:
    deleted: list[object] = []

    async def delete(locator: object) -> None:
        deleted.append(locator)

    monkeypatch.setattr(storage, "delete_staged_version", delete)

    with pytest.raises(ValueError):
        await upload_identity._cleanup_rejected_once(
            _Sessionmaker(_Session(row)), 41, _OCCURRED_TEXT, 1
        )

    assert deleted == []


@pytest.mark.asyncio
async def test_exact_object_version_absence_is_cleanup_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AbsentVersionClient:
        def delete_object(self, *, Bucket: str, Key: str, VersionId: str) -> None:
            assert (Bucket, Key, VersionId) == ("staging", _SHA, "opaque-v1")
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchVersion"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "DeleteObject",
            )

    monkeypatch.setattr(storage, "_client", AbsentVersionClient)

    async def delete_through_real_absence_mapper(locator: object) -> None:
        storage._delete_staged_version_sync(locator)  # type: ignore[arg-type]

    monkeypatch.setattr(storage, "delete_staged_version", delete_through_real_absence_mapper)

    result = await upload_identity._cleanup_rejected_once(
        _Sessionmaker(_Session(_row())), 41, _OCCURRED_TEXT, 1
    )

    assert result.deleted is True


@pytest.mark.parametrize("valid_evidence", [True, False])
@pytest.mark.asyncio
async def test_task_local_engine_is_disposed_on_success_and_validation_failure(
    monkeypatch: pytest.MonkeyPatch, valid_evidence: bool
) -> None:
    class Engine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = Engine()
    sessionmaker = _Sessionmaker(_Session(_row() if valid_evidence else None))

    def create_engine(database_url: str) -> Engine:
        assert database_url
        return engine

    def create_sessionmaker(created_engine: object, *, expire_on_commit: bool) -> _Sessionmaker:
        assert created_engine is engine
        assert expire_on_commit is False
        return sessionmaker

    async def delete(_locator: object) -> None:
        return None

    monkeypatch.setattr(upload_identity, "create_async_engine", create_engine)
    monkeypatch.setattr(upload_identity, "async_sessionmaker", create_sessionmaker)
    monkeypatch.setattr(storage, "delete_staged_version", delete)

    if valid_evidence:
        result = await upload_identity._run_cleanup_rejected(41, _OCCURRED_TEXT, 1)
        assert result.deleted is True
    else:
        with pytest.raises(ValueError):
            await upload_identity._run_cleanup_rejected(41, _OCCURRED_TEXT, 1)
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_bucket_absence_remains_retryable_infrastructure_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def bucket_absent(_locator: object) -> None:
        raise StorageUnavailable(StorageStage.CLEANUP)

    monkeypatch.setattr(storage, "delete_staged_version", bucket_absent)

    result = await upload_identity._cleanup_rejected_once(
        _Sessionmaker(_Session(_row())), 41, _OCCURRED_TEXT, 1
    )

    assert result.deleted is False


def test_transient_failure_reschedules_same_audit_reference_with_bounded_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = upload_identity.CleanupAttemptResult(
        operation="document_checkin",
        classification="digest_mismatch",
        domain="staging",
        deleted=False,
    )
    scheduled: list[dict[str, object]] = []

    def run(coroutine: Any) -> upload_identity.CleanupAttemptResult:
        coroutine.close()
        return result

    def apply_async(*, args: tuple[object, ...], countdown: int) -> None:
        scheduled.append({"args": args, "countdown": countdown})

    monkeypatch.setattr(upload_identity.asyncio, "run", run)
    monkeypatch.setattr(upload_identity.cleanup_rejected, "apply_async", apply_async)

    upload_identity.cleanup_rejected(41, _OCCURRED_TEXT, 4)

    assert scheduled == [{"args": (41, _OCCURRED_TEXT, 5), "countdown": 480}]


def test_attempt_five_is_terminal_and_never_broadens_deletion(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    result = upload_identity.CleanupAttemptResult(
        operation="document_checkin",
        classification="digest_mismatch",
        domain="staging",
        deleted=False,
    )
    scheduled: list[object] = []

    def run(coroutine: Any) -> upload_identity.CleanupAttemptResult:
        coroutine.close()
        return result

    monkeypatch.setattr(upload_identity.asyncio, "run", run)
    monkeypatch.setattr(
        upload_identity.cleanup_rejected,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )
    caplog.set_level("INFO", logger="easysynq.upload_identity")

    upload_identity.cleanup_rejected(41, _OCCURRED_TEXT, 5)

    assert scheduled == []
    assert caplog.records[-1].extra_fields == {
        "metric": "cleanup_final_failure",
        "operation": "document_checkin",
        "classification": "digest_mismatch",
        "domain": "staging",
        "stage": "cleanup",
        "outcome": "terminal",
        "count": 1,
    }
