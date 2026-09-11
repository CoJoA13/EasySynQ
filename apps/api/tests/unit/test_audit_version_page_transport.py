from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    FlexibleChecksumError,
    IncompleteReadError,
    ProxyConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
    SSLError,
)
from botocore.hooks import HierarchicalEmitter

from easysynq_api.services.audit import version_page_transport as transport
from easysynq_api.services.audit.sink import ExplicitHistoryReader

pytestmark = pytest.mark.unit

ORG = UUID("11111111-1111-4111-8111-111111111111")
PREFIX = f"checkpoints/{ORG}/"
BODY = (
    b'<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b"<Name>synthetic-audit</Name>"
    b"<Prefix>checkpoints%2F11111111-1111-4111-8111-111111111111%2F</Prefix>"
    b"<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
    b"<IsTruncated>false</IsTruncated>"
    b"<Version><Key>checkpoints%2F11111111-1111-4111-8111-111111111111%2Fliteral%252B%2B.json</Key>"
    b"<VersionId>opaque%2F+/v1</VersionId><IsLatest>true</IsLatest></Version>"
    b"</ListVersionsResult>"
)
EXPECTED_TARGET = (
    "/synthetic-audit?versions&prefix=checkpoints%2F11111111-1111-4111-8111-111111111111%2F"
    "&max-keys=1000&encoding-type=url"
)


class _ResponseServer(ThreadingHTTPServer):
    body: bytes
    requests: list[str]
    status: int
    headers: dict[str, str]
    request_headers: list[dict[str, str]]


class _ResponseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        self.request.settimeout(3)
        super().setup()

    def do_GET(self) -> None:
        server = cast(_ResponseServer, self.server)
        server.requests.append(self.path)
        server.request_headers.append(dict(self.headers))
        self.send_response(server.status)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(server.body)))
        for name, value in server.headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(server.body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


@contextmanager
def _response_server(
    body: bytes, *, status: int = 200, headers: dict[str, str] | None = None
) -> Iterator[tuple[str, _ResponseServer]]:
    server = _ResponseServer(("127.0.0.1", 0), _ResponseHandler)
    server.body = body
    server.requests = []
    server.status = status
    server.headers = {} if headers is None else headers
    server.request_headers = []
    owner_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="version-page-response-server",
    )
    owner_thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        owner_thread.join(timeout=3)
        assert not owner_thread.is_alive()


def _reader(endpoint: str) -> ExplicitHistoryReader:
    return ExplicitHistoryReader(
        endpoint, "synthetic-audit", "us-east-1", "synthetic-reader", "synthetic-secret"
    )


def test_preserves_original_page_and_opaque_identity() -> None:
    from easysynq_api.services.audit.version_page_transport import read_raw_checkpoint_version_page

    with _response_server(BODY) as (endpoint, server):
        reader = ExplicitHistoryReader(
            endpoint, "synthetic-audit", "us-east-1", "synthetic-reader", "synthetic-secret"
        )
        result = read_raw_checkpoint_version_page(reader, ORG)

    assert result.body == BODY
    assert [(ref.key, ref.version_id) for ref in result.page.versions] == [
        (PREFIX + "literal%2B+.json", "opaque%2F+/v1")
    ]
    assert result.page.truncated is False
    assert result.page.delete_markers == ()
    assert result.page.next_key_marker is None
    assert result.page.next_version_id_marker is None
    assert server.requests == [EXPECTED_TARGET]


@pytest.mark.parametrize(
    "body,code",
    [
        (BODY[:-1], "RESPONSE_INVALID"),
        (BODY.replace(b"<IsTruncated>false", b"<IsTruncated>False"), "RESPONSE_INVALID"),
        (BODY.replace(b"<IsLatest>true", b"<IsLatest>1"), "RESPONSE_INVALID"),
        (BODY.replace(b"<Name>", b"<Name>synthetic-audit</Name><Name>"), "RESPONSE_INVALID"),
        (BODY.replace(b"synthetic-audit", b"synthetic-other"), "SCOPE_MISMATCH"),
    ],
)
def test_invalid_original_200_page_keeps_decoder_classification(body: bytes, code: str) -> None:
    from easysynq_api.services.audit.version_page_transport import (
        VersionPageReadError,
        read_raw_checkpoint_version_page,
    )

    with _response_server(body) as (endpoint, server):
        with pytest.raises(VersionPageReadError) as raised:
            read_raw_checkpoint_version_page(_reader(endpoint), ORG)
    assert raised.value.code == code
    assert str(raised.value) == "checkpoint version page read failed"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert server.requests == [EXPECTED_TARGET]


@pytest.mark.parametrize("version_marker", [None, "opaque%+/雪\t", "null"])
def test_replays_opaque_key_and_version_markers(version_marker: str | None) -> None:
    from easysynq_api.services.audit.version_page_transport import read_raw_checkpoint_version_page

    key_marker = PREFIX + "literal%+/雪\x01"
    echoes = (
        b"<KeyMarker>checkpoints%2F11111111-1111-4111-8111-111111111111%2F"
        b"literal%25%2B%2F%E9%9B%AA%01</KeyMarker>"
    )
    expected_target = (
        EXPECTED_TARGET + "&key-marker=checkpoints%2F11111111-1111-4111-8111-111111111111%2F"
        "literal%25%2B%2F%E9%9B%AA%01"
    )
    if version_marker == "null":
        echoes += b"<VersionIdMarker>null</VersionIdMarker>"
        expected_target += "&version-id-marker=null"
    elif version_marker is not None:
        echoes += "<VersionIdMarker>opaque%+/雪&#x9;</VersionIdMarker>".encode()
        expected_target += "&version-id-marker=opaque%25%2B%2F%E9%9B%AA%09"
    body = BODY.replace(b"<MaxKeys>", echoes + b"<MaxKeys>")
    with _response_server(body) as (endpoint, server):
        result = read_raw_checkpoint_version_page(
            _reader(endpoint), ORG, key_marker=key_marker, version_id_marker=version_marker
        )
    assert result.body == body
    assert result.page.versions[0].version_id == "opaque%2F+/v1"
    assert server.requests == [expected_target]


@pytest.mark.parametrize("status", [403, 301])
def test_provider_error_never_reaches_redirect_target(status: int) -> None:
    from easysynq_api.services.audit.version_page_transport import (
        VersionPageReadError,
        read_raw_checkpoint_version_page,
    )

    with _response_server(BODY) as (redirect_endpoint, redirect_server):
        with _response_server(
            b"provider-private-content synthetic-secret",
            status=status,
            headers={
                "Location": redirect_endpoint + EXPECTED_TARGET,
                "x-amz-bucket-region": "us-west-2",
            },
        ) as (endpoint, server):
            with pytest.raises(VersionPageReadError) as raised:
                read_raw_checkpoint_version_page(_reader(endpoint), ORG)
    assert raised.value.code == "PROVIDER_FAILURE"
    assert str(raised.value) == "checkpoint version page read failed"
    assert raised.value.__cause__ is None
    assert server.requests == [EXPECTED_TARGET]
    assert redirect_server.requests == []


@pytest.mark.parametrize("extra", [0, 1])
def test_original_body_boundary(extra: int) -> None:
    from easysynq_api.services.audit.version_page_transport import (
        VersionPageReadError,
        read_raw_checkpoint_version_page,
    )

    body = BODY + b" " * (16_777_216 - len(BODY) + extra)
    with _response_server(body) as (endpoint, server):
        if extra:
            with pytest.raises(VersionPageReadError) as raised:
                read_raw_checkpoint_version_page(_reader(endpoint), ORG)
            assert raised.value.code == "BODY_LIMIT"
        else:
            result = read_raw_checkpoint_version_page(_reader(endpoint), ORG)
            assert result.body == body
    assert server.requests == [EXPECTED_TARGET]


@pytest.mark.parametrize("extra", [0, 1])
def test_combined_observation_boundary_preserves_duplicates(extra: int) -> None:
    from easysynq_api.services.audit.version_page_transport import (
        VersionPageReadError,
        read_raw_checkpoint_version_page,
    )

    entry = (
        b"<Version><Key>checkpoints%2F11111111-1111-4111-8111-111111111111%2Fsame</Key>"
        b"<VersionId>null</VersionId><IsLatest>false</IsLatest></Version>"
    )
    marker = entry.replace(b"Version>", b"DeleteMarker>")
    body = BODY[: BODY.index(b"<Version>")] + entry * 500 + marker * (500 + extra)
    body += b"</ListVersionsResult>"
    with _response_server(body) as (endpoint, server):
        if extra:
            with pytest.raises(VersionPageReadError) as raised:
                read_raw_checkpoint_version_page(_reader(endpoint), ORG)
            assert raised.value.code == "PAGE_LIMIT"
        else:
            result = read_raw_checkpoint_version_page(_reader(endpoint), ORG)
            assert result.body == body
            assert [(ref.key, ref.version_id) for ref in result.page.versions] == [
                (PREFIX + "same", "null")
            ] * 500
            assert result.page.delete_markers == result.page.versions
    assert server.requests == [EXPECTED_TARGET]


class _Client:
    """Fault seam at the owned SDK client; decoding and outcome handling stay real."""

    def __init__(self) -> None:
        self.capture: Callable[..., None] | None = None
        self.meta = SimpleNamespace(events=HierarchicalEmitter())
        self.response: Any = {"status_code": 200, "body": BODY, "headers": {}}
        self.operation: Any = SimpleNamespace(name="ListObjectVersions")
        self.operation_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.on_operation: Callable[[], None] | None = None
        self.on_close: Callable[[], None] | None = None
        self.normal_return = False
        self.repeat_capture = False
        self.close_calls = 0
        self.requests: list[dict[str, Any]] = []

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        if self.on_operation is not None:
            self.on_operation()
        if self.operation_error is not None:
            raise self.operation_error
        if self.normal_return:
            return {"Versions": [], "IsTruncated": False}
        assert self.capture is not None
        if self.repeat_capture:
            try:
                self.capture(operation_model=self.operation, response_dict=self.response)
            except BaseException as error:  # noqa: BLE001 - simulate swallowed capture sentinel
                assert isinstance(error, transport._PageCaptured)
        self.capture(operation_model=self.operation, response_dict=self.response)
        raise AssertionError("capture unexpectedly returned")

    def close(self) -> None:
        self.close_calls += 1
        if self.on_close is not None:
            self.on_close()
        if self.close_error is not None:
            raise self.close_error


def _install(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    def create(_reader: ExplicitHistoryReader, capture: Callable[..., None]) -> _Client:
        client.capture = capture
        return client

    monkeypatch.setattr(transport, "_create_client", create)


def _capture(call: Callable[[], Any]) -> BaseException:
    try:
        call()
    except BaseException as error:  # noqa: BLE001 - test the public fatal identity contract
        return error
    raise AssertionError("operation unexpectedly returned")


def _assert_controlled(error: BaseException, code: str) -> None:
    assert type(error) is transport.VersionPageReadError
    assert error.code == code
    assert str(error) == "checkpoint version page read failed"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def _read(**kwargs: Any) -> transport.RawCheckpointVersionPage:
    return transport.read_raw_checkpoint_version_page(_reader("http://127.0.0.1:9"), ORG, **kwargs)


@pytest.mark.parametrize("with_primary,with_cleanup", [(True, False), (False, True), (True, True)])
def test_final_clock_fault_preserves_all_concurrent_fault_identities(
    monkeypatch: pytest.MonkeyPatch, with_primary: bool, with_cleanup: bool
) -> None:
    primary = RuntimeError("synthetic operation fault")
    cleanup = KeyboardInterrupt("synthetic close fault")
    final = SystemExit("synthetic final clock fault")
    client = _Client()
    client.operation_error = primary if with_primary else None
    client.close_error = cleanup if with_cleanup else None
    closed = False

    def mark_closed() -> None:
        nonlocal closed
        closed = True

    def clock() -> float:
        if closed:
            raise final
        return 0.0

    client.on_close = mark_closed
    monkeypatch.setattr(transport, "_monotonic", clock)
    _install(monkeypatch, client)
    error = _capture(_read)
    assert isinstance(error, BaseExceptionGroup)
    expected = ([primary] if with_primary else []) + ([cleanup] if with_cleanup else []) + [final]
    assert len(error.exceptions) == len(expected)
    assert all(any(item is wanted for item in error.exceptions) for wanted in expected)
    assert error.message == "checkpoint version page operation and cleanup failed"
    assert client.close_calls == 1


class _String(str):
    pass


class _Reader(ExplicitHistoryReader):
    pass


class _UUID(UUID):
    pass


class _Event(threading.Event):
    pass


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", None),
        ("endpoint", _String("http://127.0.0.1:9")),
        ("endpoint", "http://example.invalid"),
        ("endpoint", "http://127.0.0.1:9/"),
        ("endpoint", "https://user:private@example.invalid"),
        ("endpoint", "https://example.invalid/extra"),
        ("endpoint", "https://" + "x" * 268),
        ("endpoint", "https://雪.invalid"),
        ("bucket", True),
        ("bucket", _String("synthetic-audit")),
        ("bucket", "Bad_Bucket"),
        ("bucket", "x" * 64),
        ("region", False),
        ("region", _String("us-east-1")),
        ("region", "x" * 64),
        ("region", "invalid region"),
        ("access_key", ""),
        ("access_key", "x" * 4097),
        ("access_key", _String("synthetic-reader")),
        ("access_key", " synthetic-reader"),
        ("secret_key", ""),
        ("secret_key", "x" * 4097),
        ("secret_key", _String("synthetic-secret")),
        ("secret_key", False),
    ],
)
def test_rejects_reader_fields_before_client_creation(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    def forbidden(*_args: Any) -> Any:
        pytest.fail("invalid input reached client creation")

    monkeypatch.setattr(transport, "_create_client", forbidden)
    reader = dataclasses.replace(_reader("http://127.0.0.1:9"), **{field: value})
    with pytest.raises(transport.VersionPageInputError) as raised:
        transport.read_raw_checkpoint_version_page(reader, ORG)
    assert str(raised.value) == "invalid checkpoint version page read input"
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "changes",
    [
        {"reader": None},
        {"reader": _Reader("http://127.0.0.1:9", "synthetic-audit", "", "a", "b")},
        {"org_id": str(ORG)},
        {"org_id": _UUID(str(ORG))},
        {"org_id": True},
        {"key_marker": ""},
        {"key_marker": "checkpoints/other/key"},
        {"key_marker": _String(PREFIX + "key")},
        {"key_marker": PREFIX + "雪" * 400},
        {"key_marker": PREFIX + "\ud800"},
        {"key_marker": True},
        {"version_id_marker": "null"},
        {"key_marker": PREFIX + "key", "version_id_marker": ""},
        {"key_marker": PREFIX + "key", "version_id_marker": _String("null")},
        {"key_marker": PREFIX + "key", "version_id_marker": "雪" * 342},
        {"key_marker": PREFIX + "key", "version_id_marker": "\ud800"},
        {"cancel": _Event()},
        {"cancel": True},
    ],
)
def test_rejects_exact_argument_types_and_opaque_marker_bounds_before_io(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    def forbidden(*_args: Any) -> Any:
        pytest.fail("invalid input reached client creation")

    monkeypatch.setattr(transport, "_create_client", forbidden)
    arguments = {"reader": _reader("http://127.0.0.1:9"), "org_id": ORG, **changes}
    with pytest.raises(transport.VersionPageInputError) as raised:
        transport.read_raw_checkpoint_version_page(**arguments)
    assert str(raised.value) == "invalid checkpoint version page read input"


def test_empty_region_retains_r73_pre_io_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: Any) -> Any:
        pytest.fail("empty region reached client creation")

    monkeypatch.setattr(transport, "_create_client", forbidden)
    reader = dataclasses.replace(_reader("http://127.0.0.1:9"), region="")
    with pytest.raises(transport.VersionPageInputError) as raised:
        transport.read_raw_checkpoint_version_page(reader, ORG)
    assert str(raised.value) == "invalid checkpoint version page read input"


def test_retains_truncated_opaque_next_cursor_and_duplicate_delete_marker() -> None:
    body = BODY.replace(b"<IsTruncated>false", b"<IsTruncated>true")
    extra = (
        b"<NextKeyMarker>checkpoints%2F11111111-1111-4111-8111-111111111111%2F"
        b"next%252F%2B%00%E9%9B%AA</NextKeyMarker>"
        b"<NextVersionIdMarker>opaque%2F+/null</NextVersionIdMarker>"
        b"<DeleteMarker><Key>checkpoints%2F11111111-1111-4111-8111-111111111111%2Fsame</Key>"
        b"<VersionId>null</VersionId><IsLatest>true</IsLatest></DeleteMarker>"
    )
    body = body.replace(b"</ListVersionsResult>", extra + b"</ListVersionsResult>")
    with _response_server(body) as (endpoint, server):
        result = transport.read_raw_checkpoint_version_page(_reader(endpoint), ORG)
    assert result.body == body
    assert result.page.truncated is True
    assert result.page.next_key_marker == PREFIX + "next%2F+\x00雪"
    assert result.page.next_version_id_marker == "opaque%2F+/null"
    assert [(ref.key, ref.version_id) for ref in result.page.delete_markers] == [
        (PREFIX + "same", "null")
    ]
    assert server.requests == [EXPECTED_TARGET]


class _ResponseDict(dict[str, Any]):
    pass


class _BodyBytes(bytes):
    pass


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        _ResponseDict(status_code=200, body=BODY),
        {"status_code": True, "body": BODY},
        {"status_code": "200", "body": BODY},
        {"status_code": 200.0, "body": BODY},
        {"status_code": 200},
        {"body": BODY},
        {"status_code": 200, "body": bytearray(BODY)},
        {"status_code": 200, "body": _BodyBytes(BODY)},
    ],
)
def test_strict_original_capture_shape(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    client = _Client()
    client.response = response
    _install(monkeypatch, client)
    _assert_controlled(_capture(_read), "RESPONSE_INVALID")
    assert client.close_calls == 1


@pytest.mark.parametrize("mode", ["wrong_operation", "missing_operation", "normal", "duplicate"])
def test_capture_integrity_is_required_for_public_success(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    client = _Client()
    if mode == "wrong_operation":
        client.operation = SimpleNamespace(name="GetObject")
    elif mode == "missing_operation":
        client.operation = None
    elif mode == "normal":
        client.normal_return = True
    else:
        client.repeat_capture = True
    _install(monkeypatch, client)
    _assert_controlled(_capture(_read), "RESPONSE_INVALID")
    assert client.close_calls == 1


def test_unowned_capture_sentinel_retains_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    foreign = transport._PageCaptured()
    client = _Client()
    client.operation_error = foreign
    _install(monkeypatch, client)
    assert _capture(_read) is foreign
    assert client.close_calls == 1


_SDK_ERRORS = [
    (
        ClientError(
            {"Error": {"Code": "private", "Message": "synthetic-secret"}}, "ListObjectVersions"
        ),
        "PROVIDER_FAILURE",
    ),
    (EndpointConnectionError(endpoint_url="synthetic-secret"), "TRANSPORT_FAILURE"),
    (SSLError(endpoint_url="synthetic-secret", error="private"), "TRANSPORT_FAILURE"),
    (ConnectTimeoutError(endpoint_url="synthetic-secret"), "TRANSPORT_FAILURE"),
    (ReadTimeoutError(endpoint_url="synthetic-secret"), "TRANSPORT_FAILURE"),
    (ProxyConnectionError(proxy_url="synthetic-secret"), "TRANSPORT_FAILURE"),
    (ConnectionClosedError(endpoint_url="synthetic-secret"), "TRANSPORT_FAILURE"),
    (ResponseStreamingError(error="synthetic-secret"), "TRANSPORT_FAILURE"),
    (IncompleteReadError(actual_bytes=1, expected_bytes=2), "LENGTH_MISMATCH"),
    (FlexibleChecksumError(error_msg="synthetic-secret"), "RESPONSE_INVALID"),
]


@pytest.mark.parametrize("error,code", _SDK_ERRORS)
@pytest.mark.parametrize("stage", ["construction", "operation", "close"])
def test_known_sdk_fault_classification_and_fixed_diagnostics(
    monkeypatch: pytest.MonkeyPatch, error: BaseException, code: str, stage: str
) -> None:
    client = _Client()
    _install(monkeypatch, client)
    if stage == "construction":

        def fail(*_args: Any) -> Any:
            raise error

        monkeypatch.setattr(transport, "_create_client", fail)
    elif stage == "operation":
        client.operation_error = error
    else:
        client.close_error = error
    _assert_controlled(_capture(_read), "CLEANUP_FAILED" if stage == "close" else code)
    assert client.close_calls == (0 if stage == "construction" else 1)


@pytest.mark.parametrize("stage", ["construction", "registration", "operation", "close"])
@pytest.mark.parametrize("fault_type", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_unexpected_fault_identity_survives_every_owned_stage(
    monkeypatch: pytest.MonkeyPatch, stage: str, fault_type: type[BaseException]
) -> None:
    fault = fault_type("synthetic fatal fault")
    client = _Client()
    _install(monkeypatch, client)

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise fault

    if stage == "construction":
        monkeypatch.setattr(transport, "_create_client", fail)
    elif stage == "registration":
        monkeypatch.setattr(client.meta.events, "register", fail)
    elif stage == "operation":
        client.operation_error = fault
    else:
        client.close_error = fault
    assert _capture(_read) is fault
    assert client.close_calls == (0 if stage == "construction" else 1)
    if stage == "registration":
        assert client.requests == []


@pytest.mark.parametrize("registration", [False, True])
def test_operation_and_cleanup_faults_preserve_both_identities(
    monkeypatch: pytest.MonkeyPatch, registration: bool
) -> None:
    operation_fault = RuntimeError("synthetic primary")
    close_fault = KeyboardInterrupt("synthetic cleanup")
    client = _Client()
    client.close_error = close_fault
    _install(monkeypatch, client)
    if registration:

        def fail(*_args: Any, **_kwargs: Any) -> None:
            raise operation_fault

        monkeypatch.setattr(client.meta.events, "register", fail)
    else:
        client.operation_error = operation_fault
    error = _capture(_read)
    assert isinstance(error, BaseExceptionGroup)
    assert error.exceptions == (operation_fault, close_fault)
    assert error.message == "checkpoint version page operation and cleanup failed"
    assert client.close_calls == 1


def test_controlled_registration_failure_closes_client_even_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    client.close_error = ReadTimeoutError(endpoint_url="synthetic-secret")
    _install(monkeypatch, client)

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise EndpointConnectionError(endpoint_url="synthetic-secret")

    monkeypatch.setattr(client.meta.events, "register", fail)
    error = _capture(_read)
    assert isinstance(error, ExceptionGroup)
    assert len(error.exceptions) == 2
    _assert_controlled(error.exceptions[0], "TRANSPORT_FAILURE")
    _assert_controlled(error.exceptions[1], "CLEANUP_FAILED")
    assert client.requests == []
    assert client.close_calls == 1


@pytest.mark.parametrize("stage", ["before", "construction", "operation", "close"])
def test_cancellation_checked_before_io_and_after_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    cancel = threading.Event()
    client = _Client()
    _install(monkeypatch, client)
    if stage == "before":
        cancel.set()
    elif stage == "construction":

        def create(_reader: ExplicitHistoryReader, capture: Callable[..., None]) -> _Client:
            cancel.set()
            client.capture = capture
            return client

        monkeypatch.setattr(transport, "_create_client", create)
    elif stage == "operation":
        client.on_operation = cancel.set
    else:
        client.on_close = cancel.set
    error = _capture(lambda: _read(cancel=cancel))
    assert type(error) is transport.VersionPageReadCancelled
    assert str(error) == "checkpoint version page read cancelled"
    assert client.close_calls == (0 if stage == "before" else 1)
    if stage in {"before", "construction"}:
        assert client.requests == []


@pytest.mark.parametrize("stage", ["construction", "operation", "close"])
def test_deadline_checked_after_every_owned_stage(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    now = [0.0]
    monkeypatch.setattr(transport, "_monotonic", lambda: now[0])

    def expire() -> None:
        now[0] = 25.0

    client = _Client()
    _install(monkeypatch, client)
    if stage == "construction":

        def create(_reader: ExplicitHistoryReader, capture: Callable[..., None]) -> _Client:
            expire()
            client.capture = capture
            return client

        monkeypatch.setattr(transport, "_create_client", create)
    elif stage == "operation":
        client.on_operation = expire
    else:
        client.on_close = expire
    _assert_controlled(_capture(_read), "DEADLINE_EXCEEDED")
    assert client.close_calls == 1
    if stage == "construction":
        assert client.requests == []


@pytest.mark.parametrize("unexpected", [False, True])
def test_cancellation_precedence_retains_unexpected_primary_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, unexpected: bool
) -> None:
    cancel = threading.Event()
    primary: BaseException = (
        KeyboardInterrupt("synthetic primary")
        if unexpected
        else EndpointConnectionError(endpoint_url="synthetic-secret")
    )
    cleanup = SystemExit("synthetic cleanup")
    client = _Client()
    client.operation_error = primary
    client.close_error = cleanup
    client.on_close = cancel.set
    _install(monkeypatch, client)
    error = _capture(lambda: _read(cancel=cancel))
    assert isinstance(error, BaseExceptionGroup)
    assert len(error.exceptions) == (3 if unexpected else 2)
    if unexpected:
        assert error.exceptions[0] is primary
    assert type(error.exceptions[-2]) is transport.VersionPageReadCancelled
    assert error.exceptions[-1] is cleanup


def test_cancellation_observed_after_failed_operation_survives_clear_during_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = threading.Event()
    client = _Client()
    client.on_operation = cancel.set
    client.operation_error = EndpointConnectionError(endpoint_url="synthetic-secret")
    client.on_close = cancel.clear
    _install(monkeypatch, client)
    error = _capture(lambda: _read(cancel=cancel))
    assert type(error) is transport.VersionPageReadCancelled
    assert client.close_calls == 1


def test_cancellation_wins_over_simultaneous_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = threading.Event()
    now = [0.0]
    monkeypatch.setattr(transport, "_monotonic", lambda: now[0])
    client = _Client()

    def stop() -> None:
        now[0] = 25.0
        cancel.set()

    client.on_close = stop
    _install(monkeypatch, client)
    assert type(_capture(lambda: _read(cancel=cancel))) is transport.VersionPageReadCancelled
    assert client.close_calls == 1


def test_diagnosed_failure_survives_deadline_during_close(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(transport, "_monotonic", lambda: now[0])
    client = _Client()
    client.response = {"status_code": 403, "body": b"private"}
    client.on_close = lambda: now.__setitem__(0, 25.0)
    _install(monkeypatch, client)
    _assert_controlled(_capture(_read), "PROVIDER_FAILURE")
    assert client.close_calls == 1


def test_owned_sdk_session_resists_ambient_credentials_routing_and_proxy_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "synthetic-config"
    config_path.write_text(
        "[default]\nregion = us-west-2\nendpoint_url = http://127.0.0.1:1\n"
        "retry_mode = adaptive\nmax_attempts = 5\nuse_fips_endpoint = true\n"
        "use_dualstack_endpoint = true\ns3 =\n"
        "    addressing_style = virtual\n    use_accelerate_endpoint = true\n",
        encoding="utf-8",
    )
    credentials_path = tmp_path / "synthetic-credentials"
    credentials_path.write_text(
        "[default]\naws_access_key_id = ambient-reader\n"
        "aws_secret_access_key = ambient-secret\naws_session_token = ambient-token\n",
        encoding="utf-8",
    )
    for name, value in {
        "AWS_CONFIG_FILE": str(config_path),
        "AWS_SHARED_CREDENTIALS_FILE": str(credentials_path),
        "AWS_ACCESS_KEY_ID": "ambient-reader",
        "AWS_SECRET_ACCESS_KEY": "ambient-secret",
        "AWS_SESSION_TOKEN": "ambient-token",
        "AWS_DEFAULT_REGION": "us-west-2",
        "AWS_ENDPOINT_URL": "http://127.0.0.1:1",
        "AWS_ENDPOINT_URL_S3": "http://127.0.0.1:1",
        "AWS_USE_FIPS_ENDPOINT": "true",
        "AWS_USE_DUALSTACK_ENDPOINT": "true",
        "AWS_MAX_ATTEMPTS": "5",
        "AWS_CA_BUNDLE": "/synthetic/nonexistent-ca.pem",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "HTTP_PROXY": "http://127.0.0.1:1",
        "https_proxy": "http://127.0.0.1:1",
        "http_proxy": "http://127.0.0.1:1",
        "NO_PROXY": "",
        "no_proxy": "",
    }.items():
        monkeypatch.setenv(name, value)
    original_create = transport._create_client
    observed: list[Any] = []
    original_config = transport.Config
    requested_configs: list[Any] = []

    def record_config(**kwargs: Any) -> Any:
        config = original_config(**kwargs)
        requested_configs.append(config)
        return config

    def create(reader: ExplicitHistoryReader, capture: Callable[..., None]) -> Any:
        client = original_create(reader, capture)
        observed.append(client)
        return client

    monkeypatch.setattr(transport, "_create_client", create)
    monkeypatch.setattr(transport, "Config", record_config)
    with _response_server(BODY) as (endpoint, server):
        result = transport.read_raw_checkpoint_version_page(_reader(endpoint), ORG)
    assert result.body == BODY
    assert server.requests == [EXPECTED_TARGET]
    headers = {name.lower(): value for name, value in server.request_headers[0].items()}
    assert "Credential=synthetic-reader/" in headers["authorization"]
    assert "/us-east-1/s3/aws4_request" in headers["authorization"]
    assert "x-amz-security-token" not in headers
    assert "ambient" not in str(headers)
    assert len(observed) == 1
    client = observed[0]
    assert client._endpoint.http_session._verify is True
    assert len(requested_configs) == 1
    config = requested_configs[0]
    assert config.signature_version == "s3v4"
    assert config.s3["addressing_style"] == "path"
    assert config.s3["use_accelerate_endpoint"] is False
    assert config.use_dualstack_endpoint is False
    assert config.use_fips_endpoint is False
    assert config.inject_host_prefix is False
    assert config.ignore_configured_endpoint_urls is True
    assert config.proxies == {}
    assert config.retries == {"mode": "standard", "total_max_attempts": 1}
    assert config.connect_timeout == 3
    assert config.read_timeout == 5


@pytest.mark.parametrize(
    "mutation",
    ["method", "scheme", "authority", "path", "query", "fragment", "userinfo", "malformed"],
)
def test_guard_rejects_tampered_final_sdk_request_before_any_physical_send(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    original_create = transport._create_client

    def create(reader: ExplicitHistoryReader, capture: Callable[..., None]) -> Any:
        client = original_create(reader, capture)

        def tamper(*, request: Any, **_kwargs: Any) -> None:
            if mutation == "method":
                request.method = "POST"
            elif mutation == "scheme":
                request.url = request.url.replace("http:", "https:", 1)
            elif mutation == "authority":
                request.url = "http://127.0.0.1:1" + EXPECTED_TARGET
            elif mutation == "path":
                request.url = request.url.replace("/synthetic-audit?", "/synthetic-other?")
            elif mutation == "query":
                request.url += "&delimiter=%2F"
            elif mutation == "fragment":
                request.url += "#private"
            elif mutation == "userinfo":
                request.url = request.url.replace("http://", "http://private@", 1)
            else:
                request.url = "http://["

        client.meta.events.register_first("before-send.s3.*", tamper)
        return client

    monkeypatch.setattr(transport, "_create_client", create)
    with _response_server(BODY) as (endpoint, server):
        error = _capture(lambda: transport.read_raw_checkpoint_version_page(_reader(endpoint), ORG))
    _assert_controlled(error, "ROUTING_REJECTED")
    assert server.requests == []


def test_second_physical_send_event_is_rejected_even_for_identical_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    request = SimpleNamespace(method="GET", url="http://127.0.0.1:9" + EXPECTED_TARGET)

    def repeat_send() -> None:
        client.meta.events.emit("before-send.s3.ListObjectVersions", request=request)
        client.meta.events.emit("before-send.s3.ListObjectVersions", request=request)

    client.on_operation = repeat_send
    _install(monkeypatch, client)
    _assert_controlled(_capture(_read), "ROUTING_REJECTED")
    assert client.close_calls == 1


def test_result_is_immutable_and_does_not_repr_original_provider_xml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()
    _install(monkeypatch, client)
    result = _read()
    assert "ListVersionsResult" not in repr(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.body = b"changed"  # type: ignore[misc]
    assert not hasattr(result, "__dict__")
    assert client.requests == [
        {"Bucket": "synthetic-audit", "Prefix": PREFIX, "MaxKeys": 1000, "EncodingType": "url"}
    ]
    assert client.close_calls == 1
