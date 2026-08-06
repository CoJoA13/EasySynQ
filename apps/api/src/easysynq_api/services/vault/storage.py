"""Object-store access for the vault (boto3 / MinIO).

The ``api`` tier issues **presigned PUT/GET URLs** so bytes flow client↔MinIO directly (D1,
doc 15 §12). Promotion is the deliberate exception: the server pins the returned staging
``VersionId``, streams that exact source through SHA-256, then copies that version with its opaque
ETag as a precondition into a WORM bucket. Sync boto3 runs in worker threads to stay off the event
loop. Presigned URLs are signed against ``s3_public_endpoint`` (when set) so the browser-facing host
matches the SigV4 signature; server-side operations use ``s3_endpoint``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import hashlib
import hmac
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from ...config import get_settings
from .staged_identity import (
    PromotionOutcome,
    PromotionResult,
    StagedObjectRef,
    StagedSourceChanged,
    StagedSourceUnavailable,
    StagedVersionLocator,
    StagingDomain,
    StorageStage,
    StorageUnavailable,
    TargetIdentityConflict,
    UploadIdentityMismatch,
    VerifiedStagedObject,
    WormNotApplied,
)


@dataclasses.dataclass(frozen=True, slots=True)
class ObjectHead:
    exists: bool
    size: int | None = None
    retain_until: datetime.datetime | None = None
    content_type: str | None = None  # the Content-Type the client PUT (drives S7b render routing)


def _doc_bucket() -> str:
    return get_settings().s3_bucket_documents


def _records_bucket() -> str:
    """The records WORM bucket (object-locked, GOVERNANCE) — captured record evidence promotes here,
    kept apart from the documents vault (doc 06; provisioned in ``minio-init.sh``)."""
    return get_settings().s3_bucket_records


def _staging_bucket() -> str:
    return get_settings().s3_bucket_staging


def _import_staging_bucket() -> str:
    """The ingestion staging bucket (non-WORM) where S-ing-1 content-addresses included bytes.
    S-ing-5 commit promotes an exact staged version directly from here into a WORM bucket."""
    return get_settings().s3_bucket_import_staging


def _bucket_for_domain(domain: StagingDomain) -> str:
    if domain is StagingDomain.STAGING:
        return _staging_bucket()
    return _import_staging_bucket()


def _client(*, config: Any = None) -> Any:
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
        config=config,
    )


def _presign_client() -> Any:
    """The boto3 client used to *presign* — its ``endpoint_url`` is the browser-facing host
    (``s3_public_endpoint`` when set, else the internal ``s3_endpoint``). A presigned URL is
    SigV4-signed against this host, so the host the browser hits MUST equal what was signed; we sign
    against the public endpoint directly rather than rewriting the URL's host afterward (which would
    break the signature → ``SignatureDoesNotMatch``). ``generate_presigned_url`` makes no network
    call, so the public host need only be reachable by the browser, not by this process."""
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_public_endpoint or s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
    )


def _presign(method: str, key: str, bucket: str, params: dict[str, Any]) -> str:
    url: str = _presign_client().generate_presigned_url(
        method,
        Params={"Bucket": bucket, "Key": key, **params},
        ExpiresIn=get_settings().s3_presign_expiry_seconds,
    )
    return url


async def presign_put(sha256: str, content_type: str) -> str:
    """A presigned PUT into the **staging** bucket at ``{sha256}`` (plain bucket → no object-lock
    Content-MD5 requirement on the client). Check-in promotes it to the WORM documents bucket."""
    return await asyncio.to_thread(
        _presign, "put_object", sha256, _staging_bucket(), {"ContentType": content_type}
    )


async def presign_get(object_key: str, *, bucket: str | None = None) -> str:
    return await asyncio.to_thread(_presign, "get_object", object_key, bucket or _doc_bucket(), {})


def _head_sync(key: str, bucket: str) -> ObjectHead:
    from botocore.exceptions import ClientError

    client = _client()
    try:
        meta = client.head_object(Bucket=bucket, Key=key)
    except ClientError:
        return ObjectHead(exists=False)
    retain_until: datetime.datetime | None = None
    try:
        retention = client.get_object_retention(Bucket=bucket, Key=key)
        retain_until = retention.get("Retention", {}).get("RetainUntilDate")
    except ClientError:
        retain_until = None
    return ObjectHead(
        exists=True,
        size=int(meta["ContentLength"]),
        retain_until=retain_until,
        content_type=meta.get("ContentType"),
    )


async def head(object_key: str, *, bucket: str | None = None) -> ObjectHead:
    """Metadata-only probe: existence, size, and WORM retain-until (no byte transfer)."""
    return await asyncio.to_thread(_head_sync, object_key, bucket or _doc_bucket())


def _error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code")
    return code if isinstance(code, str) else None


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return None
    status = metadata.get("HTTPStatusCode")
    return status if isinstance(status, int) else None


def _is_object_absence(exc: BaseException, *, object_level_404: bool) -> bool:
    code = _error_code(exc)
    if code in {"NoSuchKey", "NoSuchVersion"}:
        return True
    return object_level_404 and code == "404"


def _require_staging_versioning_sync(domain: StagingDomain, client: Any) -> None:
    try:
        result = client.get_bucket_versioning(Bucket=_bucket_for_domain(domain))
    except Exception as exc:
        raise StorageUnavailable(StorageStage.VERSIONING, exc) from exc
    if result.get("Status") != "Enabled":
        raise StorageUnavailable(StorageStage.VERSIONING)


def _verify_staged_sync(
    source: StagedObjectRef, *, client: Any | None = None
) -> VerifiedStagedObject:
    storage_client = client or _client()
    _require_staging_versioning_sync(source.locator.domain, storage_client)
    try:
        response = storage_client.get_object(
            Bucket=_bucket_for_domain(source.locator.domain),
            Key=source.locator.object_key,
            VersionId=source.locator.version_id,
        )
    except Exception as exc:
        if _is_object_absence(exc, object_level_404=True):
            raise StagedSourceUnavailable(source) from exc
        raise StorageUnavailable(StorageStage.SOURCE_GET, exc) from exc

    body = response.get("Body")
    if body is None:
        raise StorageUnavailable(StorageStage.SOURCE_GET)
    try:
        if response.get("VersionId") != source.locator.version_id:
            raise StorageUnavailable(StorageStage.SOURCE_GET)
        etag = response.get("ETag")
        if not isinstance(etag, str) or not etag.strip():
            raise StorageUnavailable(StorageStage.SOURCE_GET)
        digest = hashlib.sha256()
        size = 0
        try:
            while True:
                chunk = body.read(_STREAM_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        except Exception as exc:
            raise StorageUnavailable(StorageStage.SOURCE_READ, exc) from exc
    finally:
        body.close()

    observed_sha256 = digest.hexdigest()
    if not hmac.compare_digest(observed_sha256, source.expected_sha256):
        raise UploadIdentityMismatch(
            source=source,
            expected_sha256=source.expected_sha256,
            observed_sha256=observed_sha256,
            expected_size=source.expected_size,
            observed_size=size,
            etag=etag,
            classification="digest_mismatch",
        )
    if source.expected_size is not None and size != source.expected_size:
        raise UploadIdentityMismatch(
            source=source,
            expected_sha256=source.expected_sha256,
            observed_sha256=observed_sha256,
            expected_size=source.expected_size,
            observed_size=size,
            etag=etag,
            classification="size_mismatch",
        )
    return VerifiedStagedObject(
        source=source,
        verified_sha256=observed_sha256,
        size=size,
        content_type=response.get("ContentType"),
        etag=etag,
    )


async def verify_staged(source: StagedObjectRef) -> VerifiedStagedObject:
    return await asyncio.to_thread(_verify_staged_sync, source)


def _target_head_sync(client: Any, target_bucket: str, target_key: str) -> dict[str, Any] | None:
    try:
        response: dict[str, Any] = client.head_object(Bucket=target_bucket, Key=target_key)
        return response
    except Exception as exc:
        if _is_object_absence(exc, object_level_404=True):
            return None
        raise StorageUnavailable(StorageStage.TARGET_HEAD, exc) from exc


def _require_store_version_id(value: Any, stage: StorageStage) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 1024 or value == "null":
        raise StorageUnavailable(stage)
    return value


def _target_retention_sync(
    client: Any, *, target_bucket: str, target_key: str, target_version_id: str
) -> datetime.datetime:
    try:
        response = client.get_object_retention(
            Bucket=target_bucket,
            Key=target_key,
            VersionId=target_version_id,
        )
        retention = response.get("Retention", {})
        if not isinstance(retention, dict):
            raise TypeError("malformed retention response")
        retain_until = retention.get("RetainUntilDate")
    except Exception as exc:
        raise StorageUnavailable(StorageStage.RETENTION, exc) from exc
    now = datetime.datetime.now(datetime.UTC)
    if (
        not isinstance(retain_until, datetime.datetime)
        or retain_until.tzinfo is None
        or retain_until <= now
    ):
        raise WormNotApplied(
            target_bucket=target_bucket,
            target_key=target_key,
            target_version_id=target_version_id,
        )
    return retain_until


def _adopt_target_sync(
    verified: VerifiedStagedObject,
    target_bucket: str,
    target_version_id: str,
    client: Any,
) -> PromotionResult:
    target_key = verified.source.locator.object_key
    try:
        response = client.get_object(
            Bucket=target_bucket,
            Key=target_key,
            VersionId=target_version_id,
        )
    except Exception as exc:
        raise StorageUnavailable(StorageStage.TARGET_GET, exc) from exc
    body = response.get("Body")
    if body is None:
        raise StorageUnavailable(StorageStage.TARGET_GET)
    try:
        if response.get("VersionId") != target_version_id:
            raise StorageUnavailable(StorageStage.TARGET_GET)
        digest = hashlib.sha256()
        size = 0
        try:
            while True:
                chunk = body.read(_STREAM_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        except Exception as exc:
            raise StorageUnavailable(StorageStage.TARGET_READ, exc) from exc
    finally:
        body.close()
    observed_sha256 = digest.hexdigest()
    if not hmac.compare_digest(observed_sha256, verified.verified_sha256) or size != verified.size:
        raise TargetIdentityConflict(
            source=verified.source,
            target_bucket=target_bucket,
            target_key=target_key,
            target_version_id=target_version_id,
            observed_sha256=observed_sha256,
            observed_size=size,
        )
    retain_until = _target_retention_sync(
        client,
        target_bucket=target_bucket,
        target_key=target_key,
        target_version_id=target_version_id,
    )
    return PromotionResult(
        outcome=PromotionOutcome.ADOPTED_EXISTING,
        verified_sha256=verified.verified_sha256,
        size=verified.size,
        content_type=response.get("ContentType"),
        retain_until=retain_until,
        source=verified.source,
        source_etag=verified.etag,
        target_bucket=target_bucket,
        target_key=target_key,
        target_version_id=target_version_id,
    )


def _verify_copied_target_sync(
    verified: VerifiedStagedObject,
    target_bucket: str,
    target_version_id: str,
    client: Any,
) -> PromotionResult:
    target_key = verified.source.locator.object_key
    try:
        meta = client.head_object(
            Bucket=target_bucket,
            Key=target_key,
            VersionId=target_version_id,
        )
    except Exception as exc:
        raise StorageUnavailable(StorageStage.TARGET_HEAD, exc) from exc
    if meta.get("VersionId") != target_version_id:
        raise StorageUnavailable(StorageStage.TARGET_HEAD)
    try:
        size = int(meta["ContentLength"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageUnavailable(StorageStage.TARGET_HEAD, exc) from exc
    if size != verified.size:
        raise TargetIdentityConflict(
            source=verified.source,
            target_bucket=target_bucket,
            target_key=target_key,
            target_version_id=target_version_id,
            observed_sha256=None,
            observed_size=size,
        )
    retain_until = _target_retention_sync(
        client,
        target_bucket=target_bucket,
        target_key=target_key,
        target_version_id=target_version_id,
    )
    return PromotionResult(
        outcome=PromotionOutcome.COPIED,
        verified_sha256=verified.verified_sha256,
        size=size,
        content_type=meta.get("ContentType") or verified.content_type,
        retain_until=retain_until,
        source=verified.source,
        source_etag=verified.etag,
        target_bucket=target_bucket,
        target_key=target_key,
        target_version_id=target_version_id,
    )


def _finalize_sync(
    source: StagedObjectRef,
    target_bucket: str,
    *,
    client: Any | None = None,
    before_copy: Callable[[], None] | None = None,
) -> PromotionResult:
    storage_client = client or _client()
    verified = _verify_staged_sync(source, client=storage_client)
    target_key = source.locator.object_key
    existing = _target_head_sync(storage_client, target_bucket, target_key)
    if existing is not None:
        target_version_id = _require_store_version_id(
            existing.get("VersionId"), StorageStage.TARGET_HEAD
        )
        return _adopt_target_sync(verified, target_bucket, target_version_id, storage_client)

    if before_copy is not None:
        before_copy()
    try:
        copied = storage_client.copy_object(
            Bucket=target_bucket,
            Key=target_key,
            CopySource={
                "Bucket": _bucket_for_domain(source.locator.domain),
                "Key": source.locator.object_key,
                "VersionId": source.locator.version_id,
            },
            CopySourceIfMatch=verified.etag,
        )
    except Exception as exc:
        code = _error_code(exc)
        if code in {"PreconditionFailed", "412"} or _http_status(exc) == 412:
            raise StagedSourceChanged(source) from exc
        if code in {"NoSuchKey", "NoSuchVersion"}:
            raise StagedSourceUnavailable(source) from exc
        raise StorageUnavailable(StorageStage.COPY, exc) from exc
    target_version_id = _require_store_version_id(copied.get("VersionId"), StorageStage.COPY)
    return _verify_copied_target_sync(verified, target_bucket, target_version_id, storage_client)


async def promote_worm(source: StagedObjectRef, *, target_bucket: str) -> PromotionResult:
    return await asyncio.to_thread(_finalize_sync, source, target_bucket)


def _fetch_bytes_sync(object_key: str, bucket: str) -> bytes:
    client = _client()
    body: bytes = client.get_object(Bucket=bucket, Key=object_key)["Body"].read()
    return body


async def fetch_bytes(object_key: str, *, bucket: str | None = None) -> bytes:
    """Read a blob's bytes server-side (the **worker** path: the mirror writer pulls Effective
    blobs to disk). Unlike the api tier — which only ever presigns so bytes flow client↔MinIO (D1)
    — the worker reads object bytes directly. Runs the sync boto3 ``get_object`` off the event loop.
    Reads are unaffected by WORM object-lock (it blocks writes/deletes, not GETs)."""
    return await asyncio.to_thread(_fetch_bytes_sync, object_key, bucket or _doc_bucket())


_STREAM_CHUNK = 1 << 20  # 1 MiB


async def stream_object(object_key: str, *, bucket: str) -> AsyncIterator[bytes]:
    """Yield a blob's bytes in fixed chunks for a StreamingResponse — bounded memory (unlike
    :func:`fetch_bytes`, which materialises the whole object). The **API** uses this for the public
    guest pack download: every access is gated + audited per request (so a revoke takes effect on
    the next request) and bytes never sit in RAM in full. boto3 ``get_object`` returns a streaming
    Body; each ``read`` runs off the loop. WORM object-lock blocks writes/deletes, not GETs."""

    def _open() -> Any:
        return _client().get_object(Bucket=bucket, Key=object_key)["Body"]

    body = await asyncio.to_thread(_open)
    try:
        while True:
            chunk: bytes = await asyncio.to_thread(body.read, _STREAM_CHUNK)
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(body.close)


def _hash_object_sync(object_key: str, bucket: str) -> str:
    digest = hashlib.sha256()
    body = _client().get_object(Bucket=bucket, Key=object_key)["Body"]
    try:
        while True:
            chunk: bytes = body.read(_STREAM_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        body.close()
    return digest.hexdigest()


async def hash_object(object_key: str, *, bucket: str | None = None) -> str:
    """Stream-hash a blob's bytes server-side (the S-drift-3 D1 verify read): sha256 over 1 MiB
    chunks — bounded memory unlike :func:`fetch_bytes`, which materialises the whole object. The
    **internal** worker path (D1 reads bytes directly, never presigns). WORM object-lock blocks
    writes/deletes, not GETs."""
    return await asyncio.to_thread(_hash_object_sync, object_key, bucket or _doc_bucket())


def _put_bytes_sync(data: bytes, object_key: str, bucket: str, content_type: str) -> dict[str, Any]:
    response: dict[str, Any] = _client().put_object(
        Bucket=bucket, Key=object_key, Body=data, ContentType=content_type
    )
    return response


async def put_bytes(
    data: bytes, object_key: str, *, bucket: str, content_type: str = "application/octet-stream"
) -> None:
    """Write bytes server-side (the **worker** path: the renderer caches a generated PDF rendition).
    Targets the **non-WORM** renditions bucket — renditions are derived + rebuildable (doc 14 §5.4),
    so this is a plain ``put_object`` (not the staged exact-version WORM promotion cycle controlled
    source bytes take). Off the event loop."""
    await asyncio.to_thread(_put_bytes_sync, data, object_key, bucket, content_type)


async def put_staging_bytes(
    data: bytes, sha256: str, *, content_type: str = "application/octet-stream"
) -> StagedObjectRef:
    """Write server-generated bytes into the plain **staging** bucket at ``key = sha256``, so a
    subsequent :func:`promote_worm` can promote them into a WORM bucket (the S-rec-3 form-template
    check-in: the controlled content of a Form/Template *is* its canonical-serialized field schema,
    written server-side rather than client-uploaded). Off the event loop."""

    def _put() -> StagedObjectRef:
        client = _client()
        _require_staging_versioning_sync(StagingDomain.STAGING, client)
        try:
            response = client.put_object(
                Bucket=_staging_bucket(),
                Key=sha256,
                Body=data,
                ContentType=content_type,
            )
        except Exception as exc:
            raise StorageUnavailable(StorageStage.STAGING_PUT, exc) from exc
        version_id = _require_store_version_id(response.get("VersionId"), StorageStage.STAGING_PUT)
        return StagedObjectRef(
            locator=StagedVersionLocator(
                domain=StagingDomain.STAGING,
                object_key=sha256,
                version_id=version_id,
            ),
            expected_sha256=sha256,
            content_type=content_type,
            expected_size=len(data),
        )

    return await asyncio.to_thread(_put)


def _delete_staged_version_sync(locator: StagedVersionLocator) -> None:
    try:
        _client().delete_object(
            Bucket=_bucket_for_domain(locator.domain),
            Key=locator.object_key,
            VersionId=locator.version_id,
        )
    except Exception as exc:
        if _is_object_absence(exc, object_level_404=True):
            return
        raise StorageUnavailable(StorageStage.CLEANUP, exc) from exc


async def delete_staged_version(locator: StagedVersionLocator) -> None:
    """Delete exactly one staged version; exact absence is an idempotent success."""
    await asyncio.to_thread(_delete_staged_version_sync, locator)


@dataclasses.dataclass(frozen=True, slots=True)
class WormProbeResult:
    verified: bool
    retain_until: datetime.datetime | None
    detail: str


def _worm_probe_sync(bucket: str) -> WormProbeResult:
    """Prove the bucket enforces WORM (doc 08 §7.2 / gate G-B): PUT a tiny probe → confirm it came
    back object-locked (a future retain-until) → attempt to delete THAT VERSION with no bypass and
    expect a denial. A non-versioned/non-locked bucket yields no VersionId → not verified. Cleanup
    is best-effort governance-bypass (else the probe is litter that expires with the retention)."""
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    # Short timeouts so a dead/unreachable endpoint fails fast (the probe diagnoses storage — it
    # must not hang ~60s on the default boto3 timeout).
    client = _client(config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}))
    key = f"_worm-probe/{uuid.uuid4().hex}"
    try:
        put = client.put_object(Bucket=bucket, Key=key, Body=b"easysynq-worm-probe")
    except (ClientError, BotoCoreError) as exc:
        # A missing bucket / unreachable MinIO is "not verified" (→ 422), NOT an opaque 500 — the
        # whole point of this step is to diagnose storage.
        return WormProbeResult(
            verified=False,
            retain_until=None,
            detail=f"vault bucket not reachable or does not exist: {exc!r}"[:200],
        )
    version_id = put.get("VersionId")
    retain_until = _head_sync(key, bucket).retain_until

    delete_denied = False
    if version_id:
        try:
            # delete the locked VERSION without BypassGovernanceRetention — WORM must refuse this.
            client.delete_object(Bucket=bucket, Key=key, VersionId=version_id)
        except ClientError:
            delete_denied = True
        try:  # cleanup (bypass) — best-effort; if not permitted the version expires with retention
            client.delete_object(
                Bucket=bucket, Key=key, VersionId=version_id, BypassGovernanceRetention=True
            )
        except ClientError:
            pass
    else:
        try:  # non-versioned bucket: no WORM possible; just remove the plain probe object
            client.delete_object(Bucket=bucket, Key=key)
        except ClientError:
            pass

    verified = bool(version_id) and retain_until is not None and delete_denied
    if not version_id:
        detail = "bucket is not versioned/object-locked — no WORM"
    elif retain_until is None:
        detail = "probe object carries no retain-until — object-lock default retention not applied"
    elif not delete_denied:
        detail = "early delete of the object-locked probe version was ALLOWED — WORM not enforced"
    else:
        detail = "early delete of the object-locked probe version was denied — WORM enforced"
    return WormProbeResult(verified=verified, retain_until=retain_until, detail=detail)


async def worm_probe(bucket: str | None = None) -> WormProbeResult:
    """Run :func:`_worm_probe_sync` off the event loop against ``bucket`` (default: the WORM
    documents bucket). Used by the S8a/S8b setup wizard's G-B gate verification."""
    return await asyncio.to_thread(_worm_probe_sync, bucket or _doc_bucket())


def _purge_object_sync(object_key: str, bucket: str, bypass_governance: bool) -> int:
    """Delete EVERY version + delete-marker of ``object_key`` from a versioned bucket. Returns the
    count removed. Idempotent — a key with no remaining versions is a no-op success. Raises
    ``ClientError``/``BotoCoreError`` on a real failure (e.g. an ``AccessDenied`` when COMPLIANCE
    mode refuses ``BypassGovernanceRetention``) so the caller stays fail-closed."""
    client = _client()
    paginator = client.get_paginator("list_object_versions")
    removed = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=object_key):
        # Versions and DeleteMarkers are deleted the same way — by their own VersionId (deleting a
        # marker's version removes the marker; a plain delete would just add another marker).
        for entry in (*page.get("Versions", []), *page.get("DeleteMarkers", [])):
            if entry.get("Key") != object_key:  # Prefix can match longer keys — exact-match only
                continue
            kwargs: dict[str, Any] = {
                "Bucket": bucket,
                "Key": object_key,
                "VersionId": entry["VersionId"],
            }
            if bypass_governance:
                kwargs["BypassGovernanceRetention"] = True
            client.delete_object(**kwargs)
            removed += 1
    return removed


async def purge_object(object_key: str, *, bucket: str, bypass_governance: bool = False) -> int:
    """Physically destroy a record's WORM evidence (slice S-rec-2, doc 06 §5.3). Removes every
    version + delete-marker of ``object_key`` in ``bucket``; returns the count removed. Idempotent
    (re-purging an already-gone object is a no-op) and **fail-closed at the storage layer** — raises
    on any real storage failure.

    ⚠ Since the Batch-5 review (2026-07-22) the disposition callers COMMIT the ``DISPOSED``
    tombstone + the blob-row delete + a ``pending_blob_purge`` marker BEFORE calling this, and
    treat a raise as a *deferral* signal — ``_purge_marked`` / ``reap_pending_blob_purges`` catch
    it and leave the marker for the hourly reaper. So a raise here NO LONGER rolls back a
    disposition (the earlier 'never a tombstone over present bytes' guarantee is now a brief,
    reaper-recoverable window); blob-row-iff-bytes is instead preserved by deleting the ``blob``
    row at that commit.

    ``bypass_governance=True`` (the R27 dual-control destroy-under-legal-order hatch) overrides an
    *unexpired* GOVERNANCE object-lock; it is denied under COMPLIANCE mode (an honest
    ``AccessDenied`` the caller surfaces). The normal/sweep path passes ``False`` (lock expired)."""
    return await asyncio.to_thread(_purge_object_sync, object_key, bucket, bypass_governance)
