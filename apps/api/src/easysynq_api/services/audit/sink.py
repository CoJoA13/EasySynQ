"""Off-host audit-checkpoint sink push (slice S6, R13, doc 12 §4.6, doc 18 §11 D-8).

Signed checkpoints are mirrored write-once to an off-host / append-only sink so a privileged
operator who controls both the live DB and the backups still cannot silently rewrite history. v1
implements the ``worm_bucket`` kind only — a separate MinIO object-lock bucket reached with
**distinct, write-only credentials** held apart from the vault root (genuine custody separation,
D-8). ``external_object_store`` / ``append_only_syslog`` are config-shape-only: their pushers raise
so an org cannot be left ``enabled`` with no real off-host mirror.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from ...config import get_settings


class SinkPushError(Exception):
    """A configured sink kind is not implemented, or its push failed."""


class SinkReadError(Exception):
    """The independent off-host read failed (kind unimplemented, or an access/transport error). The
    verifier must ALARM on this rather than silently treat the off-host anchor as attested."""


_HISTORY_PAGE_SIZE = 1000
_HISTORY_BODY_MAX_BYTES = 65_536


@dataclasses.dataclass(frozen=True, slots=True)
class CheckpointVersionRef:
    key: str
    version_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class CheckpointVersionsPage:
    versions: tuple[CheckpointVersionRef, ...]
    delete_markers: tuple[CheckpointVersionRef, ...]
    truncated: bool
    next_key_marker: str | None
    next_version_id_marker: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class ExplicitHistoryReader:
    endpoint: str
    bucket: str
    region: str
    access_key: str = dataclasses.field(repr=False)
    secret_key: str = dataclasses.field(repr=False)


def _audit_sink_client(connection: dict[str, Any] | None = None) -> Any:
    import boto3

    s = get_settings()
    # Honor the sink's documented non-secret connection: ``endpoint``/``region`` select the
    # genuinely separate host (falling back to the vault settings only when unset), while the
    # DISTINCT credentials come from the D-8 secret (empty → vault creds, dev-only convenience that
    # does NOT honour custody separation). Without honoring the endpoint an off_host sink would
    # silently write to local MinIO, defeating the independent-witness guarantee.
    conn = connection or {}
    return boto3.client(
        "s3",
        endpoint_url=conn.get("endpoint") or s.s3_endpoint,
        aws_access_key_id=s.audit_sink_access_key or s.s3_access_key,
        aws_secret_access_key=s.audit_sink_secret_key or s.s3_secret_key,
        region_name=conn.get("region") or s.s3_region,
    )


def _audit_sink_read_client(connection: dict[str, Any] | None = None) -> Any:
    import boto3

    s = get_settings()
    # SEPARATE read-only credentials (doc 12 §4.4): the independent off-host read-back must NOT use
    # the write-only sink creds (minio-init grants no GetObject) — a distinct read principal is the
    # custody-separated witness. Endpoint/region come from the sink's connection (same separate host
    # the writer targets), so a verifier does not silently read back from local MinIO.
    conn = connection or {}
    return boto3.client(
        "s3",
        endpoint_url=conn.get("endpoint") or s.s3_endpoint,
        aws_access_key_id=s.audit_sink_read_access_key or s.s3_access_key,
        aws_secret_access_key=s.audit_sink_read_secret_key or s.s3_secret_key,
        region_name=conn.get("region") or s.s3_region,
    )


def _audit_history_read_client(connection: dict[str, Any] | None = None) -> Any:
    import boto3
    from botocore.config import Config

    s = get_settings()
    conn = connection or {}
    return boto3.client(
        "s3",
        endpoint_url=conn.get("endpoint") or s.s3_endpoint,
        aws_access_key_id=s.audit_sink_read_access_key or s.s3_access_key,
        aws_secret_access_key=s.audit_sink_read_secret_key or s.s3_secret_key,
        region_name=conn.get("region") or s.s3_region,
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 1},
        ),
    )


def _explicit_history_read_client(reader: ExplicitHistoryReader) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=reader.endpoint,
        aws_access_key_id=reader.access_key,
        aws_secret_access_key=reader.secret_key,
        region_name=reader.region,
        verify=True,
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 1},
        ),
    )


def _history_client_and_bucket(
    connection: dict[str, Any] | None,
    reader: ExplicitHistoryReader | None,
) -> tuple[Any, str]:
    if reader is None:
        bucket = (connection or {}).get("bucket") or get_settings().s3_bucket_audit_checkpoints
        return _audit_history_read_client(connection), bucket
    if not reader.access_key or not reader.secret_key:
        raise SinkReadError("explicit history reader credentials are missing")
    if not reader.endpoint or not reader.bucket or not reader.region:
        raise SinkReadError("explicit history reader configuration is invalid")
    return _explicit_history_read_client(reader), reader.bucket


def _version_refs(raw: Any, *, field: str, prefix: str) -> tuple[CheckpointVersionRef, ...]:
    if not isinstance(raw, list):
        raise SinkReadError(f"off-host checkpoint version page has malformed {field}")
    refs: list[CheckpointVersionRef] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise SinkReadError(f"off-host checkpoint version page has malformed {field}")
        key = entry.get("Key")
        version_id = entry.get("VersionId")
        if not isinstance(key, str) or not key or not key.startswith(prefix):
            raise SinkReadError(f"off-host checkpoint version page has malformed {field}")
        if not isinstance(version_id, str) or not version_id:
            raise SinkReadError(f"off-host checkpoint version page has malformed {field}")
        refs.append(CheckpointVersionRef(key, version_id))
    return tuple(refs)


def _optional_marker(raw_page: dict[str, Any], field: str, *, terminal: bool) -> str | None:
    marker = raw_page.get(field)
    if marker is None:
        return None
    if not isinstance(marker, str):
        raise SinkReadError("off-host checkpoint version page has malformed continuation markers")
    if not marker:
        if terminal:
            return None
        raise SinkReadError("off-host checkpoint version page has malformed continuation markers")
    return marker


def _validated_versions_page(raw_page: dict[str, Any], prefix: str) -> CheckpointVersionsPage:
    if not isinstance(raw_page, dict):
        raise SinkReadError("off-host checkpoint version listing returned a malformed page")
    truncated = raw_page.get("IsTruncated")
    if type(truncated) is not bool:
        raise SinkReadError("off-host checkpoint version page has malformed truncation state")
    versions = _version_refs(raw_page.get("Versions", []), field="versions", prefix=prefix)
    delete_markers = _version_refs(
        raw_page.get("DeleteMarkers", []), field="delete markers", prefix=prefix
    )
    if len(versions) + len(delete_markers) > _HISTORY_PAGE_SIZE:
        raise SinkReadError("off-host checkpoint version page exceeds the requested page size")
    next_key_marker = _optional_marker(raw_page, "NextKeyMarker", terminal=not truncated)
    next_version_id_marker = _optional_marker(
        raw_page, "NextVersionIdMarker", terminal=not truncated
    )
    if next_version_id_marker is not None and next_key_marker is None:
        raise SinkReadError(
            "off-host checkpoint version page has inconsistent continuation markers"
        )
    if truncated and next_key_marker is None:
        raise SinkReadError("off-host checkpoint version page has no usable continuation marker")
    return CheckpointVersionsPage(
        versions,
        delete_markers,
        truncated,
        next_key_marker,
        next_version_id_marker,
    )


def list_offhost_checkpoint_versions_page(
    kind: str,
    connection: dict[str, Any] | None,
    org_id: Any,
    *,
    key_marker: str | None = None,
    version_id_marker: str | None = None,
    reader: ExplicitHistoryReader | None = None,
) -> CheckpointVersionsPage:
    """List one validated page of retained checkpoint versions using opaque provider cursors."""
    if kind != "worm_bucket":
        raise SinkReadError(f"off-host version listing is not implemented for sink kind '{kind}'")
    if key_marker is not None and (not isinstance(key_marker, str) or not key_marker):
        raise SinkReadError("off-host checkpoint version request has malformed key marker")
    if version_id_marker is not None and (
        not isinstance(version_id_marker, str) or not version_id_marker
    ):
        raise SinkReadError("off-host checkpoint version request has malformed version marker")
    if version_id_marker is not None and key_marker is None:
        raise SinkReadError("off-host checkpoint version request has inconsistent markers")
    prefix = f"checkpoints/{org_id}/"
    client, bucket = _history_client_and_bucket(connection, reader)
    params: dict[str, Any] = {
        "Bucket": bucket,
        "Prefix": prefix,
        "MaxKeys": _HISTORY_PAGE_SIZE,
    }
    if key_marker is not None:
        params["KeyMarker"] = key_marker
    if version_id_marker is not None:
        params["VersionIdMarker"] = version_id_marker
    try:
        try:
            raw_page = client.list_object_versions(**params)
        except Exception as exc:
            raise SinkReadError("off-host checkpoint version listing failed") from exc
        return _validated_versions_page(raw_page, prefix)
    finally:
        client.close()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def read_offhost_checkpoint_version(
    kind: str,
    connection: dict[str, Any] | None,
    ref: CheckpointVersionRef,
    *,
    reader: ExplicitHistoryReader | None = None,
) -> dict[str, Any]:
    """Read one explicit retained version with bounded bytes and owned resource cleanup."""
    if kind != "worm_bucket":
        raise SinkReadError(f"off-host version read is not implemented for sink kind '{kind}'")
    if not isinstance(ref.key, str) or not ref.key:
        raise SinkReadError("off-host checkpoint version reference has no key")
    if not isinstance(ref.version_id, str) or not ref.version_id:
        raise SinkReadError("off-host checkpoint version reference has no version ID")
    client, bucket = _history_client_and_bucket(connection, reader)
    try:
        try:
            response = client.get_object(
                Bucket=bucket,
                Key=ref.key,
                VersionId=ref.version_id,
            )
            body = response.get("Body")
            if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
                raise SinkReadError("off-host checkpoint version response has no readable body")
            try:
                returned_version = response.get("VersionId")
                if returned_version is not None and returned_version != ref.version_id:
                    raise SinkReadError("off-host checkpoint version response identity mismatch")
                content_length = response.get("ContentLength")
                if content_length is not None:
                    if type(content_length) is not int or content_length < 0:
                        raise SinkReadError(
                            "off-host checkpoint version has malformed content length"
                        )
                    if content_length > _HISTORY_BODY_MAX_BYTES:
                        raise SinkReadError(
                            "off-host checkpoint version exceeds the body-size limit"
                        )
                raw = body.read(_HISTORY_BODY_MAX_BYTES + 1)
            finally:
                body.close()
            if not isinstance(raw, bytes):
                raise SinkReadError("off-host checkpoint version body is not bytes")
            if len(raw) > _HISTORY_BODY_MAX_BYTES:
                raise SinkReadError("off-host checkpoint version exceeds the body-size limit")
            try:
                parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise SinkReadError("off-host checkpoint version is malformed JSON") from exc
            if not isinstance(parsed, dict):
                raise SinkReadError("off-host checkpoint version is not a JSON object")
            return parsed
        except SinkReadError:
            raise
        except Exception as exc:
            raise SinkReadError("off-host checkpoint version read failed") from exc
    finally:
        client.close()


def _push_worm_bucket(connection: dict[str, Any], key: str, body: bytes) -> None:
    bucket = connection.get("bucket") or get_settings().s3_bucket_audit_checkpoints
    client = _audit_sink_client(connection)
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
    client = _audit_sink_read_client(connection)
    prefix = f"checkpoints/{org_id}/"
    best_key: str | None = None
    best: tuple[int, str] | None = None
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            name = str(obj["Key"]).rsplit("/", 1)[-1]
            head = name.split("-", 1)[0]
            if not head.isdigit():
                continue
            # Order by (latest_id, name): equal latest_id ties break on the sortable ts suffix
            # (%Y%m%dT…Z) so the FRESHEST object wins, independent of S3 list order.
            cand = (int(head), name)
            if best is None or cand > best:
                best, best_key = cand, obj["Key"]
    if best_key is None:
        return None
    body = client.get_object(Bucket=bucket, Key=best_key)["Body"].read()
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        # A syntactically-valid but non-object body (``[]``/``null``/scalar) is a CORRUPT newest
        # WORM object — fail closed (the verifier's ``except`` marks read_failed → alarm) rather
        # than returning None, which the caller would treat as a benign fresh-empty witness.
        raise SinkReadError(f"off-host checkpoint object {best_key} is not a JSON object (corrupt)")
    return parsed
