"""Object-store access for the vault (boto3 / MinIO) — presigned I/O only, never proxied.

The ``api`` tier issues **presigned PUT/GET URLs** so bytes flow client↔MinIO directly (D1,
doc 15 §12); it only ever does metadata ops (``head_object``, ``get_object_retention``,
server-side ``copy_object``). The client uploads to the plain ``staging`` bucket at
``key = sha256``; check-in server-side-copies into the ``documents`` bucket, whose GOVERNANCE
default retention auto-WORM-locks the object on creation (so check-in can confirm WORM before the
version row commits). Sync boto3 runs in a worker thread to stay off the event loop, mirroring
``readiness._check_minio``. Presigned URLs are rewritten to ``s3_public_endpoint`` for the browser.

S3 trusts the client-computed ``sha256`` as the content-addressed key (existence + size are
verified via ``head_object``; bytes are never re-hashed by the api). Cryptographic server-side
hash verification (S3 ChecksumSHA256 or a worker re-hash) is a later hardening.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ...config import get_settings


@dataclasses.dataclass(frozen=True, slots=True)
class ObjectHead:
    exists: bool
    size: int | None = None
    retain_until: datetime.datetime | None = None
    content_type: str | None = None  # the Content-Type the client PUT (drives S7b render routing)


def _doc_bucket() -> str:
    return get_settings().s3_bucket_documents


def _staging_bucket() -> str:
    return get_settings().s3_bucket_staging


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


def _to_public(url: str) -> str:
    """Rewrite a presigned URL's scheme+host to the browser-reachable origin (preserving the
    path + all signature query params), so clients in real self-hosted deploys can reach MinIO."""
    public = get_settings().s3_public_endpoint
    if not public:
        return url
    pub = urlsplit(public)
    parts = urlsplit(url)
    return urlunsplit(
        (pub.scheme or parts.scheme, pub.netloc, parts.path, parts.query, parts.fragment)
    )


def _presign(method: str, key: str, bucket: str, params: dict[str, Any]) -> str:
    client = _client()
    url: str = client.generate_presigned_url(
        method,
        Params={"Bucket": bucket, "Key": key, **params},
        ExpiresIn=get_settings().s3_presign_expiry_seconds,
    )
    return _to_public(url)


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


def _finalize_sync(sha256: str) -> ObjectHead:
    from botocore.exceptions import ClientError

    client = _client()
    try:
        client.head_object(Bucket=_staging_bucket(), Key=sha256)
    except ClientError:
        return ObjectHead(exists=False)
    # Server-side copy into the WORM documents bucket; its GOVERNANCE default retention
    # auto-applies on object creation. No bytes flow through the api.
    client.copy_object(
        Bucket=_doc_bucket(),
        Key=sha256,
        CopySource={"Bucket": _staging_bucket(), "Key": sha256},
    )
    return _head_sync(sha256, _doc_bucket())


async def finalize_worm(sha256: str) -> ObjectHead:
    """Promote a staged object to the WORM documents bucket (server-side copy) and return its
    head — existence + size + the now-applied retain-until. The blob is WORM-locked here, before
    the version row is committed."""
    return await asyncio.to_thread(_finalize_sync, sha256)


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


def _put_bytes_sync(data: bytes, object_key: str, bucket: str, content_type: str) -> None:
    _client().put_object(Bucket=bucket, Key=object_key, Body=data, ContentType=content_type)


async def put_bytes(
    data: bytes, object_key: str, *, bucket: str, content_type: str = "application/octet-stream"
) -> None:
    """Write bytes server-side (the **worker** path: the renderer caches a generated PDF rendition).
    Targets the **non-WORM** renditions bucket — renditions are derived + rebuildable (doc 14 §5.4),
    so this is a plain ``put_object`` (NOT the staging→``finalize_worm`` WORM cycle the source blob
    takes). Off the event loop."""
    await asyncio.to_thread(_put_bytes_sync, data, object_key, bucket, content_type)
