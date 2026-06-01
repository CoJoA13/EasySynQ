"""Off-host audit-checkpoint sink push (slice S6, R13, doc 12 §4.6, doc 18 §11 D-8).

Signed checkpoints are mirrored write-once to an off-host / append-only sink so a privileged
operator who controls both the live DB and the backups still cannot silently rewrite history. v1
implements the ``worm_bucket`` kind only — a separate MinIO object-lock bucket reached with
**distinct, write-only credentials** held apart from the vault root (genuine custody separation,
D-8). ``external_object_store`` / ``append_only_syslog`` are config-shape-only: their pushers raise
so an org cannot be left ``enabled`` with no real off-host mirror.
"""

from __future__ import annotations

from typing import Any

from ...config import get_settings


class SinkPushError(Exception):
    """A configured sink kind is not implemented, or its push failed."""


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
