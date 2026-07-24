"""Off-host audit-checkpoint sink push (slice S6, R13, doc 12 §4.6, doc 18 §11 D-8).

Signed checkpoints are mirrored write-once to an off-host / append-only sink so a privileged
operator who controls both the live DB and the backups still cannot silently rewrite history. v1
implements the ``worm_bucket`` kind only — a separate MinIO object-lock bucket reached with
**distinct, write-only credentials** held apart from the vault root (genuine custody separation,
D-8). ``external_object_store`` / ``append_only_syslog`` are config-shape-only: their pushers raise
so an org cannot be left ``enabled`` with no real off-host mirror.
"""

from __future__ import annotations

import json
from typing import Any

from ...config import get_settings


class SinkPushError(Exception):
    """A configured sink kind is not implemented, or its push failed."""


class SinkReadError(Exception):
    """The independent off-host read failed (kind unimplemented, or an access/transport error). The
    verifier must ALARM on this rather than silently treat the off-host anchor as attested."""


def _audit_sink_client() -> Any:
    import boto3

    s = get_settings()
    # DISTINCT credentials from the vault root (D-8). Empty creds fall back to the vault creds —
    # dev-only convenience that does NOT honour custody separation (off_host stays false).
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.audit_sink_access_key or s.s3_access_key,
        aws_secret_access_key=s.audit_sink_secret_key or s.s3_secret_key,
        region_name=s.s3_region,
    )


def _audit_sink_read_client() -> Any:
    import boto3

    s = get_settings()
    # SEPARATE read-only credentials (doc 12 §4.4): the independent off-host read-back must NOT use
    # the write-only sink creds (minio-init grants no GetObject) — a distinct read principal is
    # the custody-separated witness. Empty → the vault creds (dev only, NOT honest separation).
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.audit_sink_read_access_key or s.s3_access_key,
        aws_secret_access_key=s.audit_sink_read_secret_key or s.s3_secret_key,
        region_name=s.s3_region,
    )


def _push_worm_bucket(connection: dict[str, Any], key: str, body: bytes) -> None:
    bucket = connection.get("bucket") or get_settings().s3_bucket_audit_checkpoints
    client = _audit_sink_client()
    # Write-once: a new object per checkpoint as the chain advances; never overwrite.
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")


_PUSHERS: dict[str, Any] = {"worm_bucket": _push_worm_bucket}


def push_checkpoint(kind: str, connection: dict[str, Any] | None, key: str, body: bytes) -> None:
    """Push one signed checkpoint object to the sink. Raises :class:`SinkPushError` for an
    unimplemented kind (so config validation/Beat fails closed rather than silently dropping it)."""
    pusher = _PUSHERS.get(kind)
    if pusher is None:
        raise SinkPushError(f"checkpoint sink kind '{kind}' is not implemented in v1")
    pusher(connection or {}, key, body)


def fetch_latest_offhost_checkpoint(
    kind: str, connection: dict[str, Any] | None, org_id: Any
) -> dict[str, Any] | None:
    """Read the NEWEST signed checkpoint object BACK from the off-host sink (doc 12 §4.4), using the
    SEPARATE read credentials — a genuine independent witness, not the write path re-read. Returns
    the parsed ``{"checkpoint": {...}, "signature": "<b64>"}`` body, or ``None`` when the sink holds
    no checkpoint for the org. Raises :class:`SinkReadError` for an unimplemented kind — a
    verifier fails closed rather than silently treating a non-read-back sink as attested.

    The newest object is chosen by the ``latest_id`` in the write-once key name
    (``checkpoints/{org_id}/{latest_id}-{ts}.json``), then its body is read."""
    if kind != "worm_bucket":
        raise SinkReadError(f"off-host read-back is not implemented for sink kind '{kind}'")
    bucket = (connection or {}).get("bucket") or get_settings().s3_bucket_audit_checkpoints
    client = _audit_sink_read_client()
    prefix = f"checkpoints/{org_id}/"
    best_key: str | None = None
    best_id = -1
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            name = str(obj["Key"]).rsplit("/", 1)[-1]
            head = name.split("-", 1)[0]
            if head.isdigit() and int(head) > best_id:
                best_id, best_key = int(head), obj["Key"]
    if best_key is None:
        return None
    body = client.get_object(Bucket=bucket, Key=best_key)["Body"].read()
    parsed = json.loads(body)
    return parsed if isinstance(parsed, dict) else None
