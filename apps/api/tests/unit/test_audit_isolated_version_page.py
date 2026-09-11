from __future__ import annotations

import errno
import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import pytest

from easysynq_api.services.audit import isolated_version_page as isolated
from easysynq_api.services.audit.sink import ExplicitHistoryReader
from easysynq_api.services.audit.version_page_transport import (
    VersionPageInputError,
    VersionPageReadCancelled,
    VersionPageReadError,
)

pytestmark = pytest.mark.unit

ORG = UUID("11111111-1111-4111-8111-111111111111")
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
    mode: str
    received: threading.Event
    released: threading.Event


class _ResponseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        self.request.settimeout(3)
        super().setup()

    def do_GET(self) -> None:
        server = cast(_ResponseServer, self.server)
        server.requests.append(self.path)
        server.received.set()
        if server.mode == "blocked":
            server.released.wait(timeout=6)
            return
        self.send_response(server.status)
        self.send_header("Content-Type", "application/xml")
        length = 600 * 1_048_576 if server.mode == "memory" else len(server.body)
        self.send_header("Content-Length", str(length))
        self.end_headers()
        try:
            if server.mode == "memory":
                chunk = b"x" * 1_048_576
                for _ in range(600):
                    self.wfile.write(chunk)
            else:
                self.wfile.write(server.body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def log_message(self, _format: str, *_args: object) -> None:
        pass


@contextmanager
def _response_server(
    body: bytes, *, status: int = 200, mode: str = "normal"
) -> Iterator[tuple[str, _ResponseServer]]:
    server = _ResponseServer(("127.0.0.1", 0), _ResponseHandler)
    server.body = body
    server.requests = []
    server.status = status
    server.mode = mode
    server.received = threading.Event()
    server.released = threading.Event()
    owner_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="isolated-version-page-response-server",
    )
    owner_thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        yield f"http://{host}:{port}", server
    finally:
        server.released.set()
        server.shutdown()
        server.server_close()
        owner_thread.join(timeout=3)
        assert not owner_thread.is_alive()


def test_reads_original_page_in_executed_worker() -> None:
    from easysynq_api.services.audit.isolated_version_page import (
        read_raw_checkpoint_version_page_isolated,
    )

    with _response_server(BODY) as (endpoint, server):
        reader = ExplicitHistoryReader(
            endpoint, "synthetic-audit", "us-east-1", "synthetic-reader", "synthetic-secret"
        )
        result = read_raw_checkpoint_version_page_isolated(reader, ORG)

    assert result.body == BODY
    assert result.page.versions[0].version_id == "opaque%2F+/v1"
    assert result.page.versions[0].key == (
        "checkpoints/11111111-1111-4111-8111-111111111111/literal%2B+.json"
    )
    assert result.page.truncated is False
    assert result.page.delete_markers == ()
    assert result.page.next_key_marker is None
    assert result.page.next_version_id_marker is None
    assert server.requests == [EXPECTED_TARGET]


READY = {
    "version": 1,
    "operation": "checkpoint-version-page",
    "status": "ready",
    "address_space": 536_870_912,
    "cpu_seconds": 20,
    "file_descriptors": 64,
    "file_bytes": 0,
    "core_bytes": 0,
}
REQUEST = {
    "version": 1,
    "operation": "checkpoint-version-page",
    "endpoint": "http://127.0.0.1:9",
    "bucket": "synthetic-audit",
    "region": "us-east-1",
    "access_key": "synthetic-reader",
    "secret_key": "synthetic-secret",
    "org_id": "11111111-1111-4111-8111-111111111111",
    "key_marker": None,
    "version_id_marker": None,
}
READ_CODES = (
    "PROVIDER_FAILURE",
    "TRANSPORT_FAILURE",
    "RESPONSE_INVALID",
    "BODY_LIMIT",
    "PAGE_LIMIT",
    "SCOPE_MISMATCH",
    "CURSOR_INVALID",
    "LENGTH_MISMATCH",
    "ROUTING_REJECTED",
    "DEADLINE_EXCEEDED",
    "CLEANUP_FAILED",
)


def _reader(endpoint: str = "http://127.0.0.1:9") -> ExplicitHistoryReader:
    return ExplicitHistoryReader(
        endpoint, "synthetic-audit", "us-east-1", "synthetic-reader", "synthetic-secret"
    )


def _read(**kwargs: Any) -> Any:
    return isolated.read_raw_checkpoint_version_page_isolated(_reader(), ORG, **kwargs)


def _capture(call: Callable[[], Any]) -> BaseException:
    try:
        call()
    except BaseException as error:  # noqa: BLE001 - fatal identity is an acceptance property
        return error
    raise AssertionError("call returned instead of raising")


def _assert_isolated(error: BaseException, code: str) -> None:
    assert type(error) is isolated.IsolatedVersionPageError
    assert error.code == code
    assert str(error) == "isolated checkpoint version page read failed"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def _json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()


def _frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


_FIXTURE_PREAMBLE = """
import json
import os
import signal
import sys
import time
signal.alarm(8)

def emit(payload):
    sys.stdout.buffer.write(len(payload).to_bytes(4, 'big') + payload)
    sys.stdout.buffer.flush()

def consume_request():
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        raise SystemExit(11)
    remaining = int.from_bytes(header, 'big')
    if remaining > 131072:
        raise SystemExit(12)
    chunks = []
    while remaining:
        chunk = sys.stdin.buffer.read(min(8192, remaining))
        if not chunk:
            raise SystemExit(13)
        chunks.append(chunk)
        remaining -= len(chunk)
    if sys.stdin.buffer.read(1) != b'':
        raise SystemExit(14)
    return json.loads(b''.join(chunks))
"""


def _script(tail: str, *, ready: bytes | None = None, consume: bool = True) -> str:
    return (
        _FIXTURE_PREAMBLE
        + f"\nemit({(_json(READY) if ready is None else ready)!r})\n"
        + ("request = consume_request()\n" if consume else "")
        + tail
    )


def _success_script(*, suffix: str = "", body: bytes = BODY) -> str:
    return _script(f"emit(b'VP1\\x00' + {body!r})\n" + suffix)


def _spawn_fixture(script: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and finite owned test fixture
        [sys.executable, "-I", "-B", "-u", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
        bufsize=0,
    )


def _assert_reaped(children: list[subprocess.Popen[bytes]]) -> None:
    assert children
    for child in children:
        assert child.returncode is not None
        assert child.stdin is not None and child.stdin.closed
        assert child.stdout is not None and child.stdout.closed
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)


@pytest.fixture
def worker_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str | None], list[subprocess.Popen[bytes]]]]:
    children: list[subprocess.Popen[bytes]] = []
    production_spawn = isolated._spawn_worker

    def install(script: str | None = None) -> list[subprocess.Popen[bytes]]:
        def spawn() -> subprocess.Popen[bytes]:
            process = production_spawn() if script is None else _spawn_fixture(script)
            children.append(process)
            return process

        monkeypatch.setattr(isolated, "_spawn_worker", spawn)
        return children

    yield install
    # This fallback prevents a failing assertion from leaking a fixture. Tests
    # assert production cleanup before fixture teardown, so rescue cannot pass them.
    for child in children:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=2)
        for stream in (child.stdin, child.stdout):
            if stream is not None and not stream.closed:
                stream.close()


@pytest.mark.parametrize(
    "payload",
    [
        _json({**READY, "version": True}),
        _json({**READY, "cpu_seconds": 20.0}),
        _json({**READY, "file_bytes": False}),
        _json({**READY, "operation": "retained-version"}),
        _json({**READY, "address_space": 536_870_913}),
        _json({**READY, "file_descriptors": 65}),
        _json({**READY, "core_bytes": 1}),
        _json({**READY, "status": "waiting"}),
        _json({**READY, "extra": 1}),
        _json({key: value for key, value in READY.items() if key != "operation"}),
        b'{"version":1,' + _json(READY)[1:],
        b'{"version":NaN}',
        b'{"version":Infinity}',
        b'{"version":{"nested":{"too":"deep"}}}',
        b'"ready"',
        b"\xff",
        b"",
    ],
)
def test_wrong_ready_never_sends_credentials(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    payload: bytes,
) -> None:
    writes: list[bytes] = []
    real_write = isolated._write_fd

    def observe(fd: int, chunk: bytes) -> int:
        writes.append(chunk)
        return real_write(fd, chunk)

    monkeypatch.setattr(isolated, "_write_fd", observe)
    children = worker_owner(_script("time.sleep(0.2)\n", ready=payload, consume=False))
    start = time.monotonic()
    _assert_isolated(_capture(_read), "PROTOCOL_INVALID")
    assert writes == []
    assert time.monotonic() - start < 3
    _assert_reaped(children)


@pytest.mark.parametrize(
    "output,code",
    [
        (b"", "WORKER_FAILED"),
        (b"\x00\x00", "PROTOCOL_INVALID"),
        ((513).to_bytes(4, "big"), "OUTPUT_LIMIT"),
    ],
)
def test_missing_truncated_or_oversized_ready_sends_no_request(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    output: bytes,
    code: str,
) -> None:
    def forbid(_fd: int, _chunk: bytes) -> int:
        raise AssertionError("credentials sent without exact READY")

    monkeypatch.setattr(isolated, "_write_fd", forbid)
    children = worker_owner(_FIXTURE_PREAMBLE + f"os.write(1, {output!r})\n")
    _assert_isolated(_capture(_read), code)
    _assert_reaped(children)


@pytest.mark.parametrize(
    "payload",
    [
        _json({**REQUEST, "version": True}),
        _json({**REQUEST, "operation": "wrong"}),
        _json({**REQUEST, "org_id": "11111111111141118111111111111111"}),
        _json({**REQUEST, "org_id": "{11111111-1111-4111-8111-111111111111}"}),
        _json({**REQUEST, "key_marker": 1}),
        _json({**REQUEST, "version_id_marker": "v"}),
        _json({**REQUEST, "region": ""}),
        _json({**REQUEST, "extra": None}),
        _json({key: value for key, value in REQUEST.items() if key != "key_marker"}),
        b'{"version":1,' + _json(REQUEST)[1:],
        b'{"version":NaN}',
        b'{"version":-Infinity}',
        b'{"version":{"nested":{"too":"deep"}}}',
        b"\xff",
        b"x" * 131_073,
    ],
)
def test_actual_worker_silently_rejects_invalid_request(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    payload: bytes,
) -> None:
    children = worker_owner(None)
    monkeypatch.setattr(isolated, "_request_payload", lambda *_args: payload)
    _assert_isolated(_capture(_read), "WORKER_FAILED")
    _assert_reaped(children)
    assert children[0].returncode == 1


def test_actual_worker_requires_stdin_eof_and_rejects_trailing_input(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(None)
    monkeypatch.setattr(isolated, "_frame", lambda payload: _frame(payload) + b"x")
    _assert_isolated(_capture(_read), "WORKER_FAILED")
    _assert_reaped(children)
    assert children[0].returncode == 1


@pytest.mark.parametrize(
    "wire,code",
    [
        (_frame(b"VP1\x02" + BODY), "PROTOCOL_INVALID"),
        (_frame(b"BAD\x00" + BODY), "PROTOCOL_INVALID"),
        (_frame(b"VP1"), "PROTOCOL_INVALID"),
        (_frame(b"VP1\x01UNKNOWN"), "PROTOCOL_INVALID"),
        (_frame(b"VP1\x01PROVIDER_FAILURE\n"), "PROTOCOL_INVALID"),
        (_frame(b"VP1\x01\xff"), "PROTOCOL_INVALID"),
        (_frame(b"VP1\x00" + BODY[:-1]), "PROTOCOL_INVALID"),
        (
            _frame(b"VP1\x00" + BODY.replace(b"synthetic-audit", b"synthetic-other")),
            "PROTOCOL_INVALID",
        ),
        (_frame(b"VP1\x00" + BODY) + b"x", "PROTOCOL_INVALID"),
        (_frame(b"VP1\x00" + BODY) * 2, "PROTOCOL_INVALID"),
        (_frame(b"VP1\x00" + BODY)[:-1], "PROTOCOL_INVALID"),
        (b"\x00\x00", "PROTOCOL_INVALID"),
        ((16_777_221).to_bytes(4, "big"), "OUTPUT_LIMIT"),
    ],
)
def test_finite_wrong_result_is_rejected_and_reaped(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    wire: bytes,
    code: str,
) -> None:
    children = worker_owner(_script(f"sys.stdout.buffer.write({wire!r})\n"))
    _assert_isolated(_capture(_read), code)
    _assert_reaped(children)


@pytest.mark.parametrize("code", READ_CODES)
def test_only_framed_controlled_errors_preserve_read_codes(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]], code: str
) -> None:
    children = worker_owner(_script(f"emit(b'VP1\\x01' + {code.encode()!r})\n"))
    error = _capture(_read)
    assert type(error) is VersionPageReadError
    assert error.code == code
    assert str(error) == "checkpoint version page read failed"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    _assert_reaped(children)


@pytest.mark.parametrize("over", [False, True])
def test_binary_result_exact_body_boundary(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]], over: bool
) -> None:
    size = 16_777_217 if over else 16_777_216
    tail = f"body = {BODY!r}\nbody += b' ' * ({size} - len(body))\nemit(b'VP1\\x00' + body)\n"
    children = worker_owner(_script(tail))
    if over:
        _assert_isolated(_capture(_read), "OUTPUT_LIMIT")
    else:
        result = _read()
        assert len(result.body) == 16_777_216
        assert result.body[: len(BODY)] == BODY
        assert result.page.versions[0].version_id == "opaque%2F+/v1"
    _assert_reaped(children)


@pytest.mark.parametrize("over", [False, True])
def test_actual_worker_original_body_boundary(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]], over: bool
) -> None:
    children = worker_owner(None)
    size = 16_777_217 if over else 16_777_216
    body = BODY + b" " * (size - len(BODY))
    with _response_server(body) as (endpoint, server):
        if over:
            error = _capture(
                lambda: isolated.read_raw_checkpoint_version_page_isolated(_reader(endpoint), ORG)
            )
            assert type(error) is VersionPageReadError and error.code == "BODY_LIMIT"
        else:
            result = isolated.read_raw_checkpoint_version_page_isolated(_reader(endpoint), ORG)
            assert result.body == body
        assert server.requests == [EXPECTED_TARGET]
    _assert_reaped(children)


@pytest.mark.parametrize("status", [301, 403])
def test_actual_worker_non_200_has_one_request_and_fixed_error(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]], status: int
) -> None:
    children = worker_owner(None)
    with _response_server(b"synthetic-private-provider-text", status=status) as (endpoint, server):
        error = _capture(
            lambda: isolated.read_raw_checkpoint_version_page_isolated(_reader(endpoint), ORG)
        )
    assert type(error) is VersionPageReadError and error.code == "PROVIDER_FAILURE"
    assert "synthetic-private-provider-text" not in str(error)
    assert server.requests == [EXPECTED_TARGET]
    _assert_reaped(children)


@pytest.mark.parametrize("version_marker", [None, "null", "opaque%+/雪\t"])
def test_actual_worker_preserves_opaque_cursor_and_duplicate_observations(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    version_marker: str | None,
) -> None:
    children = worker_owner(None)
    key_marker = f"checkpoints/{ORG}/literal%+/雪\x01"
    encoded = quote(key_marker, safe="-_.~")
    cursor = b"<KeyMarker>" + encoded.encode() + b"</KeyMarker>"
    if version_marker is not None:
        cursor += b"<VersionIdMarker>" + version_marker.encode() + b"</VersionIdMarker>"
    body = BODY.replace(b"<MaxKeys>", cursor + b"<MaxKeys>")
    entry = BODY[BODY.index(b"<Version>") : BODY.index(b"</Version>") + len(b"</Version>")]
    marker = entry.replace(b"Version>", b"DeleteMarker>").replace(
        b"<IsLatest>true", b"<IsLatest>false"
    )
    body = body.replace(b"</ListVersionsResult>", entry + marker + b"</ListVersionsResult>")
    with _response_server(body) as (endpoint, server):
        result = isolated.read_raw_checkpoint_version_page_isolated(
            _reader(endpoint), ORG, key_marker=key_marker, version_id_marker=version_marker
        )
    assert result.body == body
    assert len(result.page.versions) == 2
    assert result.page.versions[0] == result.page.versions[1]
    assert len(result.page.delete_markers) == 1
    target = EXPECTED_TARGET + "&key-marker=" + encoded
    if version_marker is not None:
        target += "&version-id-marker=" + quote(version_marker, safe="-_.~")
    assert server.requests == [target]
    _assert_reaped(children)


def test_parent_redecodes_success_with_its_own_cursor(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_success_script())
    _assert_isolated(
        _capture(lambda: _read(key_marker=f"checkpoints/{ORG}/missing-echo")), "PROTOCOL_INVALID"
    )
    _assert_reaped(children)


@pytest.mark.parametrize(
    "suffix,code",
    [
        ("time.sleep(5)\n", "DEADLINE_EXCEEDED"),
        ("os.close(1)\ntime.sleep(5)\n", "DEADLINE_EXCEEDED"),
        ("raise SystemExit(7)\n", "WORKER_FAILED"),
        ("time.sleep(0.05)\nos.write(1, b'x')\n", "PROTOCOL_INVALID"),
    ],
    ids=["held-eof", "held-exit", "nonzero-exit", "late-trailing-byte"],
)
def test_result_alone_never_publishes_before_eof_and_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    suffix: str,
    code: str,
) -> None:
    monkeypatch.setattr(isolated, "_DEADLINE_SECONDS", 0.5)
    children = worker_owner(_success_script(suffix=suffix))
    start = time.monotonic()
    _assert_isolated(_capture(_read), code)
    assert time.monotonic() - start < 3
    _assert_reaped(children)


def test_early_result_before_full_request_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    # Keep the request incomplete regardless of scheduler timing, while the
    # independent worker emits a result without consuming any request bytes.
    real_write = isolated._write_fd
    sent = [False]

    def partial(fd: int, chunk: bytes) -> int:
        if sent[0]:
            raise BlockingIOError()
        sent[0] = True
        return real_write(fd, chunk[:1])

    monkeypatch.setattr(isolated, "_write_fd", partial)
    children = worker_owner(
        _script(f"time.sleep(0.05)\nemit(b'VP1\\x00' + {BODY!r})\n", consume=False)
    )
    _assert_isolated(_capture(_read), "PROTOCOL_INVALID")
    _assert_reaped(children)


@pytest.mark.parametrize("written", [0, None, -1, 100_000, True])
def test_invalid_write_progress_is_rejected_immediately(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    written: Any,
) -> None:
    children = worker_owner(_script("time.sleep(5)\n", consume=False))
    monkeypatch.setattr(isolated, "_write_fd", lambda *_args: written)
    start = time.monotonic()
    _assert_isolated(_capture(_read), "PROTOCOL_INVALID")
    assert time.monotonic() - start < 3
    _assert_reaped(children)


@pytest.mark.parametrize("boundary", ["read", "write"])
def test_transient_nonblocking_io_can_resume(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    boundary: str,
) -> None:
    children = worker_owner(_success_script())
    name = "_read_fd" if boundary == "read" else "_write_fd"
    original = getattr(isolated, name)
    attempts = [0]

    def resume(*args: Any) -> Any:
        attempts[0] += 1
        if attempts[0] == 1:
            raise BlockingIOError()
        return original(*args)

    monkeypatch.setattr(isolated, name, resume)
    assert _read().body == BODY
    assert attempts[0] > 1
    _assert_reaped(children)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"org_id": str(ORG)},
        {"org_id": True},
        {"key_marker": True},
        {"key_marker": "outside-prefix"},
        {"version_id_marker": "version-without-key"},
        {"key_marker": f"checkpoints/{ORG}/" + "x" * 1024},
        {"cancel": object()},
        {"reader": ExplicitHistoryReader("http://127.0.0.1:9", "synthetic-audit", "", "a", "s")},
    ],
)
def test_bad_inputs_are_rejected_before_spawn(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    def forbid() -> Any:
        raise AssertionError("invalid input spawned a process")

    monkeypatch.setattr(isolated, "_spawn_worker", forbid)
    arguments = {"reader": _reader(), "org_id": ORG, **kwargs}
    error = _capture(lambda: isolated.read_raw_checkpoint_version_page_isolated(**arguments))
    assert type(error) is VersionPageInputError


def test_preexisting_cancellation_spawns_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid() -> Any:
        raise AssertionError("cancelled operation spawned a process")

    monkeypatch.setattr(isolated, "_spawn_worker", forbid)
    cancel = threading.Event()
    cancel.set()
    assert type(_capture(lambda: _read(cancel=cancel))) is VersionPageReadCancelled


def test_unsupported_runtime_and_spawn_oserror_are_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(isolated, "_runtime_supported", lambda: False)
    _assert_isolated(_capture(_read), "RUNTIME_UNSUPPORTED")
    monkeypatch.setattr(isolated, "_runtime_supported", lambda: True)

    def fail() -> Any:
        raise OSError("synthetic-secret-spawn-detail")

    monkeypatch.setattr(isolated, "_spawn_worker", fail)
    error = _capture(_read)
    _assert_isolated(error, "WORKER_START_FAILED")
    assert "synthetic-secret" not in str(error)


@pytest.mark.parametrize("phase", ["spawn", "write", "read", "result", "cleanup"])
def test_cancellation_at_each_owned_phase_prevents_publication(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    phase: str,
) -> None:
    children = worker_owner(_success_script())
    cancel = threading.Event()
    if phase == "spawn":
        original = isolated._spawn_worker

        def spawn() -> Any:
            process = original()
            cancel.set()
            return process

        monkeypatch.setattr(isolated, "_spawn_worker", spawn)
    elif phase == "write":
        original_write = isolated._write_fd

        def write(fd: int, chunk: bytes) -> int:
            result = original_write(fd, chunk)
            cancel.set()
            return result

        monkeypatch.setattr(isolated, "_write_fd", write)
    elif phase == "read":
        original_read = isolated._read_fd
        reads = [0]

        def read(fd: int, size: int) -> bytes:
            chunk = original_read(fd, size)
            reads[0] += len(chunk)
            if reads[0] > len(_frame(_json(READY))):
                cancel.set()
            return chunk

        monkeypatch.setattr(isolated, "_read_fd", read)
    else:
        name = "_decode_result" if phase == "result" else "_cleanup_worker"
        delegate = getattr(isolated, name)

        def finish(*args: Any, **kwargs: Any) -> Any:
            result = delegate(*args, **kwargs)
            cancel.set()
            return result

        monkeypatch.setattr(isolated, name, finish)
    error = _capture(lambda: _read(cancel=cancel))
    assert type(error) is VersionPageReadCancelled
    assert str(error) == "checkpoint version page read cancelled"
    _assert_reaped(children)


@pytest.mark.parametrize("phase", ["spawn", "result", "cleanup"])
def test_deadline_expiring_at_owned_boundaries_prevents_publication(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    phase: str,
) -> None:
    children = worker_owner(_success_script())
    now = [0.0]
    monkeypatch.setattr(isolated, "_monotonic", lambda: now[0])
    name = {
        "spawn": "_spawn_worker",
        "result": "_decode_result",
        "cleanup": "_cleanup_worker",
    }[phase]
    delegate = getattr(isolated, name)

    def expire(*args: Any, **kwargs: Any) -> Any:
        outcome = delegate(*args, **kwargs)
        now[0] = 30.0
        return outcome

    monkeypatch.setattr(isolated, name, expire)
    _assert_isolated(_capture(_read), "DEADLINE_EXCEEDED")
    _assert_reaped(children)


class _SelectorWrapper:
    def __init__(self, delegate: Any, *, close_error: BaseException | None = None) -> None:
        self.delegate = delegate
        self.close_error = close_error
        self.closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def close(self) -> None:
        self.delegate.close()
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _selector_close_fault(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> list[_SelectorWrapper]:
    factory = isolated.selectors.DefaultSelector
    wrappers: list[_SelectorWrapper] = []

    def create() -> Any:
        wrapper = _SelectorWrapper(factory(), close_error=error)
        wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(isolated.selectors, "DefaultSelector", create)
    return wrappers


@pytest.mark.parametrize("stage", ["spawn", "selector", "register", "select", "read", "write"])
@pytest.mark.parametrize(
    "fault",
    [
        KeyboardInterrupt("synthetic-fatal"),
        MemoryError("synthetic-memory"),
        RuntimeError("synthetic-runtime"),
    ],
)
def test_unexpected_operation_fault_identity_survives_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    stage: str,
    fault: BaseException,
) -> None:
    children = worker_owner(_script("time.sleep(0.2)\n", consume=False))

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise fault

    if stage in {"spawn", "read", "write"}:
        name = {"spawn": "_spawn_worker", "read": "_read_fd", "write": "_write_fd"}[stage]
        monkeypatch.setattr(isolated, name, fail)
    elif stage == "selector":
        monkeypatch.setattr(isolated.selectors, "DefaultSelector", fail)
    else:
        factory = isolated.selectors.DefaultSelector

        def create() -> Any:
            wrapper = _SelectorWrapper(factory())
            setattr(wrapper, stage, fail)
            return wrapper

        monkeypatch.setattr(isolated.selectors, "DefaultSelector", create)
    assert _capture(_read) is fault
    if stage == "spawn":
        assert children == []
    else:
        _assert_reaped(children)


@pytest.mark.parametrize(
    "fault",
    [
        OSError("synthetic-close"),
        SystemExit(71),
        ExceptionGroup("synthetic-group", [RuntimeError("synthetic-leaf")]),
    ],
)
def test_selector_cleanup_fault_retains_identity_or_fixed_oserror(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    fault: BaseException,
) -> None:
    children = worker_owner(_success_script())
    wrappers = _selector_close_fault(monkeypatch, fault)
    error = _capture(_read)
    if isinstance(fault, OSError):
        _assert_isolated(error, "CLEANUP_FAILED")
    else:
        assert error is fault
    assert wrappers[0].closed
    _assert_reaped(children)


class _StreamWrapper:
    def __init__(self, delegate: Any, fault: BaseException) -> None:
        self.delegate = delegate
        self.fault = fault
        self.close_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def close(self) -> None:
        self.close_calls += 1
        self.delegate.close()
        raise self.fault


@pytest.mark.parametrize("stream_name", ["stdin", "stdout"])
def test_each_pipe_close_failure_prevents_success_and_closes_other_pipe(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    stream_name: str,
) -> None:
    children = worker_owner(_success_script())
    original_spawn = isolated._spawn_worker
    fault = KeyboardInterrupt("synthetic-pipe-close")
    wrappers: list[_StreamWrapper] = []

    def spawn() -> Any:
        process = original_spawn()
        wrapper = _StreamWrapper(getattr(process, stream_name), fault)
        setattr(process, stream_name, wrapper)
        wrappers.append(wrapper)
        return process

    monkeypatch.setattr(isolated, "_spawn_worker", spawn)
    assert _capture(_read) is fault
    assert wrappers[0].close_calls == 1
    _assert_reaped(children)


def test_broken_request_pipe_is_a_fixed_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_script("time.sleep(0.2)\n", consume=False))

    def broken(_fd: int, _payload: bytes) -> int:
        raise BrokenPipeError("synthetic-private-write-detail")

    monkeypatch.setattr(isolated, "_write_fd", broken)
    _assert_isolated(_capture(_read), "WORKER_FAILED")
    _assert_reaped(children)


def test_primary_cancellation_and_cleanup_faults_are_all_preserved(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_script("time.sleep(0.2)\n", consume=False))
    primary = RuntimeError("synthetic-operation")
    cleanup = SystemExit("synthetic-cleanup")
    cancel = threading.Event()
    wrappers = _selector_close_fault(monkeypatch, cleanup)

    def fail(_fd: int, _chunk: bytes) -> int:
        cancel.set()
        raise primary

    monkeypatch.setattr(isolated, "_write_fd", fail)
    error = _capture(lambda: _read(cancel=cancel))
    assert type(error) is BaseExceptionGroup
    assert error.message == "isolated checkpoint version page operation and cleanup failed"
    assert error.exceptions[0] is primary
    assert type(error.exceptions[1]) is VersionPageReadCancelled
    assert error.exceptions[2] is cleanup
    assert wrappers[0].closed
    _assert_reaped(children)


def test_cancellation_observed_before_cleanup_survives_clearing_during_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_script("emit(b'VP1\\x01PROVIDER_FAILURE')\n"))
    cancel = threading.Event()
    decode = isolated._decode_result
    cleanup = isolated._cleanup_worker

    def decoded(*args: Any, **kwargs: Any) -> Any:
        result = decode(*args, **kwargs)
        cancel.set()
        return result

    def cleared(*args: Any, **kwargs: Any) -> Any:
        cancel.clear()
        return cleanup(*args, **kwargs)

    monkeypatch.setattr(isolated, "_decode_result", decoded)
    monkeypatch.setattr(isolated, "_cleanup_worker", cleared)
    assert type(_capture(lambda: _read(cancel=cancel))) is VersionPageReadCancelled
    _assert_reaped(children)


def test_unexpected_final_checkpoint_fault_does_not_erase_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_success_script())
    checkpoint_fault = KeyboardInterrupt("synthetic-checkpoint")
    cleanup_fault = SystemExit("synthetic-cleanup")
    cleanup = isolated._cleanup_worker
    checkpoint = isolated._checkpoint
    cleaned = [False]
    _selector_close_fault(monkeypatch, cleanup_fault)

    def finish(*args: Any, **kwargs: Any) -> Any:
        outcomes = cleanup(*args, **kwargs)
        cleaned[0] = True
        return outcomes

    def final_check(*args: Any, **kwargs: Any) -> None:
        if cleaned[0]:
            raise checkpoint_fault
        checkpoint(*args, **kwargs)

    monkeypatch.setattr(isolated, "_cleanup_worker", finish)
    monkeypatch.setattr(isolated, "_checkpoint", final_check)
    error = _capture(_read)
    assert isinstance(error, BaseExceptionGroup)
    assert error.exceptions == (cleanup_fault, checkpoint_fault)
    _assert_reaped(children)


def test_reaped_pid_is_never_signalled_even_after_fatal_decode(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_success_script())
    fault = RuntimeError("synthetic-post-reap")
    signals: list[tuple[int, int]] = []

    def fail(*_args: Any) -> Any:
        assert children[0].returncode == 0
        raise fault

    monkeypatch.setattr(isolated, "_decode_result", fail)
    monkeypatch.setattr(isolated, "_killpg", lambda pid, sig: signals.append((pid, sig)))
    assert _capture(_read) is fault
    assert signals == []
    _assert_reaped(children)


@pytest.mark.parametrize("fault_kind", ["timeout", "unexpected", "oserror"])
def test_unconfirmed_reap_cannot_publish_and_preserves_cleanup_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    fault_kind: str,
) -> None:
    children = worker_owner(_script("time.sleep(5)\n", ready=b"{}", consume=False))
    original_spawn = isolated._spawn_worker
    original_wait: list[Any] = []
    fault: BaseException = {
        "timeout": subprocess.TimeoutExpired("synthetic-worker", 2),
        "unexpected": KeyboardInterrupt("synthetic-wait"),
        "oserror": OSError("synthetic-wait"),
    }[fault_kind]

    def spawn() -> Any:
        process = original_spawn()
        original_wait.append(process.wait)

        def fail_wait(*_args: Any, **_kwargs: Any) -> Any:
            raise fault

        monkeypatch.setattr(process, "wait", fail_wait)
        return process

    monkeypatch.setattr(isolated, "_spawn_worker", spawn)
    start = time.monotonic()
    error = _capture(_read)
    assert isinstance(error, BaseExceptionGroup)
    assert any(
        isinstance(item, isolated.IsolatedVersionPageError) and item.code == "CLEANUP_FAILED"
        for item in error.exceptions
    )
    if fault_kind == "unexpected":
        assert any(item is fault for item in error.exceptions)
    assert children[0].returncode is None
    assert children[0].stdin is not None and children[0].stdin.closed
    assert children[0].stdout is not None and children[0].stdout.closed
    # The failed wait leaves ownership intact; the test owner performs a measured
    # rescue wait without releasing or reusing this PID before that wait.
    original_wait[0](timeout=2)
    assert time.monotonic() - start < 3
    _assert_reaped(children)


def test_signal_fault_still_attempts_reap_and_pipe_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_script("time.sleep(0.1)\n", ready=b"{}", consume=False))
    fault = KeyboardInterrupt("synthetic-kill")

    def fail(_pid: int, _sig: int) -> None:
        raise fault

    monkeypatch.setattr(isolated, "_killpg", fail)
    error = _capture(_read)
    assert isinstance(error, BaseExceptionGroup)
    assert error.exceptions[1] is fault
    _assert_reaped(children)


def _read_worker_ready(process: subprocess.Popen[bytes]) -> bytes:
    assert process.stdout is not None
    selected = selectors.DefaultSelector()
    output = bytearray()
    deadline = time.monotonic() + 5
    try:
        selected.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            for _key, _events in selected.select(timeout=0.05):
                chunk = os.read(process.stdout.fileno(), 8192)
                assert chunk, "worker exited before READY"
                output.extend(chunk)
                assert len(output) <= 516
                if len(output) >= 4:
                    declared = int.from_bytes(output[:4], "big")
                    assert declared <= 512
                    if len(output) == 4 + declared:
                        return bytes(output[4:])
        raise AssertionError("worker READY deadline elapsed")
    finally:
        selected.close()


def test_actual_worker_limits_command_and_clean_environment_before_credentials(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-hostile-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-hostile-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic.invalid")
    children = worker_owner(None)
    process = isolated._spawn_worker()
    start = time.monotonic()
    try:
        assert json.loads(_read_worker_ready(process)) == READY
        proc = Path(f"/proc/{process.pid}")
        argv = (proc / "cmdline").read_bytes().split(b"\0")[:-1]
        environment = set((proc / "environ").read_bytes().split(b"\0")) - {b""}
        assert argv[:4] == [os.fsencode(sys.executable), b"-I", b"-B", b"-u"]
        expected_worker = Path(isolated.__file__).with_name("_isolated_version_page_worker.py")
        assert argv[4] == os.fsencode(expected_worker.resolve())
        assert argv[5] == os.fsencode(Path(isolated.__file__).resolve().parents[3])
        assert len(argv) == 6
        assert environment == {b"LANG=C.UTF-8", b"TZ=UTC"}
        limits = (proc / "limits").read_text()
        for label, expected in [
            ("Max address space", ["536870912", "536870912", "bytes"]),
            ("Max cpu time", ["20", "20", "seconds"]),
            ("Max open files", ["64", "64", "files"]),
            ("Max file size", ["0", "0", "bytes"]),
            ("Max core file size", ["0", "0", "bytes"]),
        ]:
            line = next(line for line in limits.splitlines() if line.startswith(label))
            assert line[len(label) :].split() == expected
        assert process.returncode is None
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=2)
        assert process.stdout is not None
        assert process.stdout.read() == b""
        process.stdout.close()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
    assert process.returncode == 1
    assert time.monotonic() - start < 7
    _assert_reaped(children)


_LIMIT_PREAMBLE = """
import importlib.util
import json
import os
import resource
import signal
import sys
import time
signal.alarm(35)
spec = importlib.util.spec_from_file_location('owned_limit_probe', sys.argv[1])
assert spec is not None and spec.loader is not None
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
assert not any(name == 'easysynq_api' or name.startswith('easysynq_api.') for name in sys.modules)
assert not any(name == 'botocore' or name.startswith('botocore.') for name in sys.modules)
"""


def _limit_probe(body: str, *arguments: str, timeout: float = 5) -> tuple[bytes, int, float]:
    worker = Path(isolated.__file__).with_name("_isolated_version_page_worker.py").resolve()
    start = time.monotonic()
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter, finite probe, owned arguments
        [sys.executable, "-I", "-B", "-u", "-c", _LIMIT_PREAMBLE + body, str(worker), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        start_new_session=True,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    elapsed = time.monotonic() - start
    assert elapsed < timeout + 2
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is not None and process.stderr.closed
    assert process.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    assert stderr == b""
    return stdout, process.returncode, elapsed


def test_actual_limits_apply_and_verify_before_application_imports() -> None:
    body = """
worker._apply_limits()
print(json.dumps([
    resource.getrlimit(resource.RLIMIT_AS),
    resource.getrlimit(resource.RLIMIT_CPU),
    resource.getrlimit(resource.RLIMIT_NOFILE),
    resource.getrlimit(resource.RLIMIT_FSIZE),
    resource.getrlimit(resource.RLIMIT_CORE),
]))
"""
    stdout, status, elapsed = _limit_probe(body)
    assert status == 0
    assert json.loads(stdout) == [
        [536_870_912, 536_870_912],
        [20, 20],
        [64, 64],
        [0, 0],
        [0, 0],
    ]
    assert elapsed < 5


@pytest.mark.parametrize("phase", ["apply", "verify"])
def test_limit_failure_emits_no_ready_and_imports_no_application(phase: str) -> None:
    patch = (
        "def fail(*args):\n    raise OSError('synthetic-limit')\nresource.setrlimit = fail\n"
        if phase == "apply"
        else "resource.getrlimit = lambda *args: (1, 1)\n"
    )
    body = (
        patch
        + """
status = worker._main()
assert status == 1
assert not any(name == 'easysynq_api' or name.startswith('easysynq_api.') for name in sys.modules)
assert not any(name == 'botocore' or name.startswith('botocore.') for name in sys.modules)
"""
    )
    stdout, status, _elapsed = _limit_probe(body)
    assert stdout == b""
    assert status == 0


def test_actual_address_space_limit_blocks_over_ceiling_allocation() -> None:
    body = """
worker._apply_limits()
try:
    bytearray(600_000_000)
except MemoryError:
    print('MEMORY_ERROR')
else:
    raise SystemExit(31)
"""
    stdout, status, elapsed = _limit_probe(body)
    assert (stdout, status) == (b"MEMORY_ERROR\n", 0)
    assert elapsed < 5


def test_actual_descriptor_limit_blocks_the_65th_descriptor() -> None:
    body = """
worker._apply_limits()
owned = []
try:
    for _ in range(65):
        owned.append(os.open('/dev/null', os.O_RDONLY))
except OSError as error:
    print(json.dumps({'errno': error.errno, 'highest': max(owned), 'opened': len(owned)}))
else:
    raise SystemExit(32)
finally:
    for fd in owned:
        os.close(fd)
"""
    stdout, status, elapsed = _limit_probe(body)
    assert status == 0
    assert json.loads(stdout) == {"errno": errno.EMFILE, "highest": 63, "opened": 61}
    assert elapsed < 5


def test_actual_regular_file_limit_prevents_a_single_byte(tmp_path: Path) -> None:
    target = tmp_path / "synthetic-limit-probe"
    body = """
worker._apply_limits()
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
try:
    with open(sys.argv[2], 'wb', buffering=0) as output:
        output.write(b'x')
except OSError as error:
    print(json.dumps({'errno': error.errno, 'size': os.path.getsize(sys.argv[2])}))
else:
    raise SystemExit(33)
"""
    stdout, status, elapsed = _limit_probe(body, str(target))
    assert status == 0
    assert json.loads(stdout) == {"errno": errno.EFBIG, "size": 0}
    assert target.stat().st_size == 0
    assert elapsed < 5


def test_actual_cpu_limit_terminates_a_finite_computation() -> None:
    body = """
worker._apply_limits()
print('CPU_READY', flush=True)
value = 0
for _ in range(1_000_000_000_000):
    value = (value + 1) % 1000003
raise SystemExit(34)
"""
    stdout, status, elapsed = _limit_probe(body, timeout=32)
    assert stdout == b"CPU_READY\n"
    assert status in {-signal.SIGXCPU, -signal.SIGKILL}
    assert 15 < elapsed < 32


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_actual_worker_blocked_network_has_bounded_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
    stop: str,
) -> None:
    children = worker_owner(None)
    cancel = threading.Event()
    if stop == "deadline":
        monkeypatch.setattr(isolated, "_DEADLINE_SECONDS", 3.0)
    with _response_server(BODY, mode="blocked") as (endpoint, server):

        def cancel_request() -> None:
            if server.received.wait(timeout=5):
                cancel.set()

        setter = threading.Thread(target=cancel_request)
        if stop == "cancel":
            setter.start()
        start = time.monotonic()
        try:
            error = _capture(
                lambda: isolated.read_raw_checkpoint_version_page_isolated(
                    _reader(endpoint), ORG, cancel=cancel
                )
            )
        finally:
            if stop == "cancel":
                setter.join(timeout=5)
                assert not setter.is_alive()
        assert server.received.is_set()
        assert time.monotonic() - start < 6
        if stop == "cancel":
            assert type(error) is VersionPageReadCancelled
        else:
            _assert_isolated(error, "DEADLINE_EXCEEDED")
        _assert_reaped(children)


def test_actual_worker_upstream_buffering_is_contained_by_memory_limit(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(None)
    with _response_server(b"", mode="memory") as (endpoint, server):
        start = time.monotonic()
        error = _capture(
            lambda: isolated.read_raw_checkpoint_version_page_isolated(_reader(endpoint), ORG)
        )
        assert server.received.is_set()
        _assert_isolated(error, "WORKER_FAILED")
        assert time.monotonic() - start < 15
        _assert_reaped(children)


@pytest.mark.parametrize("extra", [0, 1])
def test_actual_worker_combined_observation_boundary_preserves_every_duplicate(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]], extra: int
) -> None:
    children = worker_owner(None)
    entry = (
        b"<Version><Key>checkpoints%2F11111111-1111-4111-8111-111111111111%2Fsame</Key>"
        b"<VersionId>null</VersionId><IsLatest>false</IsLatest></Version>"
    )
    marker = entry.replace(b"Version>", b"DeleteMarker>")
    body = BODY[: BODY.index(b"<Version>")] + entry * 500 + marker * (500 + extra)
    body += b"</ListVersionsResult>"
    with _response_server(body) as (endpoint, server):
        if extra:
            error = _capture(
                lambda: isolated.read_raw_checkpoint_version_page_isolated(_reader(endpoint), ORG)
            )
            assert type(error) is VersionPageReadError and error.code == "PAGE_LIMIT"
        else:
            result = isolated.read_raw_checkpoint_version_page_isolated(_reader(endpoint), ORG)
            assert result.body == body
            assert len(result.page.versions) == len(result.page.delete_markers) == 500
            assert all(item.version_id == "null" for item in result.page.versions)
            assert all(item.version_id == "null" for item in result.page.delete_markers)
        assert server.requests == [EXPECTED_TARGET]
    _assert_reaped(children)


@pytest.mark.parametrize(
    "body",
    [BODY[:-1], BODY.replace(b"<IsTruncated>false", b"<IsTruncated>False")],
)
def test_actual_worker_original_malformed_200_uses_controlled_decoder_error(
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]], body: bytes
) -> None:
    children = worker_owner(None)
    with _response_server(body) as (endpoint, server):
        error = _capture(
            lambda: isolated.read_raw_checkpoint_version_page_isolated(_reader(endpoint), ORG)
        )
    assert type(error) is VersionPageReadError and error.code == "RESPONSE_INVALID"
    assert server.requests == [EXPECTED_TARGET]
    _assert_reaped(children)


def test_maximum_unicode_request_is_chunked_bounded_and_preserved(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    scalar = "\U0001f642"
    reader = ExplicitHistoryReader(
        "http://127.0.0.1:9", "synthetic-audit", "us-east-1", scalar * 4096, scalar * 4096
    )
    tail = (
        "assert request['access_key'] == '\\U0001f642' * 4096\n"
        "assert request['secret_key'] == '\\U0001f642' * 4096\n"
        "assert request['key_marker'] is None and request['version_id_marker'] is None\n"
        + f"emit(b'VP1\\x00' + {BODY!r})\n"
    )
    children = worker_owner(_script(tail))
    writes: list[int] = []
    original = isolated._write_fd

    def record(fd: int, payload: bytes) -> int:
        written = original(fd, payload)
        writes.append(written)
        assert len(payload) <= 8192
        return written

    monkeypatch.setattr(isolated, "_write_fd", record)
    result = isolated.read_raw_checkpoint_version_page_isolated(reader, ORG)
    assert result.body == BODY
    assert 98_304 < sum(writes) <= 131_076
    assert len(writes) > 1
    _assert_reaped(children)


def test_actual_worker_waits_for_request_eof_before_network_io(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(None)
    monkeypatch.setattr(isolated, "_DEADLINE_SECONDS", 3.0)

    def hold(selected: Any, _process: Any, stdin_fd: int) -> None:
        selected.unregister(stdin_fd)

    monkeypatch.setattr(isolated, "_close_stdin", hold)
    with _response_server(BODY) as (endpoint, server):
        error = _capture(
            lambda: isolated.read_raw_checkpoint_version_page_isolated(_reader(endpoint), ORG)
        )
    _assert_isolated(error, "DEADLINE_EXCEEDED")
    assert server.requests == []
    _assert_reaped(children)


def test_exited_but_unreaped_child_is_still_owned_until_wait(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_FIXTURE_PREAMBLE + "raise SystemExit(0)\n")
    order: list[str] = []
    fault = RuntimeError("synthetic-before-reap")
    original_spawn = isolated._spawn_worker
    original_kill = isolated._killpg

    def spawn() -> Any:
        process = original_spawn()
        original_wait = process.wait

        def wait(*args: Any, **kwargs: Any) -> Any:
            order.append("wait")
            return original_wait(*args, **kwargs)

        monkeypatch.setattr(process, "wait", wait)
        return process

    def exited(*_args: Any) -> Any:
        process = children[0]
        deadline = time.monotonic() + 2
        pause = threading.Event()
        while time.monotonic() < deadline:
            status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            if status is not None:
                assert process.returncode is None
                order.append("exited-unreaped")
                raise fault
            pause.wait(0.01)
        raise AssertionError("fixture did not exit")

    def kill(pid: int, sig: int) -> None:
        assert children[0].returncode is None
        order.append("signal")
        original_kill(pid, sig)

    monkeypatch.setattr(isolated, "_spawn_worker", spawn)
    monkeypatch.setattr(isolated, "_run_protocol", exited)
    monkeypatch.setattr(isolated, "_killpg", kill)
    assert _capture(_read) is fault
    assert order == ["exited-unreaped", "signal", "wait"]
    _assert_reaped(children)


def test_polling_never_exceeds_the_cancellation_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    worker_owner: Callable[[str | None], list[subprocess.Popen[bytes]]],
) -> None:
    children = worker_owner(_script("time.sleep(5)\n"))
    monkeypatch.setattr(isolated, "_DEADLINE_SECONDS", 0.3)
    factory = isolated.selectors.DefaultSelector
    timeouts: list[float] = []

    class TimedSelector(_SelectorWrapper):
        def select(self, timeout: float) -> Any:
            timeouts.append(timeout)
            return self.delegate.select(timeout)

    monkeypatch.setattr(isolated.selectors, "DefaultSelector", lambda: TimedSelector(factory()))
    _assert_isolated(_capture(_read), "DEADLINE_EXCEEDED")
    assert len(timeouts) >= 3
    assert all(0 <= timeout <= 0.05 for timeout in timeouts)
    _assert_reaped(children)
