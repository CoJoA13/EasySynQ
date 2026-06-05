"""Content-addressed staging copy for the scan (slice S-ing-1, doc 09 §4.1).

The scan walker streams each **included** source file once into the non-WORM ``import-staging``
bucket,
content-addressed by its SHA-256 — so two identical files copy once (doc 09 §4.1) and an abandoned
import never touches the immutable vault (doc 09 §2/§15). The content address is unknown until the
bytes
are fully read, so the upload goes to a temp key while a hashing wrapper computes the digest in the
same
pass, then a server-side ``copy_object`` renames it to ``key = sha256`` (skipped if that object
already
exists — the dedup) and the temp key is removed. ``upload_fileobj`` runs with ``use_threads=False``
so
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

from ...config import get_settings


@dataclasses.dataclass(frozen=True, slots=True)
class StagedResult:
    sha256: str
    staged_blob_uri: str
    size_bytes: int


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


def _object_exists(client: Any, bucket: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError:
        return False
    return True


def _stage_sync(fileobj: BinaryIO) -> StagedResult:
    from boto3.s3.transfer import TransferConfig

    client = _client()
    bucket = _import_staging_bucket()
    reader = _HashingReader(fileobj)
    tmp_key = f"_tmp/{uuid.uuid4().hex}"
    # Sequential (use_threads=False) so the hashing wrapper sees bytes in order; multipart keeps
    # memory
    # bounded for large files.
    client.upload_fileobj(reader, bucket, tmp_key, Config=TransferConfig(use_threads=False))
    sha = reader.hexdigest()
    try:
        if not _object_exists(client, bucket, sha):  # dedup: identical bytes copy once (§4.1)
            client.copy_object(
                Bucket=bucket, Key=sha, CopySource={"Bucket": bucket, "Key": tmp_key}
            )
    finally:
        client.delete_object(Bucket=bucket, Key=tmp_key)
    return StagedResult(sha256=sha, staged_blob_uri=f"s3://{bucket}/{sha}", size_bytes=reader.size)


async def stage_stream(fileobj: BinaryIO) -> StagedResult:
    """Stream ``fileobj`` (positioned at 0) into ``import-staging`` content-addressed by its
    SHA-256,
    in one pass, off the event loop. Returns the digest + the canonical ``s3://…`` uri + the
    byte size."""
    return await asyncio.to_thread(_stage_sync, fileobj)


def _fetch_sync(sha256: str) -> bytes:
    body: bytes = _client().get_object(Bucket=_import_staging_bucket(), Key=sha256)["Body"].read()
    return body


async def fetch_staged_bytes(sha256: str) -> bytes:
    """Read a staged object's bytes by its content address (S-ing-2 extract). The worker reads the
    staged copy — NOT the source tree (a file may be moved/deleted between scan and extract; the
    staging bytes are immutable + content-addressed). Off the event loop; whole-object (a 0-byte /
    junk file never reaches here — only included candidates carry a ``staged_blob_uri``)."""
    return await asyncio.to_thread(_fetch_sync, sha256)
