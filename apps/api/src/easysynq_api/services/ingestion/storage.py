"""Content-addressed staging copy for the scan (slice S-ing-1, doc 09 §4.1).

The scan walker streams each **included** source file once into the non-WORM ``import-staging``
bucket,
content-addressed by its SHA-256 — so two identical files copy once (doc 09 §4.1) and an abandoned
import never touches the immutable vault (doc 09 §2/§15). The content address is unknown until the
bytes
are fully read, so the upload goes to a temp key while a hashing wrapper computes the digest in the
same
pass, then a server-side ``copy_object`` publishes it to ``key = sha256`` (skipped when an exact,
verified canonical version already exists — the dedup) and only the captured temp version is
removed. ``upload_fileobj`` runs with ``use_threads=False`` so
the wrapper sees the bytes sequentially (a correct hash) and memory stays bounded (never the whole
file).

Unlike ``services/vault/storage.py`` (the api-tier, presign-centric, whole-bytes module) this is a
worker streaming path; it constructs its own plain boto3 client (no presign / public-endpoint
rewrite)."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from typing import Any, BinaryIO
from urllib.parse import parse_qs, quote, urlsplit

from ...config import get_settings
from ..vault import storage as vault_storage
from ..vault.staged_identity import (
    StagedObjectRef,
    StagedSourceUnavailable,
    StagedVersionLocator,
    StagingDomain,
    StagingVersionRequired,
    StorageStage,
    StorageUnavailable,
)


@dataclasses.dataclass(frozen=True, slots=True)
class StagedResult:
    sha256: str
    staged_blob_uri: str
    version_id: str
    size_bytes: int
    source: StagedObjectRef


def _import_staging_bucket() -> str:
    return get_settings().s3_bucket_import_staging


def _client() -> Any:
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
    )


def format_staged_uri(source: StagedObjectRef) -> str:
    """Serialize one exact import-staging identity without embedding caller-owned metadata."""
    if source.locator.domain is not StagingDomain.IMPORT_STAGING:
        raise ValueError("only import-staging sources have ingestion locators")
    return (
        f"s3://{_import_staging_bucket()}/{source.locator.object_key}"
        f"?versionId={quote(source.locator.version_id, safe='')}"
    )


def parse_staged_uri(
    uri: str,
    *,
    expected_sha256: str,
    content_type: str,
    expected_size: int | None = None,
) -> StagedObjectRef:
    """Parse an exact locator and combine it with trusted caller-owned identity metadata."""
    try:
        parsed = urlsplit(uri)
        if (
            parsed.scheme != "s3"
            or parsed.netloc != _import_staging_bucket()
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.fragment
            or parsed.path != f"/{expected_sha256}"
        ):
            raise StagingVersionRequired
        query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
    except StagingVersionRequired:
        raise
    except (TypeError, ValueError) as exc:
        raise StagingVersionRequired from exc
    if set(query) != {"versionId"} or len(query["versionId"]) != 1:
        raise StagingVersionRequired
    raw_parts = parsed.query.split("&")
    if len(raw_parts) != 1 or not raw_parts[0].startswith("versionId="):
        raise StagingVersionRequired
    version_id = query["versionId"][0]
    if quote(version_id, safe="") != raw_parts[0].removeprefix("versionId="):
        raise StagingVersionRequired
    return StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.IMPORT_STAGING,
            object_key=expected_sha256,
            version_id=version_id,
        ),
        expected_sha256=expected_sha256,
        content_type=content_type,
        expected_size=expected_size,
    )


class _HashingReader:
    """Wraps a binary file object so ``upload_fileobj`` reading it also feeds a single SHA-256 (one
    disk pass). Non-seekable on purpose so boto3 streams it sequentially."""

    def __init__(self, fileobj: BinaryIO) -> None:
        import hashlib

        self._f = fileobj
        self._h = hashlib.sha256()
        self.size = 0

    def read(self, amt: int = -1) -> bytes:
        chunk = self._f.read(amt)
        self._h.update(chunk)
        self.size += len(chunk)
        return chunk

    def hexdigest(self) -> str:
        return self._h.hexdigest()


def _canonical_head(client: Any, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        result: dict[str, Any] = client.head_object(Bucket=bucket, Key=key)
        return result
    except Exception as exc:
        if vault_storage._is_object_absence(exc, object_level_404=True):
            return None
        raise StorageUnavailable(StorageStage.TARGET_HEAD, exc) from exc


def _stage_sync(fileobj: BinaryIO, *, content_type: str) -> StagedResult:
    from boto3.s3.transfer import TransferConfig

    client = _client()
    bucket = _import_staging_bucket()
    vault_storage._require_staging_versioning_sync(StagingDomain.IMPORT_STAGING, client)
    reader = _HashingReader(fileobj)
    tmp_key = f"_tmp/{uuid.uuid4().hex}"
    temp_version_id: str | None = None
    # Sequential (use_threads=False) so the hashing wrapper sees bytes in order; multipart keeps
    # memory
    # bounded for large files.
    try:
        client.upload_fileobj(
            reader,
            bucket,
            tmp_key,
            ExtraArgs={"ContentType": content_type},
            Config=TransferConfig(use_threads=False),
        )
    except Exception as exc:
        raise StorageUnavailable(StorageStage.STAGING_PUT, exc) from exc
    sha = reader.hexdigest()
    try:
        try:
            temp_head = client.head_object(Bucket=bucket, Key=tmp_key)
        except Exception as exc:
            raise StorageUnavailable(StorageStage.STAGING_PUT, exc) from exc
        temp_version_id = vault_storage._require_store_version_id(
            temp_head.get("VersionId"), StorageStage.STAGING_PUT
        )
        temp_etag = temp_head.get("ETag")
        if not isinstance(temp_etag, str) or not temp_etag.strip():
            raise StorageUnavailable(StorageStage.STAGING_PUT)

        canonical_head = _canonical_head(client, bucket, sha)
        if canonical_head is None:
            try:
                copied = client.copy_object(
                    Bucket=bucket,
                    Key=sha,
                    CopySource={
                        "Bucket": bucket,
                        "Key": tmp_key,
                        "VersionId": temp_version_id,
                    },
                    CopySourceIfMatch=temp_etag,
                )
            except Exception as exc:
                raise StorageUnavailable(StorageStage.COPY, exc) from exc
            canonical_version_id = vault_storage._require_store_version_id(
                copied.get("VersionId"), StorageStage.COPY
            )
        else:
            canonical_version_id = vault_storage._require_store_version_id(
                canonical_head.get("VersionId"), StorageStage.TARGET_HEAD
            )

        source = StagedObjectRef(
            locator=StagedVersionLocator(
                domain=StagingDomain.IMPORT_STAGING,
                object_key=sha,
                version_id=canonical_version_id,
            ),
            expected_sha256=sha,
            content_type=content_type,
            expected_size=reader.size,
        )
        vault_storage._verify_staged_sync(source, client=client)
    finally:
        if temp_version_id is not None:
            try:
                client.delete_object(
                    Bucket=bucket,
                    Key=tmp_key,
                    VersionId=temp_version_id,
                )
            except Exception as exc:
                if not vault_storage._is_object_absence(exc, object_level_404=True):
                    raise StorageUnavailable(StorageStage.CLEANUP, exc) from exc
    return StagedResult(
        sha256=sha,
        staged_blob_uri=format_staged_uri(source),
        version_id=canonical_version_id,
        size_bytes=reader.size,
        source=source,
    )


async def stage_stream(fileobj: BinaryIO, *, content_type: str) -> StagedResult:
    """Stream ``fileobj`` (positioned at 0) into ``import-staging`` content-addressed by its
    SHA-256,
    in one pass, off the event loop. Returns the digest + the canonical ``s3://…`` uri + the
    byte size."""
    return await asyncio.to_thread(_stage_sync, fileobj, content_type=content_type)


def _fetch_sync(source: StagedObjectRef) -> bytes:
    try:
        response = _client().get_object(
            Bucket=_import_staging_bucket(),
            Key=source.locator.object_key,
            VersionId=source.locator.version_id,
        )
    except Exception as exc:
        if vault_storage._is_object_absence(exc, object_level_404=True):
            raise StagedSourceUnavailable(source) from exc
        raise StorageUnavailable(StorageStage.SOURCE_GET, exc) from exc
    body = response.get("Body")
    if body is None:
        raise StorageUnavailable(StorageStage.SOURCE_GET)
    try:
        if response.get("VersionId") != source.locator.version_id:
            raise StorageUnavailable(StorageStage.SOURCE_GET)
        try:
            result: bytes = body.read()
        except Exception as exc:
            raise StorageUnavailable(StorageStage.SOURCE_READ, exc) from exc
        return result
    finally:
        body.close()


async def fetch_staged_bytes(source: StagedObjectRef) -> bytes:
    """Read a staged object's exact pinned version (S-ing-2 extract). The worker reads the staged
    copy — NOT the source tree or the current latest staging version. Off the event loop;
    whole-object (a 0-byte / junk file never reaches here — only included candidates carry a
    ``staged_blob_uri``)."""
    return await asyncio.to_thread(_fetch_sync, source)
