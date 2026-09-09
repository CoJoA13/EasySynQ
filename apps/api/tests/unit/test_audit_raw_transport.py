from __future__ import annotations

import base64
import hashlib
import io
import sys
import threading
from collections.abc import Callable, Iterable
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    FlexibleChecksumError,
    HTTPClientError,
    IncompleteReadError,
    ProxyConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
    SSLError,
)
from botocore.httpchecksum import Sha256Checksum, StreamingChecksumBody
from botocore.response import StreamingBody

from easysynq_api.services.audit import raw_transport
from easysynq_api.services.audit.sink import CheckpointVersionRef, ExplicitHistoryReader

pytestmark = pytest.mark.unit

_DEFAULT = object()


class StatefulBody:
    def __init__(
        self,
        chunks: Iterable[Any],
        *,
        strict_sizes: bool = True,
        read_error: BaseException | None = None,
        close_error: BaseException | None = None,
        on_read: Callable[[int], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._chunks = iter(chunks)
        self.strict_sizes = strict_sizes
        self.read_error = read_error
        self.close_error = close_error
        self.on_read = on_read
        self.on_close = on_close
        self.read_sizes: list[int] = []
        self.events: list[str] = []
        self.close_calls = 0

    def read(self, size: int) -> Any:
        if type(size) is not int or not 1 <= size <= 8_192:
            raise AssertionError(f"expected a positive bounded read, got {size!r}")
        self.read_sizes.append(size)
        self.events.append(f"body.read:{size}")
        if self.on_read is not None:
            self.on_read(len(self.read_sizes))
        if self.read_error is not None:
            raise self.read_error
        chunk = next(self._chunks, b"")
        if self.strict_sizes and isinstance(chunk, bytes) and len(chunk) > size:
            raise AssertionError("configured chunk exceeds the requested read size")
        return chunk

    def close(self) -> None:
        self.close_calls += 1
        self.events.append("body.close")
        if self.on_close is not None:
            self.on_close()
        if self.close_error is not None:
            raise self.close_error


class RecordingClient:
    def __init__(
        self,
        body: Any = _DEFAULT,
        *,
        length: Any = _DEFAULT,
        version_id: Any = _DEFAULT,
        response: Any = _DEFAULT,
        get_error: BaseException | None = None,
        close_error: BaseException | None = None,
        on_get: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.body = body
        self.get_error = get_error
        self.close_error = close_error
        self.on_get = on_get
        self.on_close = on_close
        self.requests: list[dict[str, Any]] = []
        self.close_calls = 0
        if response is _DEFAULT:
            body_value = StatefulBody([b""]) if body is _DEFAULT else body
            length_value = 0 if length is _DEFAULT else length
            version_value = "older+version/1" if version_id is _DEFAULT else version_id
            self.response: Any = {
                "Body": body_value,
                "ContentLength": length_value,
                "VersionId": version_value,
                "ResponseMetadata": {"HTTPStatusCode": 200},
            }
        else:
            self.response = response
        response_body = self.response.get("Body") if type(self.response) is dict else None
        self.events = response_body.events if isinstance(response_body, StatefulBody) else []

    def get_object(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        self.events.append("client.get_object")
        if self.on_get is not None:
            self.on_get()
        if self.get_error is not None:
            raise self.get_error
        return self.response

    def close(self) -> None:
        self.close_calls += 1
        self.events.append("client.close")
        if self.on_close is not None:
            self.on_close()
        if self.close_error is not None:
            raise self.close_error


class CloseOnlyBody:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _reader(**changes: Any) -> ExplicitHistoryReader:
    values = {
        "endpoint": "http://127.0.0.1:9000",
        "bucket": "synthetic-audit",
        "region": "us-east-1",
        "access_key": "synthetic-reader",
        "secret_key": "synthetic-secret",
    }
    values.update(changes)
    return ExplicitHistoryReader(**values)


def _ref(**changes: Any) -> CheckpointVersionRef:
    values = {
        "key": "checkpoints/synthetic-object.json",
        "version_id": "older+version/1",
    }
    values.update(changes)
    return CheckpointVersionRef(**values)


def _install(monkeypatch: pytest.MonkeyPatch, client: RecordingClient) -> None:
    monkeypatch.setattr(raw_transport, "_create_client", lambda _reader, _ref: client)


def _capture(call: Callable[[], Any]) -> BaseException:
    try:
        call()
    except BaseException as error:  # noqa: BLE001 - tests assert fatal fault identity
        return error
    raise AssertionError("call returned instead of raising")


def _assert_controlled(error: BaseException, code: str) -> None:
    assert type(error) is raw_transport.RawVersionReadError
    assert error.code == code
    assert str(error) == f"raw checkpoint version read failed: {code}"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_stateful_body_advances_across_short_reads_without_reset() -> None:
    body = StatefulBody([b"a", b"bc", b""])
    assert body.read(3) == b"a"
    assert body.read(2) == b"bc"
    assert body.read(1) == b""
    assert body.read_sizes == [3, 2, 1]
    body.close()
    assert body.events == ["body.read:3", "body.read:2", "body.read:1", "body.close"]


def test_short_reads_return_the_complete_exact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _reader()
    ref = _ref()
    body = StatefulBody([b"a", b"bc", b""])
    client = RecordingClient(body, length=3, version_id=ref.version_id)
    _install(monkeypatch, client)
    result = raw_transport.read_raw_checkpoint_version(reader, ref)
    assert (result.key, result.version_id, result.body) == (ref.key, ref.version_id, b"abc")
    assert client.requests == [
        {"Bucket": reader.bucket, "Key": ref.key, "VersionId": ref.version_id}
    ]
    assert body.read_sizes == [3, 2, 1]
    assert client.events[-2:] == ["body.close", "client.close"]
    assert body.close_calls == client.close_calls == 1


def test_get_object_always_requests_exact_version_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _reader()
    ref = _ref(version_id="opaque+version/one")
    client = RecordingClient(StatefulBody([b""]), length=0, version_id=ref.version_id)
    _install(monkeypatch, client)
    raw_transport.read_raw_checkpoint_version(reader, ref)
    assert client.requests == [
        {"Bucket": reader.bucket, "Key": ref.key, "VersionId": ref.version_id}
    ]


def test_missing_returned_version_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    body = StatefulBody([b""])
    response = {
        "Body": body,
        "ContentLength": 0,
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }
    client = RecordingClient(response=response)
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, "VERSION_MISMATCH")


def test_noncanonical_bytes_are_never_reserialized(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b'{\n  "z":1, "a": "\\u0062"\n}\n'
    body = StatefulBody([payload[:3], payload[3:], b""])
    client = RecordingClient(body, length=len(payload), version_id=_ref().version_id)
    _install(monkeypatch, client)
    result = raw_transport.read_raw_checkpoint_version(_reader(), _ref())
    assert result.body == payload


def test_body_is_closed_before_client(monkeypatch: pytest.MonkeyPatch) -> None:
    body = StatefulBody([b""])
    client = RecordingClient(body, length=0, version_id=_ref().version_id)
    _install(monkeypatch, client)
    raw_transport.read_raw_checkpoint_version(_reader(), _ref())
    assert client.events[-2:] == ["body.close", "client.close"]
    assert body.close_calls == client.close_calls == 1


@pytest.mark.parametrize(
    ("payload", "chunks", "version_id"),
    [
        (b"", [b""], "null"),
        (
            b'{ \n  "z": 1, "a": "\\u0062"\n}\n',
            [b"{ \n", b'  "z": 1, ', b'"a": "\\u0062"\n}\n', b""],
            "v+1/old",
        ),
        (b"x" * 65_536, [b"x" * 8_192] * 8 + [b""], "maximum"),
    ],
)
def test_returns_exact_empty_noncanonical_and_maximum_bodies(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    chunks: list[bytes],
    version_id: str,
) -> None:
    ref = _ref(version_id=version_id)
    body = StatefulBody(chunks)
    client = RecordingClient(body, length=len(payload), version_id=version_id)
    _install(monkeypatch, client)
    result = raw_transport.read_raw_checkpoint_version(_reader(), ref)
    assert result.body == payload
    assert sum(body.read_sizes[:-1]) >= len(payload)
    assert body.read_sizes[-1] == 1
    assert max(body.read_sizes) <= 8_192


def test_result_and_reader_repr_hide_raw_body_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _reader(access_key="sentinel-access", secret_key="sentinel-secret")
    body = StatefulBody([b"sentinel-body", b""])
    _install(monkeypatch, RecordingClient(body, length=13, version_id=_ref().version_id))
    result = raw_transport.read_raw_checkpoint_version(reader, _ref())
    assert "sentinel-body" not in repr(result)
    assert "sentinel-access" not in repr(reader)
    assert "sentinel-secret" not in repr(reader)


@pytest.mark.parametrize(
    ("reader", "ref", "cancel"),
    [
        (object(), _ref(), None),
        (_reader(), object(), None),
        (_reader(endpoint="HTTP://127.0.0.1:9000"), _ref(), None),
        (_reader(endpoint="https://example.test/"), _ref(), None),
        (_reader(endpoint="http://example.test"), _ref(), None),
        (_reader(bucket="Bad_Bucket"), _ref(), None),
        (_reader(region=""), _ref(), None),
        (_reader(access_key=" reader"), _ref(), None),
        (_reader(secret_key=""), _ref(), None),
        (_reader(), _ref(key=""), None),
        (_reader(), _ref(key="a\x00b"), None),
        (_reader(), _ref(key="a" * 1_025), None),
        (_reader(), _ref(key="\ud800"), None),
        (_reader(), _ref(version_id=""), None),
        (_reader(), _ref(version_id="é" * 513), None),
        (_reader(), _ref(), object()),
    ],
)
def test_invalid_inputs_fail_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    reader: Any,
    ref: Any,
    cancel: Any,
) -> None:
    monkeypatch.setattr(
        raw_transport,
        "_create_client",
        lambda _reader, _ref: (_ for _ in ()).throw(AssertionError("client created")),
    )
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(reader, ref, cancel=cancel))
    assert type(error) is raw_transport.RawVersionInputError
    assert str(error) == "invalid raw checkpoint version input"
    assert error.__cause__ is None


@pytest.mark.parametrize("credential", ["access_key", "secret_key"])
def test_oversized_credentials_fail_before_delegated_validation_or_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    credential: str,
) -> None:
    reader = _reader(**{credential: f" {'x' * 4_095} "})
    events: list[str] = []
    original_endpoint = raw_transport._endpoint
    original_required_value = raw_transport._required_environment_value
    client = RecordingClient(StatefulBody([b""]), length=0, version_id=_ref().version_id)

    def endpoint(value: str) -> str:
        events.append("endpoint")
        return original_endpoint(value)

    def required_value(values: dict[str, Any], name: str, *, maximum: int) -> str:
        events.append(f"credential:{name}")
        return original_required_value(values, name, maximum=maximum)

    def create_client(
        _reader_value: ExplicitHistoryReader,
        _ref_value: CheckpointVersionRef,
    ) -> RecordingClient:
        events.append("client")
        return client

    monkeypatch.setattr(raw_transport, "_endpoint", endpoint)
    monkeypatch.setattr(raw_transport, "_required_environment_value", required_value)
    monkeypatch.setattr(raw_transport, "_create_client", create_client)

    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(reader, _ref()))
    assert type(error) is raw_transport.RawVersionInputError
    assert str(error) == "invalid raw checkpoint version input"
    assert error.__cause__ is None
    assert events == []
    assert client.requests == []


def test_exact_maximum_credentials_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _reader(access_key="a" * 4_096, secret_key="s" * 4_096)
    ref = _ref()
    body = StatefulBody([b""])
    client = RecordingClient(body, length=0, version_id=ref.version_id)
    _install(monkeypatch, client)

    result = raw_transport.read_raw_checkpoint_version(reader, ref)

    assert (result.key, result.version_id, result.body) == (ref.key, ref.version_id, b"")
    assert client.requests == [
        {"Bucket": reader.bucket, "Key": ref.key, "VersionId": ref.version_id}
    ]
    assert client.events[-2:] == ["body.close", "client.close"]


def test_maximum_utf8_labels_are_forwarded_without_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _reader()
    ref = _ref(key="k" * 1_024, version_id="é" * 512)
    body = StatefulBody([b""])
    client = RecordingClient(body, length=0, version_id=ref.version_id)
    _install(monkeypatch, client)
    result = raw_transport.read_raw_checkpoint_version(reader, ref)
    assert (result.key, result.version_id) == (ref.key, ref.version_id)
    assert client.requests == [
        {"Bucket": reader.bucket, "Key": ref.key, "VersionId": ref.version_id}
    ]


@pytest.mark.parametrize(
    ("response", "code", "body_is_owned"),
    [
        (None, "RESPONSE_INVALID", False),
        ({}, "RESPONSE_INVALID", False),
        ({"Body": object()}, "RESPONSE_INVALID", False),
        ({"Body": CloseOnlyBody()}, "RESPONSE_INVALID", True),
        (
            {"Body": StatefulBody([b""]), "ContentLength": 0, "VersionId": "older+version/1"},
            "RESPONSE_INVALID",
            True,
        ),
        (
            {
                "Body": StatefulBody([b""]),
                "ContentLength": 0,
                "VersionId": "older+version/1",
                "ResponseMetadata": {"HTTPStatusCode": False},
            },
            "RESPONSE_INVALID",
            True,
        ),
        (
            {
                "Body": StatefulBody([b""]),
                "ContentLength": 0,
                "VersionId": "older+version/1",
                "ResponseMetadata": {"HTTPStatusCode": 206},
            },
            "RESPONSE_INVALID",
            True,
        ),
        (
            {
                "Body": StatefulBody([b""]),
                "ContentLength": 0,
                "VersionId": "older+version/1",
                "DeleteMarker": True,
                "ResponseMetadata": {"HTTPStatusCode": 200},
            },
            "DELETE_MARKER",
            True,
        ),
        (
            {
                "Body": StatefulBody([b""]),
                "ContentLength": 0,
                "VersionId": "older+version/1",
                "DeleteMarker": None,
                "ResponseMetadata": {"HTTPStatusCode": 200},
            },
            "RESPONSE_INVALID",
            True,
        ),
    ],
)
def test_rejects_invalid_response_shape_and_owns_only_closable_body(
    monkeypatch: pytest.MonkeyPatch,
    response: Any,
    code: str,
    body_is_owned: bool,
) -> None:
    client = RecordingClient(response=response)
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, code)
    body = response.get("Body") if type(response) is dict else None
    if body_is_owned:
        assert body.close_calls == 1
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"ResponseMetadata": {"HTTPStatusCode": 500}, "DeleteMarker": True}, "RESPONSE_INVALID"),
        ({"DeleteMarker": True, "VersionId": None}, "DELETE_MARKER"),
        ({"VersionId": None, "ContentLength": 65_537}, "VERSION_MISMATCH"),
        ({"VersionId": "different", "ContentLength": -1}, "VERSION_MISMATCH"),
        ({"VersionId": ""}, "VERSION_MISMATCH"),
        ({"ContentLength": None}, "RESPONSE_INVALID"),
        ({"ContentLength": False}, "RESPONSE_INVALID"),
        ({"ContentLength": -1}, "RESPONSE_INVALID"),
        ({"ContentLength": 65_537}, "BODY_LIMIT"),
    ],
)
def test_metadata_first_fault_precedence_and_no_payload_read(
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
    code: str,
) -> None:
    body = StatefulBody([b""])
    response = {
        "Body": body,
        "ContentLength": 0,
        "VersionId": _ref().version_id,
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }
    response.update(changes)
    client = RecordingClient(response=response)
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, code)
    assert body.read_sizes == []
    assert client.events[-2:] == ["body.close", "client.close"]


@pytest.mark.parametrize(
    ("chunks", "length", "strict_sizes", "code", "expected_sizes"),
    [
        ([b"a", b""], 2, True, "LENGTH_MISMATCH", [2, 1]),
        ([b"ab", b"x"], 2, True, "LENGTH_MISMATCH", [2, 1]),
        ([b"x"], 0, True, "LENGTH_MISMATCH", [1]),
        ([b"x" * 8_192] * 8 + [b"x"], 65_536, True, "BODY_LIMIT", [8_192] * 8 + [1]),
        ([b"abc"], 2, False, "RESPONSE_INVALID", [2]),
        ([bytearray(b"a")], 1, False, "RESPONSE_INVALID", [1]),
    ],
)
def test_rejects_early_eof_visible_overrun_oversized_and_nonbytes_chunks(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[Any],
    length: int,
    strict_sizes: bool,
    code: str,
    expected_sizes: list[int],
) -> None:
    body = StatefulBody(chunks, strict_sizes=strict_sizes)
    client = RecordingClient(body, length=length, version_id=_ref().version_id)
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, code)
    assert body.read_sizes == expected_sizes
    assert body.close_calls == client.close_calls == 1


def test_one_byte_progress_stays_within_absolute_read_and_retained_byte_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = StatefulBody([b"x"] * 65_536 + [b""])
    client = RecordingClient(body, length=65_536, version_id=_ref().version_id)
    _install(monkeypatch, client)
    result = raw_transport.read_raw_checkpoint_version(_reader(), _ref())
    assert result.body == b"x" * 65_536
    assert len(body.read_sizes) == 65_537
    assert all(1 <= size <= 8_192 for size in body.read_sizes)
    assert body.read_sizes[-1] == 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            ClientError(
                {"Error": {"Code": "Sentinel", "Message": "secret provider detail"}},
                "GetObject",
            ),
            "PROVIDER_FAILURE",
        ),
        (EndpointConnectionError(endpoint_url="https://secret.invalid"), "TRANSPORT_FAILURE"),
        (SSLError(endpoint_url="https://secret.invalid", error="secret"), "TRANSPORT_FAILURE"),
        (ConnectTimeoutError(endpoint_url="https://secret.invalid"), "TRANSPORT_FAILURE"),
        (ReadTimeoutError(endpoint_url="https://secret.invalid"), "TRANSPORT_FAILURE"),
        (ProxyConnectionError(proxy_url="https://secret.invalid"), "TRANSPORT_FAILURE"),
        (
            ConnectionClosedError(endpoint_url="https://secret.invalid", request=None),
            "TRANSPORT_FAILURE",
        ),
        (ResponseStreamingError(error="secret framing detail"), "TRANSPORT_FAILURE"),
        (IncompleteReadError(actual_bytes=1, expected_bytes=2), "LENGTH_MISMATCH"),
        (FlexibleChecksumError(error_msg="secret checksum detail"), "RESPONSE_INVALID"),
    ],
)
def test_expected_sdk_failures_have_fixed_secret_free_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    code: str,
) -> None:
    client = RecordingClient(get_error=error)
    _install(monkeypatch, client)
    caught = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(caught, code)
    assert "secret" not in str(caught)
    assert client.close_calls == 1


@pytest.mark.parametrize(
    "error",
    [
        HTTPClientError(error=MemoryError("wrapped")),
        MemoryError("memory"),
        RuntimeError("runtime"),
        TypeError("type"),
        KeyboardInterrupt(),
        SystemExit(9),
    ],
)
def test_unexpected_operation_fault_preserves_exact_identity(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    body = StatefulBody([], read_error=error)
    client = RecordingClient(body, length=1, version_id=_ref().version_id)
    _install(monkeypatch, client)
    caught = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    assert caught is error
    assert body.close_calls == client.close_calls == 1


def test_real_streaming_body_short_reads_and_direct_incomplete_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = StreamingBody(io.BytesIO(b"abc"), 3)
    client = RecordingClient(stream, length=3, version_id=_ref().version_id)
    _install(monkeypatch, client)
    assert raw_transport.read_raw_checkpoint_version(_reader(), _ref()).body == b"abc"

    truncated = StreamingBody(io.BytesIO(b"a"), 2)
    failed = RecordingClient(truncated, length=2, version_id=_ref().version_id)
    _install(monkeypatch, failed)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, "LENGTH_MISMATCH")


def test_real_streaming_checksum_body_validates_at_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"checksum-preserved-bytes"
    expected = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")
    valid = StreamingChecksumBody(io.BytesIO(payload), len(payload), Sha256Checksum(), expected)
    _install(monkeypatch, RecordingClient(valid, length=len(payload), version_id=_ref().version_id))
    assert raw_transport.read_raw_checkpoint_version(_reader(), _ref()).body == payload

    invalid = StreamingChecksumBody(
        io.BytesIO(payload),
        len(payload),
        Sha256Checksum(),
        base64.b64encode(b"x" * 32).decode("ascii"),
    )
    _install(
        monkeypatch,
        RecordingClient(invalid, length=len(payload), version_id=_ref().version_id),
    )
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, "RESPONSE_INVALID")


def test_known_cleanup_failure_is_controlled_and_both_closes_are_attempted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = StatefulBody(
        [b""], close_error=EndpointConnectionError(endpoint_url="https://secret.invalid")
    )
    client = RecordingClient(body, length=0, version_id=_ref().version_id)
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, "CLEANUP_FAILED")
    assert client.events[-2:] == ["body.close", "client.close"]


def test_client_only_known_cleanup_failure_is_controlled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = StatefulBody([b""])
    client = RecordingClient(
        body,
        length=0,
        version_id=_ref().version_id,
        close_error=ReadTimeoutError(endpoint_url="https://secret.invalid"),
    )
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, "CLEANUP_FAILED")
    assert client.events[-2:] == ["body.close", "client.close"]


def test_two_cleanup_only_faults_are_grouped_in_close_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_fault = RuntimeError("body close")
    client_fault = EndpointConnectionError(endpoint_url="https://secret.invalid")
    body = StatefulBody([b""], close_error=body_fault)
    client = RecordingClient(
        body,
        length=0,
        version_id=_ref().version_id,
        close_error=client_fault,
    )
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    assert isinstance(error, BaseExceptionGroup)
    assert error.exceptions[0] is body_fault
    _assert_controlled(error.exceptions[1], "CLEANUP_FAILED")
    assert client.events[-2:] == ["body.close", "client.close"]


def test_operation_and_two_cleanup_faults_preserve_order_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_fault = RuntimeError("client close")
    body = StatefulBody(
        [b""], close_error=EndpointConnectionError(endpoint_url="https://secret.invalid")
    )
    client = RecordingClient(
        body,
        length=1,
        version_id=_ref().version_id,
        close_error=client_fault,
    )
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    assert isinstance(error, BaseExceptionGroup)
    assert error.message == "raw checkpoint version operation and cleanup failed"
    assert len(error.exceptions) == 3
    _assert_controlled(error.exceptions[0], "LENGTH_MISMATCH")
    _assert_controlled(error.exceptions[1], "CLEANUP_FAILED")
    assert error.exceptions[2] is client_fault
    assert client.events[-2:] == ["body.close", "client.close"]


def test_precancelled_call_never_creates_client(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(
        raw_transport,
        "_create_client",
        lambda _reader, _ref: (_ for _ in ()).throw(AssertionError("client created")),
    )
    error = _capture(
        lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref(), cancel=cancel)
    )
    assert type(error) is raw_transport.RawVersionReadCancelled
    assert str(error) == "raw checkpoint version read cancelled"


def test_cancellation_releases_and_joins_owner_before_cleanup_assertions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = threading.Event()
    entered = threading.Event()
    release = threading.Event()

    def block(_call: int) -> None:
        entered.set()
        assert release.wait(2)

    body = StatefulBody([b"a"], on_read=block)
    client = RecordingClient(body, length=1, version_id=_ref().version_id)
    _install(monkeypatch, client)
    outcomes: list[BaseException] = []

    def owner() -> None:
        outcomes.append(
            _capture(
                lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref(), cancel=cancel)
            )
        )

    thread = threading.Thread(target=owner)
    thread.start()
    assert entered.wait(2)
    cancel.set()
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert len(outcomes) == 1 and type(outcomes[0]) is raw_transport.RawVersionReadCancelled
    assert body.read_sizes == [1]
    assert body.close_calls == client.close_calls == 1


def test_cancellation_during_cleanup_precedes_cleanup_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = threading.Event()
    body = StatefulBody(
        [b""],
        on_close=cancel.set,
        close_error=EndpointConnectionError(endpoint_url="https://secret.invalid"),
    )
    client = RecordingClient(body, length=0, version_id=_ref().version_id)
    _install(monkeypatch, client)
    error = _capture(
        lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref(), cancel=cancel)
    )
    assert isinstance(error, BaseExceptionGroup)
    assert type(error.exceptions[0]) is raw_transport.RawVersionReadCancelled
    _assert_controlled(error.exceptions[1], "CLEANUP_FAILED")
    assert client.close_calls == 1


def test_cancellation_observed_after_client_close_prevents_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = threading.Event()
    body = StatefulBody([b""])
    client = RecordingClient(body, length=0, version_id=_ref().version_id, on_close=cancel.set)
    _install(monkeypatch, client)
    error = _capture(
        lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref(), cancel=cancel)
    )
    assert type(error) is raw_transport.RawVersionReadCancelled
    assert body.close_calls == 1
    assert client.events[-2:] == ["body.close", "client.close"]


def test_unexpected_fault_and_cleanup_cancellation_are_both_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = threading.Event()
    primary = RuntimeError("operation")
    body = StatefulBody([], read_error=primary, on_close=cancel.set)
    client = RecordingClient(body, length=1, version_id=_ref().version_id)
    _install(monkeypatch, client)
    error = _capture(
        lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref(), cancel=cancel)
    )
    assert isinstance(error, BaseExceptionGroup)
    assert error.exceptions[0] is primary
    assert type(error.exceptions[1]) is raw_transport.RawVersionReadCancelled


@pytest.mark.parametrize("stage", ["factory", "get", "read", "cleanup"])
def test_deadline_is_checked_at_every_io_stage(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    now = [0.0]
    monkeypatch.setattr(raw_transport, "_monotonic", lambda: now[0])

    def expire() -> None:
        now[0] = 16.0

    body = StatefulBody(
        [b"a", b""],
        on_read=(lambda _call: expire()) if stage == "read" else None,
        on_close=expire if stage == "cleanup" else None,
    )
    client = RecordingClient(
        body,
        length=1,
        version_id=_ref().version_id,
        on_get=expire if stage == "get" else None,
    )

    def create(_reader: ExplicitHistoryReader, _ref: CheckpointVersionRef) -> RecordingClient:
        if stage == "factory":
            expire()
        return client

    monkeypatch.setattr(raw_transport, "_create_client", create)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, "DEADLINE_EXCEEDED")
    assert client.close_calls == 1


def test_diagnosed_failure_wins_over_deadline_during_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    monkeypatch.setattr(raw_transport, "_monotonic", lambda: now[0])
    body = StatefulBody([b""], on_close=lambda: now.__setitem__(0, 16.0))
    client = RecordingClient(body, length=1, version_id=_ref().version_id)
    _install(monkeypatch, client)
    error = _capture(lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref()))
    _assert_controlled(error, "LENGTH_MISMATCH")


def test_cancellation_wins_when_deadline_is_observed_at_same_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    cancel = threading.Event()
    monkeypatch.setattr(raw_transport, "_monotonic", lambda: now[0])

    def stop(_call: int) -> None:
        now[0] = 16.0
        cancel.set()

    body = StatefulBody([b"a"], on_read=stop)
    client = RecordingClient(body, length=1, version_id=_ref().version_id)
    _install(monkeypatch, client)
    error = _capture(
        lambda: raw_transport.read_raw_checkpoint_version(_reader(), _ref(), cancel=cancel)
    )
    assert type(error) is raw_transport.RawVersionReadCancelled


class _Events:
    def __init__(self, *, registration_error: BaseException | None = None) -> None:
        self.registration_error = registration_error
        self.registrations: list[tuple[str, Callable[..., None], str]] = []

    def register(self, name: str, handler: Callable[..., None], *, unique_id: str) -> None:
        if self.registration_error is not None:
            raise self.registration_error
        self.registrations.append((name, handler, unique_id))


class _FactoryClient:
    def __init__(
        self,
        *,
        registration_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.meta = SimpleNamespace(events=_Events(registration_error=registration_error))
        self.close_error = close_error
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def _fake_boto3(
    monkeypatch: pytest.MonkeyPatch,
    client: _FactoryClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    session_arguments: dict[str, Any] = {}
    client_arguments: dict[str, Any] = {}

    class Session:
        def __init__(self, **kwargs: Any) -> None:
            session_arguments.update(kwargs)

        def client(self, service: str, **kwargs: Any) -> _FactoryClient:
            client_arguments.update({"service": service, **kwargs})
            return client

    fake_boto3 = SimpleNamespace(session=SimpleNamespace(Session=Session))
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    return session_arguments, client_arguments


def test_factory_pins_private_session_network_policy_and_service_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FactoryClient()
    session_args, client_args = _fake_boto3(monkeypatch, client)
    reader = _reader()
    ref = _ref()

    assert raw_transport._create_client(reader, ref) is client

    assert session_args == {
        "aws_access_key_id": reader.access_key,
        "aws_secret_access_key": reader.secret_key,
        "aws_session_token": None,
        "region_name": reader.region,
    }
    assert client_args["service"] == "s3"
    assert client_args["endpoint_url"] == reader.endpoint
    assert client_args["verify"] is True
    config = client_args["config"]
    assert config.signature_version == "s3v4"
    assert config.s3 == {"addressing_style": "path", "use_accelerate_endpoint": False}
    assert config.use_dualstack_endpoint is False
    assert config.use_fips_endpoint is False
    assert config.inject_host_prefix is False
    assert config.ignore_configured_endpoint_urls is True
    assert config.proxies == {}
    assert config.retries == {"mode": "standard", "total_max_attempts": 1}
    assert config.connect_timeout == 3 and config.read_timeout == 5
    assert config.request_checksum_calculation == "when_supported"
    assert config.response_checksum_validation == "when_supported"
    assert len(client.meta.events.registrations) == 1
    event_name, guard, _unique_id = client.meta.events.registrations[0]
    assert event_name == "before-send.s3"

    target = (
        "http://127.0.0.1:9000/synthetic-audit/checkpoints/synthetic-object.json"
        "?versionId=older%2Bversion%2F1"
    )
    guard(request=SimpleNamespace(method="GET", url=target))
    error = _capture(lambda: guard(request=SimpleNamespace(method="GET", url=target)))
    _assert_controlled(error, "ROUTING_REJECTED")


def test_second_same_origin_send_is_rejected() -> None:
    reader = _reader()
    ref = _ref()
    target = (
        "http://127.0.0.1:9000/synthetic-audit/checkpoints/synthetic-object.json"
        "?versionId=older%2Bversion%2F1"
    )
    request = SimpleNamespace(method="GET", url=target)
    guard = raw_transport._exact_request_guard(reader, ref)
    guard(request=request)
    error = _capture(lambda: guard(request=request))
    _assert_controlled(error, "ROUTING_REJECTED")


@pytest.mark.parametrize(
    ("key", "version_id", "encoded_path", "encoded_query"),
    [
        ("a%2Fb+c?d#e", "null", "a%252Fb%2Bc%3Fd%23e", "null"),
        ("dots/.././kept", "v%2F+x", "dots/.././kept", "v%252F%2Bx"),
        ("unicode/雪", "版本/1", "unicode/%E9%9B%AA", "%E7%89%88%E6%9C%AC%2F1"),
        ("/leading//slashes", "v", "/leading//slashes", "v"),
    ],
)
def test_guard_preserves_opaque_encoded_target_and_rejects_first_mismatch(
    key: str,
    version_id: str,
    encoded_path: str,
    encoded_query: str,
) -> None:
    reader = _reader()
    ref = _ref(key=key, version_id=version_id)
    valid = SimpleNamespace(
        method="GET",
        url=f"{reader.endpoint}/{reader.bucket}/{encoded_path}?versionId={encoded_query}",
    )
    raw_transport._exact_request_guard(reader, ref)(request=valid)

    wrong = SimpleNamespace(method="HEAD", url=valid.url)
    guard = raw_transport._exact_request_guard(reader, ref)
    first = _capture(lambda: guard(request=wrong))
    second = _capture(lambda: guard(request=valid))
    _assert_controlled(first, "ROUTING_REJECTED")
    _assert_controlled(second, "ROUTING_REJECTED")


def test_factory_registration_failure_owns_cleanup_and_preserves_both_faults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = RuntimeError("registration")
    cleanup = EndpointConnectionError(endpoint_url="https://secret.invalid")
    client = _FactoryClient(registration_error=registration, close_error=cleanup)
    _fake_boto3(monkeypatch, client)

    error = _capture(lambda: raw_transport._create_client(_reader(), _ref()))

    assert isinstance(error, BaseExceptionGroup)
    assert error.exceptions[0] is registration
    _assert_controlled(error.exceptions[1], "CLEANUP_FAILED")
    assert client.close_calls == 1
