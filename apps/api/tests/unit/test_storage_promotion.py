"""Exact-version storage promotion and staged-object identity contracts."""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from easysynq_api.services.vault import storage
from easysynq_api.services.vault.staged_identity import (
    PromotionOutcome,
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

pytestmark = pytest.mark.unit


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def close(self) -> None:
        self.closed = True


class RaisingBody(FakeBody):
    def __init__(self, data: bytes, error: BaseException) -> None:
        super().__init__(data)
        self.error = error
        self.calls = 0

    def read(self, size: int = -1) -> bytes:
        self.calls += 1
        if self.calls == 1 and self._stream.tell() == 0:
            return self._stream.read(min(size, 2))
        raise self.error


class FakeS3:
    def __init__(self, *, source_bytes: bytes, source_version: str) -> None:
        self.source_body = FakeBody(source_bytes)
        self.source_version = source_version
        self.source_etag = '"opaque-etag"'
        self.copy_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.retention_calls: list[dict[str, Any]] = []
        self.target_version: str | None = None
        self.target_bytes: bytes | None = None
        self.target_bodies: list[FakeBody] = []

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
        return {"Status": "Enabled"}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        if kwargs["Bucket"] == "documents":
            assert self.target_bytes is not None
            body = FakeBody(self.target_bytes)
            self.target_bodies.append(body)
            return {
                "Body": body,
                "VersionId": self.target_version,
                "ContentType": "application/pdf",
            }
        return {
            "Body": self.source_body,
            "VersionId": self.source_version,
            "ETag": self.source_etag,
            "ContentType": "application/pdf",
        }

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        self.copy_calls.append(kwargs)
        self.target_version = "target-v1"
        self.target_bytes = self.source_body._stream.getvalue()
        return {"VersionId": self.target_version}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.head_calls.append(kwargs)
        if self.target_version is None:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")
        return {
            "VersionId": self.target_version,
            "ContentLength": len(self.target_bytes or b""),
            "ContentType": "application/pdf",
        }

    def get_object_retention(self, **kwargs: Any) -> dict[str, Any]:
        self.retention_calls.append(kwargs)
        return {"Retention": {"RetainUntilDate": datetime.now(UTC) + timedelta(days=1)}}


def staged_ref(*, sha: str, version_id: str, size: int | None = None) -> StagedObjectRef:
    return StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=sha,
            version_id=version_id,
        ),
        expected_sha256=sha,
        content_type="application/pdf",
        expected_size=size,
    )


def s3_error(code: str, operation: str = "S3", *, status: int | None = None) -> ClientError:
    status = status if status is not None else (int(code) if code.isdigit() else 400)
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, operation
    )


@pytest.mark.parametrize("version_id", ["", "null", "v" * 1025])
def test_locator_rejects_non_exact_version_identity(version_id: str) -> None:
    with pytest.raises(StagingVersionRequired):
        StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key="a" * 64,
            version_id=version_id,
        )


def test_locator_rejects_runtime_foreign_domain_and_blank_key() -> None:
    with pytest.raises(ValueError):
        StagedVersionLocator(  # type: ignore[arg-type]
            domain="foreign", object_key="a" * 64, version_id="v1"
        )
    with pytest.raises(ValueError):
        StagedVersionLocator(domain=StagingDomain.STAGING, object_key="", version_id="v1")


@pytest.mark.parametrize(
    ("sha", "content_type", "expected_size"),
    [
        ("A" * 64, "application/pdf", None),
        ("a" * 63, "application/pdf", None),
        ("a" * 64, "", None),
        ("a" * 64, "application/pdf", -1),
    ],
)
def test_ref_rejects_noncanonical_identity_fields(
    sha: str, content_type: str, expected_size: int | None
) -> None:
    locator = StagedVersionLocator(
        domain=StagingDomain.STAGING,
        object_key="a" * 64,
        version_id="v1",
    )
    with pytest.raises(ValueError):
        StagedObjectRef(
            locator=locator,
            expected_sha256=sha,
            content_type=content_type,
            expected_size=expected_size,
        )


def test_ref_rejects_key_sha_divergence() -> None:
    locator = StagedVersionLocator(
        domain=StagingDomain.STAGING,
        object_key="b" * 64,
        version_id="v1",
    )
    with pytest.raises(ValueError):
        StagedObjectRef(
            locator=locator,
            expected_sha256="a" * 64,
            content_type="application/pdf",
        )


def test_locator_accepts_opaque_url_significant_version() -> None:
    locator = StagedVersionLocator(
        domain=StagingDomain.IMPORT_STAGING,
        object_key="a" * 64,
        version_id="v+/=?%opaque",
    )
    assert locator.version_id == "v+/=?%opaque"


def test_finalize_sync_upload_identity_mismatch_rejects_same_size_false_bytes_before_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = b"approved-2026"
    evil = b"tampered-2026"
    assert len(good) == len(evil)
    sha = hashlib.sha256(good).hexdigest()
    source = staged_ref(sha=sha, version_id="v-evil", size=len(good))
    client = FakeS3(source_bytes=evil, source_version="v-evil")
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(UploadIdentityMismatch) as caught:
        storage._finalize_sync(source, "documents", client=client)

    assert caught.value.expected_sha256 == sha
    assert caught.value.observed_sha256 == hashlib.sha256(evil).hexdigest()
    assert client.copy_calls == []
    assert client.source_body.closed is True


def test_finalize_sync_copy_pins_exact_source_version_with_opaque_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    source = staged_ref(sha=sha, version_id="v+/=source", size=len(data))
    client = FakeS3(source_bytes=data, source_version="v+/=source")
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    result = storage._finalize_sync(source, "documents", client=client)

    assert result.outcome is PromotionOutcome.COPIED
    assert client.get_calls[0] == {
        "Bucket": "staging",
        "Key": sha,
        "VersionId": "v+/=source",
    }
    assert client.copy_calls == [
        {
            "Bucket": "documents",
            "Key": sha,
            "CopySource": {"Bucket": "staging", "Key": sha, "VersionId": "v+/=source"},
            "CopySourceIfMatch": '"opaque-etag"',
        }
    ]
    assert client.head_calls == [
        {"Bucket": "documents", "Key": sha},
        {"Bucket": "documents", "Key": sha, "VersionId": "target-v1"},
    ]
    assert client.retention_calls == [{"Bucket": "documents", "Key": sha, "VersionId": "target-v1"}]
    assert result.source_etag == '"opaque-etag"'
    assert result.target_version_id == "target-v1"
    assert client.source_body.closed is True


@pytest.mark.parametrize("returned_version", [None, "different-version"])
def test_verify_rejects_unproven_response_version_and_closes_body(
    monkeypatch: pytest.MonkeyPatch, returned_version: str | None
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    source = staged_ref(sha=sha, version_id="source-v1")
    client = FakeS3(source_bytes=data, source_version=returned_version or "")
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._verify_staged_sync(source, client=client)

    assert caught.value.stage is StorageStage.SOURCE_GET
    assert source.locator.version_id == "source-v1"
    assert client.source_body.closed is True


@pytest.mark.parametrize("status", [None, "Suspended"])
def test_versioning_guard_fails_before_source_or_target_access(
    monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.get_bucket_versioning = lambda **kwargs: {"Status": status} if status else {}  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.VERSIONING
    assert client.get_calls == []
    assert client.head_calls == []
    assert client.copy_calls == []


def test_inaccessible_versioning_probe_fails_before_any_object_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.get_bucket_versioning = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        EndpointConnectionError(endpoint_url="http://minio:9000")
    )
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.VERSIONING
    assert client.get_calls == []
    assert client.head_calls == []
    assert client.copy_calls == []


def test_verify_ignores_claimed_checksum_metadata_and_hashes_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = b"approved-2026"
    evil = b"tampered-2026"
    sha = hashlib.sha256(good).hexdigest()
    source = staged_ref(sha=sha, version_id="v1", size=len(good))
    client = FakeS3(source_bytes=evil, source_version="v1")
    original_get = client.get_object

    def get_with_claimed_checksum(**kwargs: Any) -> dict[str, Any]:
        response = original_get(**kwargs)
        response["ChecksumSHA256"] = sha
        response["Metadata"] = {"sha256": sha}
        return response

    client.get_object = get_with_claimed_checksum  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(UploadIdentityMismatch) as caught:
        storage._verify_staged_sync(source, client=client)

    assert caught.value.classification == "digest_mismatch"
    assert caught.value.observed_sha256 == hashlib.sha256(evil).hexdigest()
    assert client.source_body.closed is True


def test_verify_rejects_blank_etag_before_copy_and_closes_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.source_etag = "  "
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.SOURCE_GET
    assert client.copy_calls == []
    assert client.source_body.closed is True


def test_verify_rejects_expected_size_mismatch_even_when_digest_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(UploadIdentityMismatch) as caught:
        storage._finalize_sync(
            staged_ref(sha=sha, version_id="v1", size=len(data) + 1),
            "documents",
            client=client,
        )

    assert caught.value.classification == "size_mismatch"
    assert caught.value.observed_sha256 == sha
    assert client.copy_calls == []
    assert client.source_body.closed is True


def test_source_read_baseexception_closes_body_without_reclassification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cancelled(BaseException):
        pass

    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.source_body = RaisingBody(data, Cancelled())
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(Cancelled):
        storage._verify_staged_sync(staged_ref(sha=sha, version_id="v1"), client=client)

    assert client.source_body.closed is True


def test_retry_after_midstream_failure_gets_fresh_version_and_hashes_from_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    source = staged_ref(sha=sha, version_id="v1")
    first = RaisingBody(data, OSError("stream reset"))
    second = FakeBody(data)

    class RetryClient(FakeS3):
        def __init__(self) -> None:
            super().__init__(source_bytes=data, source_version="v1")
            self.bodies = iter((first, second))

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            self.get_calls.append(kwargs)
            return {
                "Body": next(self.bodies),
                "VersionId": "v1",
                "ETag": '"etag"',
                "ContentType": "application/pdf",
            }

    client = RetryClient()
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._verify_staged_sync(source, client=client)
    verified = storage._verify_staged_sync(source, client=client)

    assert caught.value.stage is StorageStage.SOURCE_READ
    assert verified.verified_sha256 == sha
    assert len(client.get_calls) == 2
    assert all(call["VersionId"] == "v1" for call in client.get_calls)
    assert first.closed is True
    assert second.closed is True


def test_no_such_bucket_is_infrastructure_not_source_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.get_object = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        s3_error("NoSuchBucket", "GetObject", status=404)
    )
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._verify_staged_sync(staged_ref(sha=sha, version_id="v1"), client=client)

    assert caught.value.stage is StorageStage.SOURCE_GET


def test_masked_access_denied_404_is_not_object_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.get_object = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        s3_error("AccessDenied", "GetObject", status=404)
    )
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._verify_staged_sync(staged_ref(sha=sha, version_id="v1"), client=client)

    assert caught.value.stage is StorageStage.SOURCE_GET


@pytest.mark.parametrize(
    "failure",
    [
        s3_error("InternalError", "GetObject", status=500),
        EndpointConnectionError(endpoint_url="http://minio:9000"),
    ],
)
def test_source_get_server_and_transport_failures_are_infrastructure(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.get_object = lambda **kwargs: (_ for _ in ()).throw(failure)  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._verify_staged_sync(staged_ref(sha=sha, version_id="v1"), client=client)

    assert caught.value.stage is StorageStage.SOURCE_GET


def test_explicit_missing_source_version_is_identity_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    source = staged_ref(sha=sha, version_id="v1")
    client = FakeS3(source_bytes=data, source_version="v1")
    client.get_object = lambda **kwargs: (_ for _ in ()).throw(s3_error("NoSuchVersion"))  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StagedSourceUnavailable) as caught:
        storage._verify_staged_sync(source, client=client)

    assert caught.value.source is source


@pytest.mark.parametrize(
    ("code", "error_type", "stage"),
    [
        ("PreconditionFailed", StagedSourceChanged, None),
        ("412", StagedSourceChanged, None),
        ("NoSuchKey", StagedSourceUnavailable, None),
        ("NoSuchVersion", StagedSourceUnavailable, None),
        ("404", StorageUnavailable, StorageStage.COPY),
        ("NoSuchBucket", StorageUnavailable, StorageStage.COPY),
        ("AccessDenied", StorageUnavailable, StorageStage.COPY),
        ("InternalError", StorageUnavailable, StorageStage.COPY),
    ],
)
def test_copy_error_classification_retains_exact_source_and_closes_body(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    error_type: type[Exception],
    stage: StorageStage | None,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    source = staged_ref(sha=sha, version_id="v1")
    client = FakeS3(source_bytes=data, source_version="v1")
    client.copy_object = lambda **kwargs: (_ for _ in ()).throw(s3_error(code, "CopyObject"))  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(error_type) as caught:
        storage._finalize_sync(source, "documents", client=client)

    if stage is not None:
        assert isinstance(caught.value, StorageUnavailable)
        assert caught.value.stage is stage
    assert source.locator.version_id == "v1"
    assert client.source_body.closed is True


def test_copy_http_412_maps_to_source_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    source = staged_ref(sha=sha, version_id="v1")
    client = FakeS3(source_bytes=data, source_version="v1")
    client.copy_object = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        s3_error("UnexpectedCode", "CopyObject", status=412)
    )
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StagedSourceChanged):
        storage._finalize_sync(source, "documents", client=client)

    assert client.source_body.closed is True


def test_existing_exact_retained_target_is_hashed_and_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.target_version = "target-orphan"
    client.target_bytes = data
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    result = storage._finalize_sync(
        staged_ref(sha=sha, version_id="v1", size=len(data)),
        "documents",
        client=client,
    )

    assert result.outcome is PromotionOutcome.ADOPTED_EXISTING
    assert result.target_version_id == "target-orphan"
    assert client.get_calls[-1]["VersionId"] == "target-orphan"
    assert client.copy_calls == []
    assert client.retention_calls[-1]["VersionId"] == "target-orphan"
    assert client.source_body.closed is True
    assert len(client.target_bodies) == 1
    assert client.target_bodies[0].closed is True


def test_target_head_failure_after_source_verify_is_infrastructure_and_source_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.head_object = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        s3_error("AccessDenied", "HeadObject")
    )
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.TARGET_HEAD
    assert client.source_body.closed is True
    assert client.copy_calls == []


def test_existing_target_get_failure_closes_verified_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.target_version = "target-orphan"
    client.target_bytes = data
    original_get = client.get_object

    def target_get_failure(**kwargs: Any) -> dict[str, Any]:
        if kwargs["Bucket"] == "documents":
            raise s3_error("AccessDenied", "GetObject")
        return original_get(**kwargs)

    client.get_object = target_get_failure  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.TARGET_GET
    assert client.source_body.closed is True


def test_existing_target_midstream_failure_closes_source_and_target_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.target_version = "target-orphan"
    client.target_bytes = data
    target_body = RaisingBody(data, OSError("target stream failed"))
    original_get = client.get_object

    def target_read_failure(**kwargs: Any) -> dict[str, Any]:
        if kwargs["Bucket"] == "documents":
            return {"Body": target_body, "VersionId": "target-orphan"}
        return original_get(**kwargs)

    client.get_object = target_read_failure  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.TARGET_READ
    assert client.source_body.closed is True
    assert target_body.closed is True


def test_existing_target_wrong_bytes_is_conflict_and_target_body_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    source = staged_ref(sha=sha, version_id="v1")
    client = FakeS3(source_bytes=data, source_version="v1")
    client.target_version = "target-orphan"
    client.target_bytes = b"imposter"
    target_body = FakeBody(b"imposter")
    original_get = client.get_object

    def tracked_get(**kwargs: Any) -> dict[str, Any]:
        if kwargs["Bucket"] == "documents":
            client.get_calls.append(kwargs)
            return {
                "Body": target_body,
                "VersionId": "target-orphan",
                "ContentType": "application/pdf",
            }
        return original_get(**kwargs)

    client.get_object = tracked_get  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(TargetIdentityConflict):
        storage._finalize_sync(source, "documents", client=client)

    assert target_body.closed is True
    assert client.copy_calls == []


@pytest.mark.parametrize("retain_until", [None, datetime.now(UTC) - timedelta(seconds=1)])
def test_missing_or_expired_retention_is_worm_failure(
    monkeypatch: pytest.MonkeyPatch, retain_until: datetime | None
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    retention = {} if retain_until is None else {"RetainUntilDate": retain_until}
    client.get_object_retention = lambda **kwargs: {"Retention": retention}  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(WormNotApplied):
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert client.source_body.closed is True


def test_retention_probe_failure_is_infrastructure_after_bodies_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.target_version = "target-orphan"
    client.target_bytes = data
    target_body = FakeBody(data)
    original_get = client.get_object

    def tracked_get(**kwargs: Any) -> dict[str, Any]:
        if kwargs["Bucket"] == "documents":
            return {"Body": target_body, "VersionId": "target-orphan"}
        return original_get(**kwargs)

    client.get_object = tracked_get  # type: ignore[method-assign]
    client.get_object_retention = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        s3_error("AccessDenied", "GetObjectRetention")
    )
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.RETENTION
    assert client.source_body.closed is True
    assert target_body.closed is True


def test_malformed_retention_response_is_infrastructure(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.get_object_retention = lambda **kwargs: {"Retention": None}  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.RETENTION
    assert client.source_body.closed is True


def test_copy_requires_valid_target_version_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    client.copy_object = lambda **kwargs: {"VersionId": "null"}  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.stage is StorageStage.COPY
    assert client.source_body.closed is True


def test_copied_target_size_divergence_is_identity_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = FakeS3(source_bytes=data, source_version="v1")
    original_head = client.head_object

    def wrong_size_after_copy(**kwargs: Any) -> dict[str, Any]:
        response = original_head(**kwargs)
        if "VersionId" in kwargs:
            response["ContentLength"] = len(data) + 1
        return response

    client.head_object = wrong_size_after_copy  # type: ignore[method-assign]
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(TargetIdentityConflict) as caught:
        storage._finalize_sync(staged_ref(sha=sha, version_id="v1"), "documents", client=client)

    assert caught.value.observed_sha256 is None
    assert caught.value.observed_size == len(data) + 1
    assert client.source_body.closed is True


def test_copy_ambiguity_is_adopted_on_retry_without_second_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    source = staged_ref(sha=sha, version_id="v1", size=len(data))

    class AmbiguousClient(FakeS3):
        def __init__(self) -> None:
            super().__init__(source_bytes=data, source_version="v1")
            self.source_bodies: list[FakeBody] = []
            self.copy_attempts = 0

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["Bucket"] == "staging":
                body = FakeBody(data)
                self.source_bodies.append(body)
                self.get_calls.append(kwargs)
                return {
                    "Body": body,
                    "VersionId": "v1",
                    "ETag": '"opaque-etag"',
                    "ContentType": "application/pdf",
                }
            return super().get_object(**kwargs)

        def copy_object(self, **kwargs: Any) -> dict[str, Any]:
            self.copy_attempts += 1
            self.copy_calls.append(kwargs)
            self.target_version = "target-ambiguous"
            self.target_bytes = data
            raise EndpointConnectionError(endpoint_url="http://minio:9000")

    client = AmbiguousClient()
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as first:
        storage._finalize_sync(source, "documents", client=client)
    result = storage._finalize_sync(source, "documents", client=client)

    assert first.value.stage is StorageStage.COPY
    assert result.outcome is PromotionOutcome.ADOPTED_EXISTING
    assert result.target_version_id == "target-ambiguous"
    assert client.copy_attempts == 1
    assert all(body.closed for body in client.source_bodies)


async def test_put_staging_bytes_requires_versioning_and_returns_exact_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"generated"
    sha = hashlib.sha256(data).hexdigest()

    class PutClient:
        def __init__(self) -> None:
            self.put_calls: list[dict[str, Any]] = []

        def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
            assert kwargs == {"Bucket": "staging"}
            return {"Status": "Enabled"}

        def put_object(self, **kwargs: Any) -> dict[str, str]:
            self.put_calls.append(kwargs)
            return {"VersionId": "put-v+/=1"}

    client = PutClient()
    monkeypatch.setattr(storage, "_client", lambda: client)
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    result = await storage.put_staging_bytes(data, sha, content_type="application/json")

    assert result == StagedObjectRef(
        locator=StagedVersionLocator(StagingDomain.STAGING, sha, "put-v+/=1"),
        expected_sha256=sha,
        content_type="application/json",
        expected_size=len(data),
    )
    assert client.put_calls == [
        {"Bucket": "staging", "Key": sha, "Body": data, "ContentType": "application/json"}
    ]


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        (None, None),
        ("Suspended", None),
        (None, EndpointConnectionError(endpoint_url="http://minio:9000")),
    ],
)
async def test_put_staging_bytes_refuses_before_put_unless_versioning_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
    status: str | None,
    failure: Exception | None,
) -> None:
    data = b"generated"
    sha = hashlib.sha256(data).hexdigest()

    class PutClient:
        def __init__(self) -> None:
            self.versioning_calls = 0
            self.put_calls: list[dict[str, Any]] = []

        def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
            self.versioning_calls += 1
            if failure is not None:
                raise failure
            return {"Status": status} if status is not None else {}

        def put_object(self, **kwargs: Any) -> dict[str, str]:
            self.put_calls.append(kwargs)
            return {"VersionId": "must-not-be-created"}

    client = PutClient()
    monkeypatch.setattr(storage, "_client", lambda: client)
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        await storage.put_staging_bytes(data, sha, content_type="application/json")

    assert caught.value.stage is StorageStage.VERSIONING
    assert client.versioning_calls == 1
    assert client.put_calls == []


@pytest.mark.parametrize("version_id", [None, "", "null", "v" * 1025])
async def test_put_staging_bytes_maps_invalid_store_version_to_infrastructure(
    monkeypatch: pytest.MonkeyPatch, version_id: str | None
) -> None:
    data = b"generated"
    sha = hashlib.sha256(data).hexdigest()

    class PutClient:
        def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
            return {"Status": "Enabled"}

        def put_object(self, **kwargs: Any) -> dict[str, str | None]:
            return {"VersionId": version_id}

    monkeypatch.setattr(storage, "_client", lambda: PutClient())
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")

    with pytest.raises(StorageUnavailable) as caught:
        await storage.put_staging_bytes(data, sha, content_type="application/json")

    assert caught.value.stage is StorageStage.STAGING_PUT


async def test_delete_staged_version_sends_exact_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class DeleteClient:
        def delete_object(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(storage, "_client", lambda: DeleteClient())
    monkeypatch.setattr(storage, "_import_staging_bucket", lambda: "imports")
    locator = StagedVersionLocator(StagingDomain.IMPORT_STAGING, "a" * 64, "exact-v1")

    await storage.delete_staged_version(locator)

    assert calls == [{"Bucket": "imports", "Key": "a" * 64, "VersionId": "exact-v1"}]


async def test_delete_staged_version_does_not_swallow_bucket_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeleteClient:
        def delete_object(self, **kwargs: Any) -> None:
            raise s3_error("NoSuchBucket", "DeleteObject")

    monkeypatch.setattr(storage, "_client", lambda: DeleteClient())
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")
    locator = StagedVersionLocator(StagingDomain.STAGING, "a" * 64, "exact-v1")

    with pytest.raises(StorageUnavailable) as caught:
        await storage.delete_staged_version(locator)

    assert caught.value.stage is StorageStage.CLEANUP


@pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchVersion", "404"])
async def test_delete_staged_version_treats_exact_object_absence_as_idempotent(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    class DeleteClient:
        def delete_object(self, **kwargs: Any) -> None:
            raise s3_error(code, "DeleteObject")

    monkeypatch.setattr(storage, "_client", lambda: DeleteClient())
    monkeypatch.setattr(storage, "_staging_bucket", lambda: "staging")
    locator = StagedVersionLocator(StagingDomain.STAGING, "a" * 64, "exact-v1")

    await storage.delete_staged_version(locator)
