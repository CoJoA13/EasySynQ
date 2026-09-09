from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from easysynq_api.services.audit import sink

_ORG = "11111111-1111-1111-1111-111111111111"
_PREFIX = f"checkpoints/{_ORG}/"


class _Body:
    def __init__(self, data: bytes, *, error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.closed = False
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if self.error is not None:
            raise self.error
        return self.data[:size]

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(
        self,
        *,
        page: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        list_error: Exception | None = None,
        get_error: Exception | None = None,
    ) -> None:
        self.page = page if page is not None else {"IsTruncated": False}
        self.response = response or {}
        self.list_error = list_error
        self.get_error = get_error
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.closed = False

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        if self.list_error is not None:
            raise self.list_error
        return self.page

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        if self.get_error is not None:
            raise self.get_error
        return self.response

    def list_objects_v2(self, **_kwargs: Any) -> None:
        raise AssertionError("history reader used current-object listing")

    def get_paginator(self, _operation: str) -> None:
        raise AssertionError("history reader used a paginator")

    def close(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    monkeypatch.setattr(sink, "_audit_history_read_client", lambda _connection: client)


def _client_error(operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "synthetic denial"}}, operation
    )


def test_history_client_uses_reader_identity_and_fixed_network_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def client(service: str, **kwargs: Any) -> object:
        captured["service"] = service
        captured.update(kwargs)
        return sentinel

    fake_boto = type("Boto", (), {"client": staticmethod(client)})
    monkeypatch.setitem(sys.modules, "boto3", fake_boto)
    monkeypatch.setattr(
        sink,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "s3_endpoint": "https://local.invalid",
                "s3_access_key": "writer-fallback",
                "s3_secret_key": "writer-secret",
                "s3_region": "local-region",
                "audit_sink_read_access_key": "reader-key",
                "audit_sink_read_secret_key": "reader-secret",
            },
        )(),
    )

    result = sink._audit_history_read_client(
        {"endpoint": "https://witness.invalid", "region": "witness-region"}
    )

    assert result is sentinel
    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == "https://witness.invalid"
    assert captured["aws_access_key_id"] == "reader-key"
    assert captured["aws_secret_access_key"] == "reader-secret"
    assert captured["region_name"] == "witness-region"
    config = captured["config"]
    assert config.connect_timeout == 3
    assert config.read_timeout == 5
    assert config.retries["total_max_attempts"] == 1


def test_explicit_history_reader_controls_client_and_bucket_without_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_clients: list[dict[str, Any]] = []
    list_client = _Client(page={"IsTruncated": False})
    body = _Body(b'{"checkpoint":{},"signature":"x"}')
    read_client = _Client(
        response={"Body": body, "ContentLength": len(body.data), "VersionId": "retained"}
    )
    clients = iter([list_client, read_client])

    def client(service: str, **kwargs: Any) -> _Client:
        captured_clients.append({"service": service, **kwargs})
        return next(clients)

    fake_boto = type("Boto", (), {"client": staticmethod(client)})
    monkeypatch.setitem(sys.modules, "boto3", fake_boto)
    monkeypatch.setattr(
        sink,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("explicit reader consulted Settings")),
    )
    reader = sink.ExplicitHistoryReader(
        endpoint="https://enrolled-witness.example.test",
        bucket="enrolled-history",
        region="enrolled-region",
        access_key="explicit-access",
        secret_key="explicit-secret",
    )
    conflicting = {
        "endpoint": "https://database-controlled.invalid",
        "bucket": "database-controlled",
        "region": "database-region",
    }

    page = sink.list_offhost_checkpoint_versions_page(
        "worm_bucket", conflicting, _ORG, reader=reader
    )
    ref = sink.CheckpointVersionRef(f"{_PREFIX}7-old.json", "retained")
    document = sink.read_offhost_checkpoint_version("worm_bucket", conflicting, ref, reader=reader)

    assert page.versions == ()
    assert document == {"checkpoint": {}, "signature": "x"}
    assert list_client.list_calls[0]["Bucket"] == "enrolled-history"
    assert read_client.get_calls == [
        {"Bucket": "enrolled-history", "Key": ref.key, "VersionId": "retained"}
    ]
    assert len(captured_clients) == 2
    for client_args in captured_clients:
        assert client_args["service"] == "s3"
        assert client_args["endpoint_url"] == "https://enrolled-witness.example.test"
        assert client_args["region_name"] == "enrolled-region"
        assert client_args["aws_access_key_id"] == "explicit-access"
        assert client_args["aws_secret_access_key"] == "explicit-secret"
        assert client_args["verify"] is True
    assert list_client.closed and read_client.closed and body.closed


@pytest.mark.parametrize("operation", ["list", "read"])
def test_explicit_history_reader_requires_direct_credentials_before_client_creation(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setattr(
        sink,
        "_explicit_history_read_client",
        lambda _reader: (_ for _ in ()).throw(AssertionError("client created")),
    )
    reader = sink.ExplicitHistoryReader(
        endpoint="https://enrolled-witness.example.test",
        bucket="enrolled-history",
        region="enrolled-region",
        access_key="",
        secret_key="",
    )

    with pytest.raises(sink.SinkReadError, match="credentials"):
        if operation == "list":
            sink.list_offhost_checkpoint_versions_page(
                "worm_bucket", {"bucket": "fallback"}, _ORG, reader=reader
            )
        else:
            sink.read_offhost_checkpoint_version(
                "worm_bucket",
                {"bucket": "fallback"},
                sink.CheckpointVersionRef(f"{_PREFIX}1-a.json", "v"),
                reader=reader,
            )


def test_list_page_forwards_exact_opaque_cursor_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opaque_key = f"{_PREFIX}old[minio_cache:v2,return:]"
    client = _Client(
        page={
            "IsTruncated": True,
            "Versions": [{"Key": f"{_PREFIX}7-old.json", "VersionId": "retained"}],
            "DeleteMarkers": [{"Key": f"{_PREFIX}junk", "VersionId": "marker"}],
            "NextKeyMarker": opaque_key,
            "NextVersionIdMarker": "null",
        }
    )
    _install(monkeypatch, client)

    page = sink.list_offhost_checkpoint_versions_page(
        "worm_bucket",
        {"bucket": "synthetic-history"},
        _ORG,
        key_marker="checkpoints/old[opaque]",
        version_id_marker="older-version",
    )

    assert client.list_calls == [
        {
            "Bucket": "synthetic-history",
            "Prefix": _PREFIX,
            "MaxKeys": 1000,
            "KeyMarker": "checkpoints/old[opaque]",
            "VersionIdMarker": "older-version",
        }
    ]
    assert page.next_key_marker == opaque_key
    assert page.next_version_id_marker == "null"
    assert page.versions == (sink.CheckpointVersionRef(f"{_PREFIX}7-old.json", "retained"),)
    assert page.delete_markers == (sink.CheckpointVersionRef(f"{_PREFIX}junk", "marker"),)
    assert client.closed


@pytest.mark.parametrize(
    "terminal_markers",
    [
        {"NextVersionIdMarker": ""},
        {"NextKeyMarker": "", "NextVersionIdMarker": ""},
    ],
)
def test_terminal_provider_empty_markers_are_normalized_only_after_completion(
    monkeypatch: pytest.MonkeyPatch, terminal_markers: dict[str, str]
) -> None:
    client = _Client(page={"IsTruncated": False, **terminal_markers})
    _install(monkeypatch, client)

    page = sink.list_offhost_checkpoint_versions_page(
        "worm_bucket", {"bucket": "synthetic-history"}, _ORG
    )

    assert page.truncated is False
    assert page.next_key_marker is None
    assert page.next_version_id_marker is None
    assert client.closed


@pytest.mark.parametrize(
    "page",
    [
        {},
        {"IsTruncated": "false"},
        {"IsTruncated": True},
        {"IsTruncated": True, "NextKeyMarker": ""},
        {"IsTruncated": True, "NextKeyMarker": 7},
        {
            "IsTruncated": True,
            "NextKeyMarker": "opaque",
            "NextVersionIdMarker": 7,
        },
        {
            "IsTruncated": True,
            "NextKeyMarker": "opaque",
            "NextVersionIdMarker": "",
        },
        {"IsTruncated": False, "NextVersionIdMarker": "orphan"},
        {"IsTruncated": False, "Versions": "not-a-list"},
        {"IsTruncated": False, "Versions": ["not-an-object"]},
        {"IsTruncated": False, "Versions": [{}]},
        {"IsTruncated": False, "Versions": [{"Key": f"{_PREFIX}1-a", "VersionId": ""}]},
        {
            "IsTruncated": False,
            "Versions": [{"Key": "checkpoints/another-org/1-a", "VersionId": "v"}],
        },
        {"IsTruncated": False, "DeleteMarkers": [{}]},
    ],
)
def test_list_page_rejects_malformed_provider_shapes_and_closes(
    monkeypatch: pytest.MonkeyPatch, page: dict[str, Any]
) -> None:
    client = _Client(page=page)
    _install(monkeypatch, client)
    with pytest.raises(sink.SinkReadError):
        sink.list_offhost_checkpoint_versions_page("worm_bucket", {"bucket": "b"}, _ORG)
    assert client.closed


def test_list_page_rejects_more_than_requested_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    versions = [{"Key": f"{_PREFIX}{index}-a", "VersionId": str(index)} for index in range(1001)]
    client = _Client(page={"IsTruncated": False, "Versions": versions})
    _install(monkeypatch, client)
    with pytest.raises(sink.SinkReadError, match="page size"):
        sink.list_offhost_checkpoint_versions_page("worm_bucket", {"bucket": "b"}, _ORG)
    assert client.closed


@pytest.mark.parametrize(
    "error",
    [_client_error("ListObjectVersions"), NotImplementedError("synthetic")],
)
def test_list_failure_has_no_current_object_fallback(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    client = _Client(list_error=error)
    _install(monkeypatch, client)
    with pytest.raises(sink.SinkReadError, match="version listing failed"):
        sink.list_offhost_checkpoint_versions_page("worm_bucket", {"bucket": "b"}, _ORG)
    assert client.closed


def test_list_rejects_version_marker_without_key_before_creating_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sink,
        "_audit_history_read_client",
        lambda _connection: (_ for _ in ()).throw(AssertionError("client created")),
    )
    with pytest.raises(sink.SinkReadError, match="inconsistent markers"):
        sink.list_offhost_checkpoint_versions_page(
            "worm_bucket", {"bucket": "b"}, _ORG, version_id_marker="null"
        )


def test_read_requests_explicit_version_with_finite_size_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _Body(b'{"checkpoint":{},"signature":"x"}')
    client = _Client(response={"Body": body, "ContentLength": len(body.data), "VersionId": "null"})
    _install(monkeypatch, client)
    ref = sink.CheckpointVersionRef(f"{_PREFIX}1-old.json", "null")

    result = sink.read_offhost_checkpoint_version(
        "worm_bucket", {"bucket": "synthetic-history"}, ref
    )

    assert result == {"checkpoint": {}, "signature": "x"}
    assert client.get_calls == [
        {"Bucket": "synthetic-history", "Key": ref.key, "VersionId": "null"}
    ]
    assert body.read_sizes == [65_537]
    assert body.closed and client.closed


@pytest.mark.parametrize(
    ("payload", "content_length", "version_id", "message"),
    [
        (b"\xff", None, "v", "malformed JSON"),
        (b"not-json", None, "v", "malformed JSON"),
        (b"[]", None, "v", "not a JSON object"),
        (b'{"a":1,"a":2}', None, "v", "malformed JSON"),
        (b"{}", -1, "v", "content length"),
        (b"{}", 65_537, "v", "body-size limit"),
        (b"{}", None, "different", "identity mismatch"),
    ],
)
def test_read_rejects_malformed_or_oversized_response_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    content_length: int | None,
    version_id: str,
    message: str,
) -> None:
    body = _Body(payload)
    response: dict[str, Any] = {"Body": body, "VersionId": version_id}
    if content_length is not None:
        response["ContentLength"] = content_length
    client = _Client(response=response)
    _install(monkeypatch, client)
    ref = sink.CheckpointVersionRef(f"{_PREFIX}1-old.json", "v")
    with pytest.raises(sink.SinkReadError, match=message):
        sink.read_offhost_checkpoint_version("worm_bucket", {"bucket": "b"}, ref)
    assert body.closed and client.closed


def test_read_accepts_exact_body_limit_and_rejects_one_more_byte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact = b'{"x":"' + b"a" * 65_528 + b'"}'
    assert len(exact) == 65_536
    exact_body = _Body(exact)
    exact_client = _Client(response={"Body": exact_body, "ContentLength": 65_536})
    _install(monkeypatch, exact_client)
    ref = sink.CheckpointVersionRef(f"{_PREFIX}1-old.json", "v")
    assert sink.read_offhost_checkpoint_version("worm_bucket", {"bucket": "b"}, ref)["x"]
    assert exact_body.closed and exact_client.closed

    oversized = b'{"x":"' + b"a" * 65_529 + b'"}'
    assert len(oversized) == 65_537
    oversized_body = _Body(oversized)
    oversized_client = _Client(response={"Body": oversized_body, "ContentLength": 0})
    _install(monkeypatch, oversized_client)
    with pytest.raises(sink.SinkReadError, match="body-size limit"):
        sink.read_offhost_checkpoint_version("worm_bucket", {"bucket": "b"}, ref)
    assert oversized_body.closed and oversized_client.closed


@pytest.mark.parametrize("content_length", [None, False])
def test_actual_body_overflow_is_rejected_with_absent_or_false_length(
    monkeypatch: pytest.MonkeyPatch, content_length: int | bool | None
) -> None:
    body = _Body(b"x" * 65_537)
    response: dict[str, Any] = {"Body": body}
    if content_length is not None:
        response["ContentLength"] = content_length
    client = _Client(response=response)
    _install(monkeypatch, client)
    ref = sink.CheckpointVersionRef(f"{_PREFIX}1-old.json", "v")

    expected = "content length" if content_length is False else "body-size limit"
    with pytest.raises(sink.SinkReadError, match=expected):
        sink.read_offhost_checkpoint_version("worm_bucket", {"bucket": "b"}, ref)
    assert body.closed and client.closed


@pytest.mark.parametrize("error", [_client_error("GetObject"), NotImplementedError("synthetic")])
def test_read_failure_has_no_unversioned_fallback(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    client = _Client(get_error=error)
    _install(monkeypatch, client)
    ref = sink.CheckpointVersionRef(f"{_PREFIX}1-old.json", "v")
    with pytest.raises(sink.SinkReadError, match="version read failed"):
        sink.read_offhost_checkpoint_version("worm_bucket", {"bucket": "b"}, ref)
    assert client.closed


def test_body_read_exception_still_closes_body_and_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _Body(b"{}", error=OSError("synthetic read failure"))
    client = _Client(response={"Body": body})
    _install(monkeypatch, client)
    with pytest.raises(sink.SinkReadError, match="version read failed"):
        sink.read_offhost_checkpoint_version(
            "worm_bucket",
            {"bucket": "b"},
            sink.CheckpointVersionRef(f"{_PREFIX}1-old.json", "v"),
        )
    assert body.closed and client.closed


@pytest.mark.parametrize("operation", ["list", "read"])
def test_unknown_kind_fails_before_client_creation(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    monkeypatch.setattr(
        sink,
        "_audit_history_read_client",
        lambda _connection: (_ for _ in ()).throw(AssertionError("client created")),
    )
    with pytest.raises(sink.SinkReadError, match="not implemented"):
        if operation == "list":
            sink.list_offhost_checkpoint_versions_page("external_object_store", {}, _ORG)
        else:
            sink.read_offhost_checkpoint_version(
                "external_object_store", {}, sink.CheckpointVersionRef(f"{_PREFIX}1-a", "v")
            )


def _policy_from_heredoc(source: str, filename: str) -> dict[str, Any]:
    match = re.search(
        rf"cat > /tmp/{re.escape(filename)} <<'EOF'\n(.*?)\nEOF",
        source,
        re.DOTALL,
    )
    assert match is not None
    parsed = json.loads(match.group(1))
    assert isinstance(parsed, dict)
    return parsed


def test_shipped_audit_sink_policies_keep_reader_narrow_and_writer_unchanged() -> None:
    root = Path(__file__).resolve().parents[4]
    source = (root / "infra/compose/minio/minio-init.sh").read_text()
    reader = _policy_from_heredoc(source, "audit-sink-readonly.json")
    writer = _policy_from_heredoc(source, "audit-sink-writeonly.json")
    reader_actions = reader["Statement"][0]["Action"]
    writer_actions = writer["Statement"][0]["Action"]
    assert reader_actions == [
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:ListBucketVersions",
    ]
    assert writer_actions == ["s3:PutObject", "s3:GetBucketLocation", "s3:ListBucket"]
    forbidden = ("PutObject", "Delete", "Retention", "Governance")
    assert not any(token in action for action in reader_actions for token in forbidden)
