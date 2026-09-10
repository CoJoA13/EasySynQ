from __future__ import annotations

import base64
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

import pytest

from easysynq_api.services.audit import isolated_raw, raw_transport
from easysynq_api.services.audit.raw_transport import (
    RawCheckpointVersion,
    RawVersionInputError,
    RawVersionReadCancelled,
    RawVersionReadError,
)
from easysynq_api.services.audit.sink import CheckpointVersionRef, ExplicitHistoryReader

pytestmark = pytest.mark.unit

_EXPECTED_TARGET = "/synthetic-audit/checkpoints/synthetic.json?versionId=retained%2Bversion%2F1"
_BODY = b'{  "synthetic": true, "n": 1 }\n'
_VERSION = "retained+version/1"
_READY = {
    "version": 1,
    "status": "ready",
    "address_space": 536_870_912,
    "cpu_seconds": 10,
    "file_descriptors": 64,
    "file_bytes": 0,
    "core_bytes": 0,
}


class _ResponseServer(ThreadingHTTPServer):
    expected_target: str
    body: bytes
    version_id: str
    status: int


class _ResponseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        server = cast(_ResponseServer, self.server)
        if self.path != server.expected_target:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(server.status)
        self.send_header("Content-Length", str(len(server.body)))
        self.send_header("x-amz-version-id", server.version_id)
        self.end_headers()
        self.wfile.write(server.body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


@contextmanager
def _response_server(
    body: bytes,
    *,
    key: str = "checkpoints/synthetic.json",
    version_id: str = _VERSION,
    status: int = 200,
) -> Iterator[tuple[str, CheckpointVersionRef]]:
    server = _ResponseServer(("127.0.0.1", 0), _ResponseHandler)
    server.expected_target = (
        f"/synthetic-audit/{quote(key, safe='/~')}?versionId={quote(version_id, safe='-_.~')}"
    )
    server.body = body
    server.version_id = version_id
    server.status = status
    owner_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="isolated-raw-response-server",
    )
    owner_thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        yield f"http://{host}:{port}", CheckpointVersionRef(key, version_id)
    finally:
        server.shutdown()
        server.server_close()
        owner_thread.join(timeout=3)
        assert not owner_thread.is_alive()


def _reader(endpoint: str = "http://127.0.0.1:9") -> ExplicitHistoryReader:
    return ExplicitHistoryReader(
        endpoint,
        "synthetic-audit",
        "us-east-1",
        "synthetic-reader",
        "synthetic-secret",
    )


def _capture(call: Callable[[], Any]) -> BaseException:
    try:
        call()
    except BaseException as error:  # noqa: BLE001 - fatal identity is part of this contract
        return error
    raise AssertionError("call returned instead of raising")


def _assert_isolated(error: BaseException, code: str) -> None:
    assert type(error) is isolated_raw.IsolatedRawReadError
    assert error.code == code
    assert str(error) == "isolated raw checkpoint read failed"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def _json_frame(value: dict[str, Any]) -> bytes:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    return len(payload).to_bytes(4, "big") + payload


_FIXTURE_PREAMBLE = """
import base64
import json
import os
import sys
import time

READY = {
    "version": 1,
    "status": "ready",
    "address_space": 536870912,
    "cpu_seconds": 10,
    "file_descriptors": 64,
    "file_bytes": 0,
    "core_bytes": 0,
}

def frame(value):
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    return len(payload).to_bytes(4, "big") + payload

def emit(value):
    sys.stdout.buffer.write(frame(value))
    sys.stdout.buffer.flush()

def consume_request():
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        raise SystemExit(11)
    remaining = int.from_bytes(header, "big")
    chunks = []
    while remaining:
        chunk = sys.stdin.buffer.read(min(4096, remaining))
        if not chunk:
            raise SystemExit(12)
        chunks.append(chunk)
        remaining -= len(chunk)
    if sys.stdin.buffer.read(1) != b"":
        raise SystemExit(13)
    return json.loads(b"".join(chunks))
"""


def _success_script(body: bytes = b"fixture-body", *, delay_after: float = 0.0) -> str:
    encoded = base64.b64encode(body).decode("ascii")
    return (
        _FIXTURE_PREAMBLE
        + "\nemit(READY)\n"
        + "request = consume_request()\n"
        + "emit({'version': 1, 'status': 'ok', 'key': request['key'], "
        + "'version_id': request['version_id'], 'body_base64': "
        + repr(encoded)
        + "})\n"
        + f"time.sleep({delay_after!r})\n"
    )


def _spawn_fixture(script: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed test interpreter and finite fixture source
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
    assert all(child.returncode is not None for child in children)
    assert all(child.stdin is not None and child.stdin.closed for child in children)
    assert all(child.stdout is not None and child.stdout.closed for child in children)


class _UnexpectedSelectorCloseError(Exception):
    pass


class _DelegatingSelectorCloseFailure:
    def __init__(self, delegate: Any, error: BaseException) -> None:
        self._delegate = delegate
        self._error = error
        self.delegate_closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def close(self) -> None:
        self._delegate.close()
        self.delegate_closed = True
        raise self._error


def _install_selector_close_failure(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> list[_DelegatingSelectorCloseFailure]:
    selector_factory = isolated_raw.selectors.DefaultSelector
    wrappers: list[_DelegatingSelectorCloseFailure] = []

    def create() -> _DelegatingSelectorCloseFailure:
        wrapper = _DelegatingSelectorCloseFailure(selector_factory(), error)
        wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(isolated_raw.selectors, "DefaultSelector", create)
    return wrappers


def _observe_exited_without_reaping(
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    pause = threading.Event()
    flags = os.WEXITED | os.WNOWAIT | os.WNOHANG
    while True:
        observed = os.waitid(os.P_PID, process.pid, flags)
        if observed is not None:
            assert observed.si_pid == process.pid
            assert process.returncode is None
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("child did not enter exited-but-unreaped state")
        pause.wait(min(0.01, remaining))


@pytest.fixture
def fixture_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], list[subprocess.Popen[bytes]]]]:
    children: list[subprocess.Popen[bytes]] = []

    def install(script: str) -> list[subprocess.Popen[bytes]]:
        def spawn() -> subprocess.Popen[bytes]:
            child = _spawn_fixture(script)
            children.append(child)
            return child

        monkeypatch.setattr(isolated_raw, "_spawn_worker", spawn)
        return children

    yield install
    for child in children:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=2)


class _RetainedVersionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        if self.path != _EXPECTED_TARGET:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(_BODY)))
        self.send_header("x-amz-version-id", _VERSION)
        self.end_headers()
        self.wfile.write(_BODY)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


@pytest.fixture
def retained_server() -> Iterator[tuple[str, bytes, CheckpointVersionRef]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RetainedVersionHandler)
    owner_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="isolated-raw-retained-server",
    )
    owner_thread.start()
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        yield (
            f"http://{host}:{port}",
            _BODY,
            CheckpointVersionRef("checkpoints/synthetic.json", _VERSION),
        )
    finally:
        server.shutdown()
        server.server_close()
        owner_thread.join(timeout=3)
        assert not owner_thread.is_alive()


def test_finite_response_fixture_preserves_exact_body(retained_server):
    from urllib.request import urlopen

    endpoint, body, ref = retained_server
    with urlopen(  # noqa: S310 - fixture endpoint is fixed to an owned HTTP loopback server
        endpoint + _EXPECTED_TARGET, timeout=3
    ) as response:
        assert response.status == 200
        assert response.headers["x-amz-version-id"] == ref.version_id
        assert response.read() == body


def test_isolated_read_returns_exact_retained_bytes(retained_server):
    from easysynq_api.services.audit.isolated_raw import (
        read_raw_checkpoint_version_isolated,
    )
    from easysynq_api.services.audit.sink import ExplicitHistoryReader

    endpoint, body, ref = retained_server
    reader = ExplicitHistoryReader(
        endpoint,
        "synthetic-audit",
        "us-east-1",
        "synthetic-reader",
        "synthetic-secret",
    )
    result = read_raw_checkpoint_version_isolated(reader, ref)
    assert (result.key, result.version_id, result.body) == (
        ref.key,
        ref.version_id,
        body,
    )


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"x",
        b'{\n  "z": 1, "a": "\\u0062"\n}\n',
        bytes(range(256)) * 256,
    ],
    ids=("empty", "short", "noncanonical-json", "maximum-65536"),
)
def test_real_worker_preserves_exact_bounded_bodies(body: bytes) -> None:
    with _response_server(body) as (endpoint, ref):
        result = isolated_raw.read_raw_checkpoint_version_isolated(_reader(endpoint), ref)
    assert (result.key, result.version_id, result.body) == (ref.key, ref.version_id, body)


def test_real_worker_preserves_opaque_key_and_version_labels() -> None:
    body = b"opaque-label-body\x00\xff"
    key = "checkpoints/opaque ~+ snowman-\u2603.json"
    version_id = "retained+opaque/version-1"
    with _response_server(body, key=key, version_id=version_id) as (endpoint, ref):
        result = isolated_raw.read_raw_checkpoint_version_isolated(_reader(endpoint), ref)
    assert (result.key, result.version_id, result.body) == (key, version_id, body)


def test_success_uses_exactly_one_owned_real_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[subprocess.Popen[bytes]] = []
    original_spawn = isolated_raw._spawn_worker

    def observed_spawn() -> subprocess.Popen[bytes]:
        process = original_spawn()
        processes.append(process)
        return process

    monkeypatch.setattr(isolated_raw, "_spawn_worker", observed_spawn)
    with _response_server(b"one-real-child") as (endpoint, ref):
        result = isolated_raw.read_raw_checkpoint_version_isolated(_reader(endpoint), ref)
    assert result.body == b"one-real-child"
    assert len(processes) == 1
    _assert_reaped(processes)


def test_real_worker_preserves_controlled_r79_error_code() -> None:
    with _response_server(b"", status=404) as (endpoint, ref):
        error = _capture(
            lambda: isolated_raw.read_raw_checkpoint_version_isolated(_reader(endpoint), ref)
        )
    assert type(error) is RawVersionReadError
    assert error.code == "PROVIDER_FAILURE"
    assert str(error) == "raw checkpoint version read failed: PROVIDER_FAILURE"
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_request_codec_admits_and_round_trips_the_measured_r79_unicode_domain() -> None:
    scalar = "\U0001f642"
    reader = ExplicitHistoryReader(
        "https://audit.example.test",
        "synthetic-audit",
        "x",
        scalar * 4_096,
        scalar * 4_096,
    )
    ref = CheckpointVersionRef(scalar * 256, scalar * 256)
    raw_transport._validate_inputs(reader, ref, None)
    payload = isolated_raw._request_payload(reader, ref)
    assert len(payload) == 104_598
    assert isolated_raw._decode_request(payload) == (reader, ref)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"version":1,"version":1,"status":"ok","key":"k","version_id":"v","body_base64":""}',
        b'{"version":1,"status":"ok","key":{"nested":{"too":"deep"}},'
        b'"version_id":"v","body_base64":""}',
        b'{"version":1,"status":"ok","key":"k","version_id":"v","body_base64":NaN}',
    ],
)
def test_result_json_rejects_duplicate_deep_and_nonfinite_values(payload: bytes) -> None:
    error = _capture(lambda: isolated_raw._decode_result(payload, CheckpointVersionRef("k", "v")))
    _assert_isolated(error, "PROTOCOL_INVALID")


@pytest.mark.parametrize(
    ("message", "ref"),
    [
        (
            {"version": True, "status": "ok", "key": "k", "version_id": "v", "body_base64": ""},
            CheckpointVersionRef("k", "v"),
        ),
        (
            {"version": 1, "status": "ok", "key": "wrong", "version_id": "v", "body_base64": ""},
            CheckpointVersionRef("k", "v"),
        ),
        (
            {"version": 1, "status": "ok", "key": "k", "version_id": "wrong", "body_base64": ""},
            CheckpointVersionRef("k", "v"),
        ),
        (
            {"version": 1, "status": "ok", "key": "k", "version_id": "v", "body_base64": "YQ"},
            CheckpointVersionRef("k", "v"),
        ),
        (
            {
                "version": 1,
                "status": "ok",
                "key": "k",
                "version_id": "v",
                "body_base64": base64.b64encode(b"x" * 65_537).decode("ascii"),
            },
            CheckpointVersionRef("k", "v"),
        ),
    ],
    ids=(
        "boolean-version",
        "key-mismatch",
        "version-mismatch",
        "noncanonical-base64",
        "body-over-limit",
    ),
)
def test_result_schema_rejects_wrong_types_identity_and_noncanonical_body(
    message: dict[str, Any],
    ref: CheckpointVersionRef,
) -> None:
    error = _capture(lambda: isolated_raw._decode_result(_json_frame(message)[4:], ref))
    _assert_isolated(error, "PROTOCOL_INVALID")


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"status": "not-ready"},
        {"address_space": 536_870_911},
        {"extra": 1},
    ],
)
def test_ready_schema_requires_exact_fields_types_and_limits(change: dict[str, Any]) -> None:
    message = dict(_READY)
    message.update(change)
    error = _capture(lambda: isolated_raw._decode_ready(_json_frame(message)[4:]))
    _assert_isolated(error, "PROTOCOL_INVALID")


def test_declared_frame_over_cap_is_output_limit() -> None:
    declared = (131_073).to_bytes(4, "big")
    error = _capture(lambda: isolated_raw._inspect_frame(bytearray(declared), 131_072))
    _assert_isolated(error, "OUTPUT_LIMIT")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"version":1,"version":1,"endpoint":"https://audit.example.test"}',
        b'{"version":1,"endpoint":{"nested":{"too":"deep"}}}',
        b'{"version":1,"endpoint":NaN}',
    ],
)
def test_request_json_rejects_duplicate_deep_and_nonfinite_values(payload: bytes) -> None:
    error = _capture(lambda: isolated_raw._decode_request(payload))
    assert type(error) is ValueError


@pytest.mark.parametrize("code", sorted(raw_transport._READ_CODES))
def test_real_child_reconstructs_each_existing_controlled_read_code(
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
    code: str,
) -> None:
    script = (
        _FIXTURE_PREAMBLE
        + "\nemit(READY)\nconsume_request()\n"
        + f"emit({{'version': 1, 'status': 'read-error', 'code': {code!r}}})\n"
    )
    children = fixture_worker(script)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    assert type(error) is RawVersionReadError
    assert error.code == code
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    _assert_reaped(children)


def test_real_child_that_closes_stdin_is_a_fixed_failure_and_is_reaped(
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    script = _FIXTURE_PREAMBLE + "\nos.close(0)\nemit(READY)\ntime.sleep(0.1)\n"
    children = fixture_worker(script)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    _assert_isolated(error, "WORKER_FAILED")
    _assert_reaped(children)


@pytest.mark.parametrize(
    ("script", "code"),
    [
        (
            "import sys, time\n"
            "sys.stdout.buffer.write((513).to_bytes(4, 'big'))\n"
            "sys.stdout.buffer.flush()\n"
            "time.sleep(5)\n",
            "OUTPUT_LIMIT",
        ),
        (
            _FIXTURE_PREAMBLE
            + "\nemit(READY)\nconsume_request()\n"
            + "sys.stdout.buffer.write((131073).to_bytes(4, 'big'))\n"
            + "sys.stdout.buffer.flush()\ntime.sleep(5)\n",
            "OUTPUT_LIMIT",
        ),
        (
            _FIXTURE_PREAMBLE
            + "\nemit(READY)\nrequest = consume_request()\n"
            + 'payload = b\'{"version":1,"version":1}\'\n'
            + "sys.stdout.buffer.write(len(payload).to_bytes(4, 'big') + payload)\n"
            + "sys.stdout.buffer.flush()\ntime.sleep(5)\n",
            "PROTOCOL_INVALID",
        ),
        (
            _FIXTURE_PREAMBLE
            + "\nemit(READY)\nrequest = consume_request()\n"
            + "emit({'version': 1, 'status': 'ok', 'key': 'wrong', "
            + "'version_id': request['version_id'], 'body_base64': ''})\n"
            + "time.sleep(5)\n",
            "PROTOCOL_INVALID",
        ),
        (
            _FIXTURE_PREAMBLE
            + "\nemit(READY)\nrequest = consume_request()\n"
            + "sys.stdout.buffer.write(frame({'version': 1, 'status': 'ok', "
            + "'key': request['key'], 'version_id': request['version_id'], "
            + "'body_base64': ''}) + b'x')\n"
            + "sys.stdout.buffer.flush()\ntime.sleep(5)\n",
            "PROTOCOL_INVALID",
        ),
        (
            _FIXTURE_PREAMBLE
            + "\nemit(READY)\nconsume_request()\n"
            + "sys.stdout.buffer.write(b'\\x00\\x00\\x00\\x05{}')\n"
            + "sys.stdout.buffer.flush()\n",
            "WORKER_FAILED",
        ),
        (_success_script() + "raise SystemExit(7)\n", "WORKER_FAILED"),
    ],
    ids=(
        "ready-frame-over-limit",
        "result-frame-over-limit",
        "duplicate-result-field",
        "result-identity-mismatch",
        "trailing-output",
        "truncated-result",
        "nonzero-after-result",
    ),
)
def test_real_fixture_workers_reject_bounded_protocol_and_exit_faults(
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
    script: str,
    code: str,
) -> None:
    children = fixture_worker(script)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    _assert_isolated(error, code)
    _assert_reaped(children)


@pytest.mark.parametrize(
    "script",
    [
        "import time\ntime.sleep(5)\n",
        _success_script(delay_after=5),
        _FIXTURE_PREAMBLE + "\nemit(READY)\ntime.sleep(5)\n",
    ],
    ids=("before-ready", "after-result", "blocked-stdin"),
)
def test_watchdog_rejects_startup_result_and_blocked_stdin_hangs(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
    script: str,
) -> None:
    monkeypatch.setattr(isolated_raw, "_DEADLINE_SECONDS", 0.2)
    children = fixture_worker(script)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    _assert_isolated(error, "DEADLINE_EXCEEDED")
    _assert_reaped(children)


def test_never_reading_stdin_cannot_block_the_unicode_request_watchdog(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    scalar = "\U0001f642"
    reader = ExplicitHistoryReader(
        "https://audit.example.test",
        "synthetic-audit",
        "x",
        scalar * 4_096,
        scalar * 4_096,
    )
    ref = CheckpointVersionRef(scalar * 256, scalar * 256)
    script = _FIXTURE_PREAMBLE + "\nemit(READY)\ntime.sleep(5)\n"
    monkeypatch.setattr(isolated_raw, "_DEADLINE_SECONDS", 0.2)
    children = fixture_worker(script)
    error = _capture(lambda: isolated_raw.read_raw_checkpoint_version_isolated(reader, ref))
    _assert_isolated(error, "DEADLINE_EXCEEDED")
    _assert_reaped(children)


def test_late_success_after_deadline_is_never_published(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    script = (
        _FIXTURE_PREAMBLE
        + "\nemit(READY)\nrequest = consume_request()\ntime.sleep(0.3)\n"
        + "emit({'version': 1, 'status': 'ok', 'key': request['key'], "
        + "'version_id': request['version_id'], 'body_base64': ''})\n"
    )
    monkeypatch.setattr(isolated_raw, "_DEADLINE_SECONDS", 0.1)
    children = fixture_worker(script)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    _assert_isolated(error, "DEADLINE_EXCEEDED")
    _assert_reaped(children)


def test_attempt_expired_during_real_process_launch_cannot_return_late_success(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    children = fixture_worker(_success_script(b"late-launch-body"))
    immediate_spawn = isolated_raw._spawn_worker

    def delayed_spawn() -> subprocess.Popen[bytes]:
        process = immediate_spawn()
        time.sleep(0.1)
        return process

    monkeypatch.setattr(isolated_raw, "_spawn_worker", delayed_spawn)
    monkeypatch.setattr(isolated_raw, "_DEADLINE_SECONDS", 0.05)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    _assert_isolated(error, "DEADLINE_EXCEEDED")
    _assert_reaped(children)


@pytest.mark.parametrize(
    "script",
    [
        _success_script(b"expired-after-cleanup"),
        _FIXTURE_PREAMBLE
        + "\nemit(READY)\nconsume_request()\n"
        + "emit({'version': 1, 'status': 'read-error', 'code': 'PROVIDER_FAILURE'})\n",
    ],
    ids=("success", "read-error"),
)
def test_final_deadline_after_cleanup_rejects_body_and_completed_read_error(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
    script: str,
) -> None:
    children = fixture_worker(script)
    original_cleanup = isolated_raw._cleanup_worker

    def cleanup_then_expire(
        selected: Any,
        process: subprocess.Popen[bytes] | None,
        *,
        operation_failed: bool,
    ) -> list[BaseException]:
        outcomes = original_cleanup(selected, process, operation_failed=operation_failed)
        assert process is not None and process.returncode == 0
        monkeypatch.setattr(isolated_raw, "_monotonic", lambda: float("inf"))
        return outcomes

    monkeypatch.setattr(isolated_raw, "_cleanup_worker", cleanup_then_expire)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    _assert_isolated(error, "DEADLINE_EXCEEDED")
    _assert_reaped(children)


def _read_stream_exact(stream: Any, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        chunk = stream.read(size - len(output))
        if not chunk:
            raise AssertionError("worker stream closed before the expected bytes")
        output.extend(chunk)
    return bytes(output)


def _read_real_worker_ready(process: subprocess.Popen[bytes]) -> None:
    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    deadline = time.monotonic() + 5.0
    selected = selectors.DefaultSelector()
    frame = bytearray()
    frame_size: int | None = None
    try:
        selected.register(descriptor, selectors.EVENT_READ)
        while frame_size is None or len(frame) < frame_size:
            if process.poll() is not None:
                raise AssertionError("worker exited before READY")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("worker READY timed out")
            if not selected.select(remaining):
                if process.poll() is not None:
                    raise AssertionError("worker exited before READY")
                raise AssertionError("worker READY timed out")
            target = 4 if frame_size is None else frame_size
            try:
                chunk = os.read(descriptor, target - len(frame))
            except BlockingIOError:
                continue
            if not chunk:
                raise AssertionError("worker stream closed before READY")
            frame.extend(chunk)
            if frame_size is None and len(frame) == 4:
                payload_size = int.from_bytes(frame, "big")
                if payload_size > 512:
                    raise AssertionError("worker READY exceeded its bound")
                frame_size = 4 + payload_size
    finally:
        selected.close()

    if process.poll() is not None:
        raise AssertionError("worker exited after READY")
    try:
        pairs = json.loads(bytes(frame[4:]).decode("utf-8"), object_pairs_hook=list)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise AssertionError("worker emitted malformed READY") from None
    if not isinstance(pairs, list) or any(
        not isinstance(item, tuple) or len(item) != 2 for item in pairs
    ):
        raise AssertionError("worker emitted malformed READY")
    ready: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in ready:
            raise AssertionError("worker emitted malformed READY")
        ready[key] = value
    assert set(ready) == set(_READY)
    assert all(
        type(ready[key]) is type(value) and ready[key] == value for key, value in _READY.items()
    )


def test_actual_private_worker_rejects_oversized_request_before_eof() -> None:
    process = isolated_raw._spawn_worker()
    timed_out = False
    try:
        assert process.stdout is not None
        ready_size = int.from_bytes(_read_stream_exact(process.stdout, 4), "big")
        assert ready_size <= 512
        isolated_raw._decode_ready(_read_stream_exact(process.stdout, ready_size))
        assert process.stdin is not None
        process.stdin.write((131_073).to_bytes(4, "big"))
        process.stdin.flush()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        if process.stdout is not None and not process.stdout.closed:
            process.stdout.close()
    assert not timed_out
    assert process.returncode != 0
    assert process.stdin is not None and process.stdin.closed
    assert process.stdout is not None and process.stdout.closed


def test_real_pipe_short_reads_and_writes_advance_statefully(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    real_read = isolated_raw._read_fd
    real_write = isolated_raw._write_fd
    reads: list[int] = []
    writes: list[int] = []

    def short_read(fd: int, size: int) -> bytes:
        reads.append(size)
        return real_read(fd, min(size, 5))

    def short_write(fd: int, payload: bytes) -> int:
        writes.append(len(payload))
        return real_write(fd, payload[:7])

    monkeypatch.setattr(isolated_raw, "_read_fd", short_read)
    monkeypatch.setattr(isolated_raw, "_write_fd", short_write)
    children = fixture_worker(_success_script(b"short-io"))
    result = isolated_raw.read_raw_checkpoint_version_isolated(
        _reader(), CheckpointVersionRef("checkpoints/k", "v")
    )
    assert result.body == b"short-io"
    assert len(reads) > 3
    assert len(writes) > 3
    assert max(reads) > 5
    assert max(writes) > 7
    _assert_reaped(children)


@pytest.mark.parametrize(
    ("reader", "cancel", "expected_type"),
    [
        (
            ExplicitHistoryReader("http://example.test", "synthetic-audit", "us-east-1", "a", "b"),
            None,
            RawVersionInputError,
        ),
        (_reader(), threading.Event(), RawVersionReadCancelled),
    ],
)
def test_invalid_input_and_preset_cancellation_create_no_child(
    monkeypatch: pytest.MonkeyPatch,
    reader: ExplicitHistoryReader,
    cancel: threading.Event | None,
    expected_type: type[BaseException],
) -> None:
    if cancel is not None:
        cancel.set()

    def forbidden_spawn() -> subprocess.Popen[bytes]:
        raise AssertionError("prelaunch rejection attempted to create a child")

    monkeypatch.setattr(isolated_raw, "_spawn_worker", forbidden_spawn)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            reader,
            CheckpointVersionRef("checkpoints/k", "v"),
            cancel=cancel,
        )
    )
    assert type(error) is expected_type


def test_unsupported_runtime_and_start_failure_are_fixed_and_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ExplicitHistoryReader(
        "https://audit.example.test",
        "synthetic-audit",
        "us-east-1",
        "visible-access-sentinel",
        "visible-secret-sentinel",
    )
    ref = CheckpointVersionRef("checkpoints/k", "v")
    monkeypatch.setattr(isolated_raw, "_runtime_supported", lambda: False)
    unsupported = _capture(lambda: isolated_raw.read_raw_checkpoint_version_isolated(reader, ref))
    _assert_isolated(unsupported, "RUNTIME_UNSUPPORTED")

    monkeypatch.setattr(isolated_raw, "_runtime_supported", lambda: True)

    def failed_spawn() -> subprocess.Popen[bytes]:
        raise OSError("visible-spawn-detail")

    monkeypatch.setattr(isolated_raw, "_spawn_worker", failed_spawn)
    start_failed = _capture(lambda: isolated_raw.read_raw_checkpoint_version_isolated(reader, ref))
    _assert_isolated(start_failed, "WORKER_START_FAILED")
    rendered = repr(start_failed)
    assert "visible-access-sentinel" not in rendered
    assert "visible-secret-sentinel" not in rendered
    assert "visible-spawn-detail" not in rendered


def _cancel_after_milestone(milestone: threading.Event, cancel: threading.Event) -> None:
    if milestone.wait(timeout=2):
        cancel.set()


@pytest.mark.parametrize("phase", ["write", "read", "post-result"])
def test_cancellation_during_real_child_io_never_publishes_a_body(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
    phase: str,
) -> None:
    cancel = threading.Event()
    milestone = threading.Event()
    monkeypatch.setattr(isolated_raw, "_DEADLINE_SECONDS", 1.0)
    if phase == "write":
        scalar = "\U0001f642"
        reader = ExplicitHistoryReader(
            "https://audit.example.test",
            "synthetic-audit",
            "x",
            scalar * 4_096,
            scalar * 4_096,
        )
        ref = CheckpointVersionRef(scalar * 256, scalar * 256)
        script = _FIXTURE_PREAMBLE + "\nemit(READY)\ntime.sleep(5)\n"

        original_write = isolated_raw._write_fd

        def observed_write(fd: int, payload: bytes) -> int:
            written = original_write(fd, payload)
            milestone.set()
            return written

        monkeypatch.setattr(isolated_raw, "_write_fd", observed_write)
    else:
        reader = _reader()
        ref = CheckpointVersionRef("checkpoints/k", "v")
        script = (
            _FIXTURE_PREAMBLE + "\nemit(READY)\nconsume_request()\ntime.sleep(5)\n"
            if phase == "read"
            else _success_script(delay_after=5)
        )
        if phase == "read":
            original_close = isolated_raw._close_stdin

            def observed_close(*args: Any, **kwargs: Any) -> None:
                original_close(*args, **kwargs)
                milestone.set()

            monkeypatch.setattr(isolated_raw, "_close_stdin", observed_close)
        else:
            original_decode = isolated_raw._decode_result

            def observed_decode(*args: Any, **kwargs: Any) -> Any:
                result = original_decode(*args, **kwargs)
                milestone.set()
                return result

            monkeypatch.setattr(isolated_raw, "_decode_result", observed_decode)
    children = fixture_worker(script)
    setter = threading.Thread(target=_cancel_after_milestone, args=(milestone, cancel))
    setter.start()
    try:
        error = _capture(
            lambda: isolated_raw.read_raw_checkpoint_version_isolated(reader, ref, cancel=cancel)
        )
    finally:
        setter.join(timeout=2)
        assert not setter.is_alive()
    assert milestone.is_set()
    assert cancel.is_set()
    assert type(error) is RawVersionReadCancelled
    assert str(error) == "raw checkpoint version read cancelled"
    _assert_reaped(children)


def test_cancellation_after_real_child_exit_before_reap_discards_body(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    cancel = threading.Event()
    stdin_closed = threading.Event()
    exit_observed = threading.Event()
    children = fixture_worker(_success_script(b"must-not-publish"))
    selector_factory = isolated_raw.selectors.DefaultSelector
    original_close_stdin = isolated_raw._close_stdin

    class ExitObservingSelector:
        def __init__(self) -> None:
            self._delegate = selector_factory()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._delegate, name)

        def select(self, timeout: float | None = None) -> Any:
            events = self._delegate.select(timeout)
            if stdin_closed.is_set() and not exit_observed.is_set():
                assert len(children) == 1
                _observe_exited_without_reaping(children[0], timeout=2)
                exit_observed.set()
                cancel.set()
            return events

    def close_stdin_then_arm(*args: Any, **kwargs: Any) -> None:
        original_close_stdin(*args, **kwargs)
        stdin_closed.set()

    monkeypatch.setattr(isolated_raw.selectors, "DefaultSelector", ExitObservingSelector)
    monkeypatch.setattr(isolated_raw, "_close_stdin", close_stdin_then_arm)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(),
            CheckpointVersionRef("checkpoints/k", "v"),
            cancel=cancel,
        )
    )
    assert exit_observed.is_set()
    assert cancel.is_set()
    assert type(error) is RawVersionReadCancelled
    assert str(error) == "raw checkpoint version read cancelled"
    _assert_reaped(children)


def test_final_cancellation_after_confirmed_reap_discards_success_without_signalling(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    cancel = threading.Event()
    children = fixture_worker(_success_script(b"must-not-publish"))
    original_cleanup = isolated_raw._cleanup_worker
    signals: list[tuple[int, int]] = []

    def cleanup_then_cancel(
        selected: Any,
        process: subprocess.Popen[bytes] | None,
        *,
        operation_failed: bool,
    ) -> list[BaseException]:
        outcomes = original_cleanup(selected, process, operation_failed=operation_failed)
        assert process is not None and process.returncode == 0
        cancel.set()
        return outcomes

    monkeypatch.setattr(isolated_raw, "_cleanup_worker", cleanup_then_cancel)
    monkeypatch.setattr(
        isolated_raw,
        "_killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(),
            CheckpointVersionRef("checkpoints/k", "v"),
            cancel=cancel,
        )
    )
    assert type(error) is RawVersionReadCancelled
    assert signals == []
    _assert_reaped(children)


@pytest.mark.parametrize(
    "sentinel",
    [
        ExceptionGroup("parent sentinel", [RuntimeError("leaf")]),
        _UnexpectedSelectorCloseError("custom-parent-sentinel"),
    ],
    ids=["exception-group", "custom-exception"],
)
def test_unexpected_selector_close_fault_keeps_exact_identity_and_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
    sentinel: BaseException,
) -> None:
    children = fixture_worker(_success_script(b"must-not-publish"))
    wrappers = _install_selector_close_failure(monkeypatch, sentinel)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    try:
        assert error is sentinel
    finally:
        assert len(wrappers) == 1
        assert wrappers[0].delegate_closed
        _assert_reaped(children)


def test_expected_selector_close_oserror_remains_fixed_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    children = fixture_worker(_success_script(b"must-not-publish"))
    wrappers = _install_selector_close_failure(monkeypatch, OSError("expected-close-failure"))
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    try:
        _assert_isolated(error, "CLEANUP_FAILED")
    finally:
        assert len(wrappers) == 1
        assert wrappers[0].delegate_closed
        _assert_reaped(children)


@pytest.mark.parametrize(
    "sentinel",
    [
        MemoryError("memory-sentinel"),
        KeyboardInterrupt(),
        SystemExit(71),
        RuntimeError("runtime-sentinel"),
    ],
)
def test_unexpected_parent_fault_after_spawn_keeps_identity_and_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
    sentinel: BaseException,
) -> None:
    children = fixture_worker(_FIXTURE_PREAMBLE + "\nemit(READY)\ntime.sleep(5)\n")

    def fail_selector() -> Any:
        raise sentinel

    monkeypatch.setattr(isolated_raw.selectors, "DefaultSelector", fail_selector)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    assert error is sentinel
    _assert_reaped(children)


def test_unexpected_fault_after_real_reap_never_signals_cached_process_identity(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    sentinel = RuntimeError("post-reap-sentinel")
    children = fixture_worker(_success_script())
    original_run = isolated_raw._run_protocol
    signals: list[tuple[int, int]] = []

    def run_then_fail(*args: Any, **kwargs: Any) -> Any:
        outcome = original_run(*args, **kwargs)
        process = cast(subprocess.Popen[bytes], args[1])
        assert process.returncode == 0
        assert isinstance(outcome, RawCheckpointVersion)
        raise sentinel

    monkeypatch.setattr(isolated_raw, "_run_protocol", run_then_fail)
    monkeypatch.setattr(
        isolated_raw,
        "_killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    assert error is sentinel
    assert signals == []
    _assert_reaped(children)


def test_combined_parent_operation_and_cleanup_faults_retain_exact_identities(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    operation = RuntimeError("operation-sentinel")
    cleanup = KeyboardInterrupt()
    script = _FIXTURE_PREAMBLE + "\nos.close(0)\nemit(READY)\ntime.sleep(0.1)\n"
    children = fixture_worker(script)

    def fail_write(_fd: int, _payload: bytes) -> int:
        raise operation

    def fail_signal(_pid: int, _signal: int) -> None:
        raise cleanup

    monkeypatch.setattr(isolated_raw, "_write_fd", fail_write)
    monkeypatch.setattr(isolated_raw, "_killpg", fail_signal)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    assert type(error) is BaseExceptionGroup
    assert error.message == "isolated raw checkpoint operation and cleanup failed"
    assert error.exceptions == (operation, cleanup)
    _assert_reaped(children)


def test_unconfirmed_reap_retains_operation_and_adds_fixed_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    fixture_worker: Callable[[str], list[subprocess.Popen[bytes]]],
) -> None:
    script = (
        _FIXTURE_PREAMBLE
        + '\npayload = b\'{"version":1,"status":"wrong"}\'\n'
        + "sys.stdout.buffer.write(len(payload).to_bytes(4, 'big') + payload)\n"
        + "sys.stdout.buffer.flush()\ntime.sleep(5)\n"
    )
    children = fixture_worker(script)
    monkeypatch.setattr(isolated_raw, "_killpg", lambda _pid, _signal: None)
    monkeypatch.setattr(isolated_raw, "_REAP_SECONDS", 0.01)
    error = _capture(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            _reader(), CheckpointVersionRef("checkpoints/k", "v")
        )
    )
    assert type(error) is ExceptionGroup
    assert [item.code for item in error.exceptions] == ["PROTOCOL_INVALID", "CLEANUP_FAILED"]
    assert children[0].returncode is None


def test_worker_command_and_environment_are_fixed_and_do_not_inherit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "hostile-access-sentinel")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "hostile-secret-sentinel")
    monkeypatch.setenv("HTTPS_PROXY", "http://hostile-proxy.invalid")
    process = isolated_raw._spawn_worker()
    try:
        _read_real_worker_ready(process)
        argv = Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")[:-1]
        environment = {
            entry
            for entry in Path(f"/proc/{process.pid}/environ").read_bytes().split(b"\0")
            if entry
        }
        assert argv[:4] == [os.fsencode(sys.executable), b"-I", b"-B", b"-u"]
        assert Path(os.fsdecode(argv[4])).name == "_isolated_raw_worker.py"
        assert Path(os.fsdecode(argv[5])).name == "src"
        joined = b"\0".join(argv) + b"\0".join(environment)
        assert b"hostile-access-sentinel" not in joined
        assert b"hostile-secret-sentinel" not in joined
        assert b"hostile-proxy.invalid" not in joined
        assert environment == {b"LANG=C.UTF-8", b"TZ=UTC"}
    finally:
        assert process.stdin is not None
        process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        assert process.stdout is not None
        process.stdout.close()
    assert process.returncode is not None


_LIMIT_PROBE_PREAMBLE = """
import importlib.util
import json
import os
import resource
import signal
import sys
import time

worker_path = sys.argv[1]
assert not any(name == "easysynq_api" or name.startswith("easysynq_api.") for name in sys.modules)
spec = importlib.util.spec_from_file_location("isolated_limit_probe", worker_path)
assert spec is not None and spec.loader is not None
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
assert not any(name == "easysynq_api" or name.startswith("easysynq_api.") for name in sys.modules)
worker._apply_limits()
"""


def _spawn_limit_probe(body: str, *arguments: str) -> subprocess.Popen[bytes]:
    worker_path = Path(isolated_raw.__file__).with_name("_isolated_raw_worker.py").resolve()
    return subprocess.Popen(  # noqa: S603 - fixed interpreter and owned finite test probe
        [
            sys.executable,
            "-I",
            "-B",
            "-u",
            "-c",
            _LIMIT_PROBE_PREAMBLE + body,
            str(worker_path),
            *arguments,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        start_new_session=True,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
    )


def _communicate_probe(
    process: subprocess.Popen[bytes],
    *,
    timeout: float = 5,
) -> tuple[bytes, bytes]:
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
        raise


def test_apply_limits_reports_exact_effective_hard_and_soft_values() -> None:
    body = """
limits = {
    "address_space": resource.getrlimit(resource.RLIMIT_AS),
    "cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU),
    "file_descriptors": resource.getrlimit(resource.RLIMIT_NOFILE),
    "file_bytes": resource.getrlimit(resource.RLIMIT_FSIZE),
    "core_bytes": resource.getrlimit(resource.RLIMIT_CORE),
}
print(json.dumps(limits, sort_keys=True))
"""
    process = _spawn_limit_probe(body)
    stdout, stderr = _communicate_probe(process)
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert json.loads(stdout) == {
        "address_space": [536_870_912, 536_870_912],
        "cpu_seconds": [10, 10],
        "file_descriptors": [64, 64],
        "file_bytes": [0, 0],
        "core_bytes": [0, 0],
    }


def test_actual_address_space_limit_rejects_an_over_ceiling_allocation() -> None:
    body = """
try:
    bytearray(600_000_000)
except MemoryError:
    print("MEMORY_ERROR")
else:
    raise SystemExit(31)
"""
    process = _spawn_limit_probe(body)
    stdout, stderr = _communicate_probe(process)
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert stdout == b"MEMORY_ERROR\n"


def test_actual_file_growth_limit_rejects_regular_file_bytes(tmp_path: Path) -> None:
    target = tmp_path / "limit-probe.bin"
    body = """
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
try:
    with open(sys.argv[2], "wb", buffering=0) as output:
        output.write(b"x")
except OSError as error:
    print(json.dumps({"errno": error.errno, "size": os.path.getsize(sys.argv[2])}))
else:
    raise SystemExit(32)
"""
    process = _spawn_limit_probe(body, str(target))
    stdout, stderr = _communicate_probe(process)
    assert process.returncode == 0, stderr.decode(errors="replace")
    observation = json.loads(stdout)
    assert observation["size"] == 0
    assert observation["errno"] == errno.EFBIG
    assert target.stat().st_size == 0


def test_actual_cpu_limit_terminates_a_finite_owned_probe() -> None:
    body = """
print("CPU_READY", flush=True)
value = 0
for _ in range(1_000_000_000_000):
    value = (value + 1) % 1000003
"""
    process = _spawn_limit_probe(body)
    try:
        stdout, stderr = _communicate_probe(process, timeout=15)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
    assert stdout == b"CPU_READY\n", stderr.decode(errors="replace")
    assert process.returncode in {-signal.SIGXCPU, -signal.SIGKILL}
