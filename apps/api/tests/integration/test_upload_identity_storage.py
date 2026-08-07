"""Real-MinIO proofs for exact-version staging and browser upload identity."""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Iterator
from typing import Any

import boto3
import httpx
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from easysynq_api.services.vault import storage
from easysynq_api.services.vault.staged_identity import (
    StagedObjectRef,
    StagedSourceChanged,
    StagedVersionLocator,
    StagingDomain,
    StorageStage,
    StorageUnavailable,
    UploadIdentityMismatch,
)


@pytest.fixture
def s3_client(_minio: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from easysynq_api.config import get_settings

    monkeypatch.setenv("S3_ENDPOINT", _minio["endpoint"])
    monkeypatch.setenv("S3_ACCESS_KEY", _minio["access_key"])
    monkeypatch.setenv("S3_SECRET_KEY", _minio["secret_key"])
    monkeypatch.setenv("S3_BUCKET_DOCUMENTS", "documents")
    monkeypatch.setenv("S3_BUCKET_STAGING", "staging")
    monkeypatch.setenv("S3_BUCKET_IMPORT_STAGING", "import-staging")
    get_settings.cache_clear()
    yield boto3.client(
        "s3",
        endpoint_url=_minio["endpoint"],
        aws_access_key_id=_minio["access_key"],
        aws_secret_access_key=_minio["secret_key"],
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )
    get_settings.cache_clear()


def _source_ref(
    data: bytes,
    version_id: str,
    *,
    domain: StagingDomain = StagingDomain.STAGING,
    claimed_sha256: str | None = None,
) -> StagedObjectRef:
    sha256 = claimed_sha256 or hashlib.sha256(data).hexdigest()
    return StagedObjectRef(
        locator=StagedVersionLocator(domain=domain, object_key=sha256, version_id=version_id),
        expected_sha256=sha256,
        content_type="application/octet-stream",
        expected_size=len(data),
    )


def _multipart_put(s3_client: Any, *, key: str, data: bytes) -> tuple[str, str]:
    from boto3.s3.transfer import TransferConfig

    s3_client.upload_fileobj(
        io.BytesIO(data),
        "staging",
        key,
        ExtraArgs={"ContentType": "application/octet-stream"},
        Config=TransferConfig(
            multipart_threshold=1,
            multipart_chunksize=5 * 1024 * 1024,
            use_threads=False,
        ),
    )
    head = s3_client.head_object(Bucket="staging", Key=key)
    return head["VersionId"], head["ETag"]


def _assert_missing(s3_client: Any, *, bucket: str, key: str) -> None:
    with pytest.raises(ClientError) as caught:
        s3_client.head_object(Bucket=bucket, Key=key)
    assert caught.value.response["Error"]["Code"] in {"404", "NoSuchKey"}


@pytest.mark.integration
def test_staging_bucket_versioning_returns_exact_put_identities(s3_client: Any) -> None:
    for bucket in ("staging", "import-staging"):
        assert s3_client.get_bucket_versioning(Bucket=bucket)["Status"] == "Enabled"
        response = s3_client.put_object(
            Bucket=bucket,
            Key=f"versioning-proof-{bucket}",
            Body=b"versioned",
        )
        assert response["VersionId"] not in {"", "null"}


@pytest.mark.integration
def test_staging_cors_exposes_version_and_etag_to_browser_javascript(s3_client: Any) -> None:
    url = s3_client.generate_presigned_url(
        "put_object",
        Params={"Bucket": "staging", "Key": "browser-cors-proof"},
        ExpiresIn=60,
    )
    preflight = httpx.options(
        url,
        headers={
            "Origin": "http://test",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code in {200, 204}, preflight.text
    assert preflight.headers["access-control-allow-origin"] == "http://test"
    assert "PUT" in preflight.headers["access-control-allow-methods"]
    allowed_headers = preflight.headers["access-control-allow-headers"].lower()
    assert allowed_headers == "*" or "content-type" in allowed_headers

    disallowed = httpx.options(
        url,
        headers={
            "Origin": "http://disallowed.test",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert not any(
        header.lower().startswith("access-control-allow-") for header in disallowed.headers
    )

    response = httpx.put(url, headers={"Origin": "http://test"}, content=b"browser-version")

    assert response.status_code == 200, response.text
    assert response.headers["x-amz-version-id"] not in {"", "null"}
    assert response.headers["etag"]
    browser_exposed = {
        header.strip().lower()
        for header in response.headers["access-control-expose-headers"].split(",")
    }
    assert "*" in browser_exposed or {"x-amz-version-id", "etag"}.issubset(browser_exposed)


@pytest.mark.integration
def test_staging_buckets_have_no_lifecycle_expiry(s3_client: Any) -> None:
    for bucket in ("staging", "import-staging"):
        with pytest.raises(ClientError) as caught:
            s3_client.get_bucket_lifecycle_configuration(Bucket=bucket)
        assert caught.value.response["Error"]["Code"] == "NoSuchLifecycleConfiguration"


@pytest.mark.integration
def test_multipart_etag_is_not_sha_and_correct_content_promotes(s3_client: Any) -> None:
    good = b"multipart-approved-content\n" * 240_000
    sha256 = hashlib.sha256(good).hexdigest()
    version_id, etag = _multipart_put(s3_client, key=sha256, data=good)
    assert "-" in etag.strip('"')
    assert etag.strip('"') != sha256

    result = storage._finalize_sync(_source_ref(good, version_id), "documents", client=s3_client)

    promoted = s3_client.get_object(
        Bucket="documents", Key=sha256, VersionId=result.target_version_id
    )["Body"].read()
    assert promoted == good


@pytest.mark.integration
def test_same_size_false_multipart_bytes_never_reach_worm_target(s3_client: Any) -> None:
    good = b"g" * (5 * 1024 * 1024 + 17)
    evil = b"e" * len(good)
    sha256 = hashlib.sha256(good).hexdigest()
    version_id, _etag = _multipart_put(s3_client, key=sha256, data=evil)

    with pytest.raises(UploadIdentityMismatch):
        storage._finalize_sync(
            _source_ref(evil, version_id, claimed_sha256=sha256),
            "documents",
            client=s3_client,
        )

    _assert_missing(s3_client, bucket="documents", key=sha256)


@pytest.mark.integration
def test_overwrite_between_verify_and_copy_never_promotes_newer_bytes(s3_client: Any) -> None:
    good = b"approved-version-one"
    evil = b"malicious-version-two"
    sha256 = hashlib.sha256(good).hexdigest()
    put_v1 = s3_client.put_object(Bucket="staging", Key=sha256, Body=good)
    source = _source_ref(good, put_v1["VersionId"])

    def overwrite_with_evil_v2() -> None:
        s3_client.put_object(Bucket="staging", Key=sha256, Body=evil)

    try:
        result = storage._finalize_sync(
            source,
            "documents",
            client=s3_client,
            before_copy=overwrite_with_evil_v2,
        )
    except StagedSourceChanged:
        _assert_missing(s3_client, bucket="documents", key=sha256)
    else:
        target = s3_client.get_object(
            Bucket="documents", Key=sha256, VersionId=result.target_version_id
        )["Body"].read()
        assert hashlib.sha256(target).hexdigest() == sha256
        assert target == good
        assert target != evil


@pytest.mark.integration
def test_overwrite_same_bytes_still_copies_pinned_source_version_metadata(
    s3_client: Any,
) -> None:
    body = f"same-bytes-{uuid.uuid4().hex}".encode()
    sha256 = hashlib.sha256(body).hexdigest()
    put_v1 = s3_client.put_object(
        Bucket="staging",
        Key=sha256,
        Body=body,
        Metadata={"source-version": "v1"},
    )
    source = _source_ref(body, put_v1["VersionId"])

    def overwrite_with_same_bytes_v2() -> None:
        s3_client.put_object(
            Bucket="staging",
            Key=sha256,
            Body=body,
            Metadata={"source-version": "v2"},
        )

    result = storage._finalize_sync(
        source,
        "documents",
        client=s3_client,
        before_copy=overwrite_with_same_bytes_v2,
    )
    target = s3_client.head_object(
        Bucket="documents",
        Key=sha256,
        VersionId=result.target_version_id,
    )

    assert target["Metadata"] == {"source-version": "v1"}


@pytest.mark.integration
async def test_exact_rejected_version_delete_leaves_newer_version_readable(s3_client: Any) -> None:
    v1 = b"rejected-version-one"
    v2 = b"accepted-version-two"
    key = hashlib.sha256(v1).hexdigest()
    put_v1 = s3_client.put_object(Bucket="staging", Key=key, Body=v1)
    put_v2 = s3_client.put_object(Bucket="staging", Key=key, Body=v2)

    await storage.delete_staged_version(
        StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=key,
            version_id=put_v1["VersionId"],
        )
    )

    current = s3_client.get_object(Bucket="staging", Key=key)
    assert current["VersionId"] == put_v2["VersionId"]
    assert current["Body"].read() == v2
    with pytest.raises(ClientError) as caught:
        s3_client.get_object(Bucket="staging", Key=key, VersionId=put_v1["VersionId"])
    assert caught.value.response["Error"]["Code"] == "NoSuchVersion"


class _CopyFailingClient:
    def __init__(self, real: Any) -> None:
        self.real = real
        self.source_gets: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.source_gets.append(kwargs)
        return self.real.get_object(**kwargs)

    def copy_object(self, **_kwargs: Any) -> dict[str, Any]:
        raise ClientError(
            {"Error": {"Code": "InternalError"}, "ResponseMetadata": {"HTTPStatusCode": 500}},
            "CopyObject",
        )


@pytest.mark.integration
def test_copy_failure_preserves_exact_verified_source_for_retry(s3_client: Any) -> None:
    source_bytes = b"retryable-exact-source"
    sha256 = hashlib.sha256(source_bytes).hexdigest()
    put = s3_client.put_object(Bucket="staging", Key=sha256, Body=source_bytes)
    source = _source_ref(source_bytes, put["VersionId"])
    failing = _CopyFailingClient(s3_client)

    with pytest.raises(StorageUnavailable) as caught:
        storage._finalize_sync(source, "documents", client=failing)

    assert caught.value.stage is StorageStage.COPY
    assert failing.source_gets[0]["VersionId"] == put["VersionId"]
    retry_source = s3_client.get_object(Bucket="staging", Key=sha256, VersionId=put["VersionId"])[
        "Body"
    ].read()
    assert retry_source == source_bytes
    _assert_missing(s3_client, bucket="documents", key=sha256)
