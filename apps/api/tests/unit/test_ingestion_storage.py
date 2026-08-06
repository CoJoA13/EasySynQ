"""Exact-version import-staging locator and scanner storage contracts."""

from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError

from easysynq_api.services.ingestion import extract, storage
from easysynq_api.services.vault.staged_identity import (
    StagedObjectRef,
    StagedVersionLocator,
    StagingDomain,
    StagingVersionRequired,
    StorageStage,
    StorageUnavailable,
    UploadIdentityMismatch,
)

pytestmark = pytest.mark.unit


def _source(*, version_id: str = "v/1+opaque", data: bytes = b"approved") -> StagedObjectRef:
    sha = hashlib.sha256(data).hexdigest()
    return StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.IMPORT_STAGING,
            object_key=sha,
            version_id=version_id,
        ),
        expected_sha256=sha,
        content_type="application/pdf",
        expected_size=len(data),
    )


def _s3_error(code: str, *, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadObject",
    )


def test_staged_uri_round_trips_url_significant_version_without_inventing_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(version_id="v/1+opaque?#%=")
    monkeypatch.setattr(storage, "_import_staging_bucket", lambda: "import-staging")

    uri = storage.format_staged_uri(source)

    assert uri == (
        f"s3://import-staging/{source.expected_sha256}?versionId=v%2F1%2Bopaque%3F%23%25%3D"
    )
    assert (
        storage.parse_staged_uri(
            uri,
            expected_sha256=source.expected_sha256,
            content_type="application/pdf",
            expected_size=len(b"approved"),
        )
        == source
    )


@pytest.mark.parametrize(
    "uri",
    [
        "https://import-staging/{sha}?versionId=v1",
        "//import-staging/{sha}?versionId=v1",
        "s3://foreign/{sha}?versionId=v1",
        "s3://user@import-staging/{sha}?versionId=v1",
        "s3://import-staging:9000/{sha}?versionId=v1",
        "s3://import-staging/{sha}?versionId=v1#fragment",
        "s3://import-staging/{other}?versionId=v1",
        "s3://import-staging/{sha}?versionId=v1&versionId=v2",
        "s3://import-staging/{sha}?versionId=v1&extra=x",
        "s3://import-staging/{sha}?versionId=",
        "s3://import-staging/{sha}?versionId=null",
        "s3://import-staging/{sha}?versionId={oversized}",
        "s3://import-staging/{sha}",
    ],
)
def test_parse_staged_uri_rejects_malformed_or_legacy_locator_with_stable_type(
    monkeypatch: pytest.MonkeyPatch, uri: str
) -> None:
    sha = "a" * 64
    monkeypatch.setattr(storage, "_import_staging_bucket", lambda: "import-staging")
    rendered = uri.format(sha=sha, other="b" * 64, oversized="v" * 1025)

    with pytest.raises(StagingVersionRequired):
        storage.parse_staged_uri(
            rendered,
            expected_sha256=sha,
            content_type="application/pdf",
            expected_size=123,
        )


class _Body:
    def __init__(self, data: bytes, *, error: BaseException | None = None) -> None:
        self._stream = io.BytesIO(data)
        self._error = error
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self._error is not None:
            raise self._error
        return self._stream.read(size)

    def close(self) -> None:
        self.closed = True


class _FakeS3:
    def __init__(
        self,
        *,
        versioning: str | None = "Enabled",
        temp_version: str | None = "tmp/v+1",
        canonical_version: str | None = None,
        final_version: str | None = "canonical/v+2",
        canonical_bytes: bytes | None = None,
        canonical_head_error: BaseException | None = None,
        canonical_get_error: BaseException | None = None,
    ) -> None:
        self.versioning = versioning
        self.temp_version = temp_version
        self.canonical_version = canonical_version
        self.final_version = final_version
        self.canonical_bytes = canonical_bytes
        self.canonical_head_error = canonical_head_error
        self.canonical_get_error = canonical_get_error
        self.temp_bytes = b""
        self.upload_calls: list[dict[str, Any]] = []
        self.head_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.copy_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
        return {"Status": self.versioning} if self.versioning is not None else {}

    def upload_fileobj(self, fileobj: Any, bucket: str, key: str, **kwargs: Any) -> None:
        self.upload_calls.append({"Bucket": bucket, "Key": key, **kwargs})
        chunks: list[bytes] = []
        while chunk := fileobj.read(8192):
            chunks.append(chunk)
        self.temp_bytes = b"".join(chunks)

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.head_calls.append(kwargs)
        if str(kwargs["Key"]).startswith("_tmp/"):
            return {"VersionId": self.temp_version, "ETag": '"temp-etag/opaque"'}
        if self.canonical_head_error is not None:
            raise self.canonical_head_error
        if self.canonical_version is None:
            raise _s3_error("404", status=404)
        return {"VersionId": self.canonical_version}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        if self.canonical_get_error is not None:
            raise self.canonical_get_error
        data = self.canonical_bytes if self.canonical_bytes is not None else self.temp_bytes
        return {
            "Body": _Body(data),
            "VersionId": kwargs["VersionId"],
            "ETag": '"canonical-etag"',
            "ContentType": "application/pdf",
        }

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        self.copy_calls.append(kwargs)
        self.canonical_bytes = self.temp_bytes
        return {"VersionId": self.final_version}

    def delete_object(self, **kwargs: Any) -> None:
        self.delete_calls.append(kwargs)


def _stage(monkeypatch: pytest.MonkeyPatch, client: _FakeS3, data: bytes = b"approved") -> Any:
    monkeypatch.setattr(storage, "_client", lambda: client)
    monkeypatch.setattr(storage, "_import_staging_bucket", lambda: "import-staging")
    return storage._stage_sync(io.BytesIO(data), content_type="application/pdf")


@pytest.mark.parametrize("status", [None, "Suspended"])
def test_stage_requires_enabled_versioning_before_upload_or_object_access(
    monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    client = _FakeS3(versioning=status)

    with pytest.raises(StorageUnavailable) as caught:
        _stage(monkeypatch, client)

    assert caught.value.stage is StorageStage.VERSIONING
    assert client.upload_calls == []
    assert client.head_calls == []
    assert client.copy_calls == []


def test_true_canonical_404_copies_and_pins_every_temp_and_final_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = _FakeS3(canonical_head_error=_s3_error("404", status=404))

    result = _stage(monkeypatch, client, data)

    tmp_key = client.upload_calls[0]["Key"]
    assert client.upload_calls[0]["Bucket"] == "import-staging"
    assert client.upload_calls[0]["ExtraArgs"] == {"ContentType": "application/pdf"}
    assert client.copy_calls == [
        {
            "Bucket": "import-staging",
            "Key": sha,
            "CopySource": {
                "Bucket": "import-staging",
                "Key": tmp_key,
                "VersionId": "tmp/v+1",
            },
            "CopySourceIfMatch": '"temp-etag/opaque"',
        }
    ]
    assert client.delete_calls == [
        {"Bucket": "import-staging", "Key": tmp_key, "VersionId": "tmp/v+1"}
    ]
    assert client.get_calls == [
        {"Bucket": "import-staging", "Key": sha, "VersionId": "canonical/v+2"}
    ]
    assert result.version_id == "canonical/v+2"
    assert result.source.locator.version_id == "canonical/v+2"
    assert result.staged_blob_uri.endswith("?versionId=canonical%2Fv%2B2")


@pytest.mark.parametrize(
    ("error", "expected_stage"),
    [
        (_s3_error("AccessDenied", status=403), StorageStage.TARGET_HEAD),
        (_s3_error("InternalError", status=500), StorageStage.TARGET_HEAD),
        (_s3_error("NoSuchBucket", status=404), StorageStage.TARGET_HEAD),
    ],
)
def test_canonical_head_errors_other_than_object_absence_never_copy(
    monkeypatch: pytest.MonkeyPatch,
    error: ClientError,
    expected_stage: StorageStage,
) -> None:
    client = _FakeS3(canonical_head_error=error)

    with pytest.raises(StorageUnavailable) as caught:
        _stage(monkeypatch, client)

    assert caught.value.stage is expected_stage
    assert client.copy_calls == []
    assert client.delete_calls[0]["VersionId"] == "tmp/v+1"


@pytest.mark.parametrize("version_id", [None, "", "null", "v" * 1025])
def test_stage_rejects_invalid_temp_version_identity_without_key_delete(
    monkeypatch: pytest.MonkeyPatch, version_id: str | None
) -> None:
    client = _FakeS3(temp_version=version_id)

    with pytest.raises(StorageUnavailable) as caught:
        _stage(monkeypatch, client)

    assert caught.value.stage is StorageStage.STAGING_PUT
    assert client.copy_calls == []
    assert client.delete_calls == []


def test_canonical_reuse_fetches_and_verifies_the_captured_exact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"approved"
    sha = hashlib.sha256(data).hexdigest()
    client = _FakeS3(
        canonical_version="existing/v+7",
        canonical_bytes=data,
    )

    result = _stage(monkeypatch, client, data)

    assert client.copy_calls == []
    assert client.get_calls == [
        {"Bucket": "import-staging", "Key": sha, "VersionId": "existing/v+7"}
    ]
    assert result.version_id == "existing/v+7"
    assert result.source.locator.version_id == "existing/v+7"


def test_canonical_reuse_digest_mismatch_is_typed_and_never_copies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3(canonical_version="bad-v1", canonical_bytes=b"tampered")

    with pytest.raises(UploadIdentityMismatch):
        _stage(monkeypatch, client)

    assert client.copy_calls == []


def test_canonical_reuse_storage_failure_is_typed_and_never_copies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3(
        canonical_version="existing-v1",
        canonical_get_error=_s3_error("AccessDenied", status=403),
    )

    with pytest.raises(StorageUnavailable) as caught:
        _stage(monkeypatch, client)

    assert caught.value.stage is StorageStage.SOURCE_GET
    assert client.copy_calls == []


@pytest.mark.parametrize("version_id", ["", "null", "v" * 1025])
def test_canonical_reuse_requires_valid_captured_version_identity(
    monkeypatch: pytest.MonkeyPatch, version_id: str
) -> None:
    client = _FakeS3(canonical_version=version_id, canonical_bytes=b"approved")

    with pytest.raises(StorageUnavailable) as caught:
        _stage(monkeypatch, client)

    assert caught.value.stage is StorageStage.TARGET_HEAD
    assert client.get_calls == []
    assert client.copy_calls == []


@pytest.mark.parametrize("version_id", [None, "", "null", "v" * 1025])
def test_copy_requires_store_produced_canonical_version_identity(
    monkeypatch: pytest.MonkeyPatch, version_id: str | None
) -> None:
    client = _FakeS3(final_version=version_id)

    with pytest.raises(StorageUnavailable) as caught:
        _stage(monkeypatch, client)

    assert caught.value.stage is StorageStage.COPY
    assert client.delete_calls[0]["VersionId"] == "tmp/v+1"


@pytest.mark.parametrize("read_error", [None, OSError("read failed")])
def test_exact_fetch_uses_version_and_closes_body_on_success_or_read_failure(
    monkeypatch: pytest.MonkeyPatch, read_error: BaseException | None
) -> None:
    source = _source(version_id="v/1+opaque")
    body = _Body(b"approved", error=read_error)
    calls: list[dict[str, Any]] = []

    class FetchClient:
        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"Body": body, "VersionId": "v/1+opaque"}

    monkeypatch.setattr(storage, "_client", FetchClient)
    monkeypatch.setattr(storage, "_import_staging_bucket", lambda: "import-staging")

    if read_error is None:
        assert storage._fetch_sync(source) == b"approved"
    else:
        with pytest.raises(StorageUnavailable) as caught:
            storage._fetch_sync(source)
        assert caught.value.stage is StorageStage.SOURCE_READ

    assert calls == [
        {
            "Bucket": "import-staging",
            "Key": source.expected_sha256,
            "VersionId": "v/1+opaque",
        }
    ]
    assert body.closed is True


@pytest.mark.asyncio
async def test_extract_rejects_legacy_locator_with_stable_failure_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha = "a" * 64

    async def forbidden_fetch(_source: StagedObjectRef) -> bytes:
        raise AssertionError("legacy locator must be rejected before storage access")

    monkeypatch.setattr(storage, "fetch_staged_bytes", forbidden_fetch)
    file_row = SimpleNamespace(
        sha256=sha,
        staged_blob_uri=f"s3://import-staging/{sha}",
        mime_type="application/pdf",
        size_bytes=123,
        rel_path="legacy.pdf",
        filename="legacy.pdf",
        ext="pdf",
    )

    result = await extract._extract_one(
        SimpleNamespace(), file_row, ocr_enabled=False, ocr_language="eng"
    )

    assert result.failed is True
    assert result.error == "staging_version_required"
