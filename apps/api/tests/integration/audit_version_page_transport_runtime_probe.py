"""Finite installed-image acceptance probe for the original version-page transport."""

from __future__ import annotations

import argparse
import errno
import hashlib
import http.server
import importlib.util
import json
import os
import platform
import resource
import selectors
import signal
import ssl
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID
from xml.parsers import expat
from xml.sax.saxutils import escape

import boto3
import botocore
import certifi

from easysynq_api.services.audit import isolated_version_page as isolated
from easysynq_api.services.audit import version_page_transport as transport
from easysynq_api.services.audit.sink import ExplicitHistoryReader

_ORG = UUID("11111111-1111-4111-8111-111111111111")
_PREFIX = "checkpoints/11111111-1111-4111-8111-111111111111/"
_BUCKET = "page-transport-fixture"
_OPEN = b'<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
_CLOSE = b"</ListVersionsResult>"
_BODY_LIMIT = 16_777_216
_BUFFERED_BYTES = 603_979_776
_CHUNK = 65_536
_LIMITS = {
    "address_space": [536_870_912, 536_870_912],
    "cpu_seconds": [20, 20],
    "file_descriptors": [64, 64],
    "file_bytes": [0, 0],
    "core_bytes": [0, 0],
}


def _page(
    entries: bytes = b"",
    *,
    key: str | None = None,
    version: str | None = None,
    next_key: str | None = None,
    next_version: str | None = None,
) -> bytes:
    fields = (
        f"<Name>{_BUCKET}</Name><Prefix>{quote(_PREFIX, safe='-_.~')}</Prefix>"
        "<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
        f"<IsTruncated>{'true' if next_key is not None else 'false'}</IsTruncated>"
    )
    for tag, value, encoded in (
        ("KeyMarker", key, True),
        ("VersionIdMarker", version, False),
        ("NextKeyMarker", next_key, True),
        ("NextVersionIdMarker", next_version, False),
    ):
        if value is not None:
            content = quote(value, safe="-_.~") if encoded else escape(value)
            fields += f"<{tag}>{content}</{tag}>"
    return _OPEN + fields.encode() + entries + _CLOSE


def _entry(kind: str, key: str, version: str) -> bytes:
    return (
        f"<{kind}><Key>{quote(key, safe='-_.~')}</Key>"
        f"<VersionId>{escape(version)}</VersionId>"
        f"<IsLatest>false</IsLatest></{kind}>"
    ).encode()


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, body: bytes, route: str, host: str) -> None:
        super().__init__((host, 0), _Handler)
        self.body = body
        self.route = route
        self.redirect = ""
        self.requests: list[dict[str, str]] = []
        self.started = threading.Event()
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.errors: list[str] = []
        self.sent = 0
        self.maximum_chunk = 0
        self.disconnected = False

    def handle_error(self, _request: object, _client_address: object) -> None:
        self.errors.append(type(sys.exception()).__name__)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _Server)
        self.connection.settimeout(3)
        server.requests.append(
            {
                "method": "GET",
                "target": self.path,
                "authorization": self.headers.get("Authorization", ""),
            }
        )
        server.started.set()
        self.close_connection = True
        try:
            status = 301 if server.route == "redirect" else 403 if server.route == "deny" else 200
            self.send_response(status)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Connection", "close")
            if server.route == "redirect":
                self.send_header("Location", server.redirect)
                self.send_header("x-amz-bucket-region", "us-west-2")
            size = _BUFFERED_BYTES if server.route == "buffered" else len(server.body)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if server.route == "blocked":
                while not server.stop.wait(0.05):
                    try:
                        if self.connection.recv(1) == b"":
                            server.disconnected = True
                            return
                    except TimeoutError:
                        continue
                return
            offset = 0
            while offset < size and not server.stop.is_set():
                amount = min(1 if server.route == "trickle" else _CHUNK, size - offset)
                chunk = (
                    b" " * amount
                    if server.route == "buffered"
                    else server.body[offset : offset + amount]
                )
                self.wfile.write(chunk)
                self.wfile.flush()
                server.sent += len(chunk)
                server.maximum_chunk = max(server.maximum_chunk, len(chunk))
                offset += amount
                if server.route == "trickle":
                    server.stop.wait(1)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
            server.disconnected = True
        finally:
            server.finished.set()


@contextmanager
def _server(
    body: bytes,
    *,
    route: str = "body",
    tls: ssl.SSLContext | None = None,
    host: str = "127.0.0.1",
) -> Iterator[_Server]:
    server = _Server(body, route, host)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.stop.set()
        server.shutdown()
        server.server_close()
        thread.join(4)
        assert not thread.is_alive()
        assert not server.requests or server.finished.is_set()
        assert server.errors == []


def _reader(
    server: _Server,
    config: dict[str, Any],
    *,
    tls: bool = False,
    host: str = "127.0.0.1",
) -> ExplicitHistoryReader:
    return ExplicitHistoryReader(
        f"{'https' if tls else 'http'}://{host}:{server.server_port}",
        _BUCKET,
        "us-east-1",
        config["access_key"],
        config["secret_key"],
    )


def _memory(pid: int) -> tuple[int, int] | None:
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except (FileNotFoundError, ProcessLookupError):
        return None
    values = {}
    for line in lines:
        name, _, value = line.partition(":")
        if name in {"VmRSS", "VmSize"}:
            number, unit = value.split()
            assert unit == "kB"
            values[name] = int(number)
    return (values["VmRSS"], values["VmSize"]) if len(values) == 2 else None


def _capture(
    operation: Callable[[], Any],
    secrets: tuple[str, ...],
    *,
    producer: Callable[[], subprocess.Popen[bytes]] | None = None,
    result_seen: threading.Event | None = None,
    eof_seen: threading.Event | None = None,
) -> tuple[Any | None, BaseException | None, dict[str, Any]]:
    original_spawn = isolated._spawn_worker
    original_ready = isolated._decode_ready
    original_selector = isolated.selectors.DefaultSelector
    original_piece = isolated._read_frame_piece
    original_read = isolated._read_fd
    processes: list[subprocess.Popen[bytes]] = []
    selected: list[selectors.BaseSelector] = []
    samplers: list[threading.Thread] = []
    stopped = threading.Event()
    maxima = [0, 0]
    limits: list[dict[str, list[int]]] = []
    private = []
    stdout_eof = False

    def sample(process: subprocess.Popen[bytes]) -> None:
        while not stopped.wait(0.01):
            memory = _memory(process.pid)
            if memory is not None:
                maxima[0] = max(maxima[0], memory[0])
                maxima[1] = max(maxima[1], memory[1])

    def spawn() -> subprocess.Popen[bytes]:
        process = original_spawn() if producer is None else producer()
        processes.append(process)
        thread = threading.Thread(target=sample, args=(process,))
        thread.start()
        samplers.append(thread)
        return process

    def ready(payload: bytes) -> None:
        original_ready(payload)
        assert len(processes) == 1
        pid = processes[0].pid
        limits.append(
            {
                name: list(resource.prlimit(pid, limit))
                for name, limit in (
                    ("address_space", resource.RLIMIT_AS),
                    ("cpu_seconds", resource.RLIMIT_CPU),
                    ("file_descriptors", resource.RLIMIT_NOFILE),
                    ("file_bytes", resource.RLIMIT_FSIZE),
                    ("core_bytes", resource.RLIMIT_CORE),
                )
            }
        )
        memory = _memory(pid)
        assert memory is not None
        maxima[0], maxima[1] = max(maxima[0], memory[0]), max(maxima[1], memory[1])
        environment = Path(f"/proc/{pid}/environ").read_bytes()
        assert set(environment.rstrip(b"\0").split(b"\0")) == {b"LANG=C.UTF-8", b"TZ=UTC"}
        argv = Path(f"/proc/{pid}/cmdline").read_bytes()
        assert all(secret.encode() not in argv + environment for secret in secrets)
        private.append(True)

    def selector() -> selectors.BaseSelector:
        value = original_selector()
        selected.append(value)
        return value

    def piece(fd: int, buffer: bytearray, maximum: int) -> tuple[bytes | None, bool]:
        candidate, eof = original_piece(fd, buffer, maximum)
        if candidate is not None and candidate.startswith(b"VP1\x00") and result_seen is not None:
            result_seen.set()
        return candidate, eof

    def read(fd: int, size: int) -> bytes:
        nonlocal stdout_eof
        value = original_read(fd, size)
        if value == b"":
            stdout_eof = True
            if eof_seen is not None:
                eof_seen.set()
        return value

    isolated._spawn_worker = spawn
    isolated._decode_ready = ready
    isolated.selectors.DefaultSelector = selector
    isolated._read_frame_piece = piece
    isolated._read_fd = read
    value = None
    error = None
    started = time.monotonic()
    try:
        try:
            value = operation()
        except BaseException as caught:  # noqa: BLE001 - preserve exact public outcome
            error = caught
    finally:
        isolated._spawn_worker = original_spawn
        isolated._decode_ready = original_ready
        isolated.selectors.DefaultSelector = original_selector
        isolated._read_frame_piece = original_piece
        isolated._read_fd = original_read
        stopped.set()
        for thread in samplers:
            thread.join(2)
            assert not thread.is_alive()
    assert len(processes) == 1 and limits == [_LIMITS] and private == [True]
    process = processes[0]
    assert process.returncode is not None and not Path(f"/proc/{process.pid}").exists()
    assert process.stdin is not None and process.stdin.closed
    assert process.stdout is not None and process.stdout.closed
    assert len(selected) == 1 and selected[0].get_map() is None
    assert all(0 < value <= 524_288 for value in maxima)
    return (
        value,
        error,
        {
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "exit_code": process.returncode,
            "limits": limits[0],
            "maximum_rss_kib": maxima[0],
            "maximum_vms_kib": maxima[1],
            "reaped": True,
            "pipes_closed": True,
            "selector_closed": True,
            "worker_environment_fixed": True,
            "worker_argv_private": True,
            "spawn_kind": "production-worker" if producer is None else "adversarial-ipc-producer",
            "stdout_eof_observed": stdout_eof,
        },
    )


def _expected_target(key: str | None, version: str | None) -> str:
    target = (
        f"/{_BUCKET}?versions&prefix={quote(_PREFIX, safe='-_.~')}&max-keys=1000&encoding-type=url"
    )
    if key is not None:
        target += "&key-marker=" + quote(key, safe="-_.~")
    if version is not None:
        target += "&version-id-marker=" + quote(version, safe="-_.~")
    return target


def _assert_request(
    server: _Server, config: dict[str, Any], key: str | None, version: str | None
) -> None:
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["method"] == "GET" and request["target"] == _expected_target(key, version)
    assert request["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert f"Credential={config['access_key']}/" in request["authorization"]
    assert "/us-east-1/s3/aws4_request" in request["authorization"]


def _call(
    reader: ExplicitHistoryReader,
    *,
    key: str | None = None,
    version: str | None = None,
    cancel: threading.Event | None = None,
    org: UUID = _ORG,
) -> tuple[Any | None, BaseException | None, dict[str, Any]]:
    return _capture(
        lambda: isolated.read_raw_checkpoint_version_page_isolated(
            reader,
            org,
            key_marker=key,
            version_id_marker=version,
            cancel=cancel,
        ),
        (reader.access_key, reader.secret_key),
    )


def _controlled(error: BaseException | None, code: str) -> None:
    assert type(error) is transport.VersionPageReadError
    assert error.code == code and error.__cause__ is None


def _body_cases(config: dict[str, Any]) -> dict[str, Any]:
    key = _PREFIX + "literal%2F+/雪"
    version = "v%2F+/雪"
    mixed = _entry("Version", key, version) * 2 + _entry("DeleteMarker", key, "null")
    simple = _page(mixed)
    blank = _page()
    exact = blank[: -len(_CLOSE)] + b" " * (_BODY_LIMIT - len(blank)) + _CLOSE
    cases = (
        ("duplicates-markers-opaque-labels", simple, None, None, "accepted"),
        ("key-only-cursor", _page(key=key), key, None, "accepted"),
        ("two-part-cursor", _page(key=key, version=version), key, version, "accepted"),
        (
            "control-null-cursor",
            _page(key=key + "\x00", version="null"),
            key + "\x00",
            "null",
            "accepted",
        ),
        (
            "next-two-part-cursor",
            _page(_entry("Version", key, version), next_key=key, next_version=version),
            None,
            None,
            "accepted",
        ),
        ("malformed-200", b"<ListVersionsResult>", None, None, "RESPONSE_INVALID"),
        ("coercible-truncation", blank.replace(b"false", b"1"), None, None, "RESPONSE_INVALID"),
        (
            "coercible-latest",
            simple.replace(b"<IsLatest>false</IsLatest>", b"<IsLatest>0</IsLatest>"),
            None,
            None,
            "RESPONSE_INVALID",
        ),
        (
            "wrong-namespace",
            blank.replace(b"2006-03-01", b"2006-03-02"),
            None,
            None,
            "RESPONSE_INVALID",
        ),
        (
            "wrong-scope",
            blank.replace(b"<Name>page-transport-fixture", b"<Name>other-fixture"),
            None,
            None,
            "SCOPE_MISMATCH",
        ),
        (
            "thousand-observations",
            _page(_entry("Version", key, version) * 1000),
            None,
            None,
            "accepted",
        ),
        (
            "thousand-one-observations",
            _page(_entry("Version", key, version) * 1000 + _entry("DeleteMarker", key, "null")),
            None,
            None,
            "PAGE_LIMIT",
        ),
        ("exact-body-boundary", exact, None, None, "accepted"),
        ("body-boundary-one-over", exact + b" ", None, None, "BODY_LIMIT"),
    )
    receipts = {}
    vector_digest = hashlib.sha256()
    for name, body, marker, version_marker, expected in cases:
        vector_digest.update(name.encode() + b"\0" + len(body).to_bytes(8, "big") + body)
        vector_digest.update(json.dumps([marker, version_marker], ensure_ascii=True).encode())
        with _server(body) as server:
            value, error, worker = _call(
                _reader(server, config), key=marker, version=version_marker
            )
            _assert_request(server, config, marker, version_marker)
            if expected == "accepted":
                outcome = (
                    error.code
                    if type(error)
                    in {transport.VersionPageReadError, isolated.IsolatedVersionPageError}
                    else type(error).__name__
                )
                assert error is None and type(value) is transport.RawCheckpointVersionPage, (
                    f"{name}: {outcome}"
                )
                assert value.body == body
                page = value.page
                assert page.truncated is (name == "next-two-part-cursor")
                assert page.next_key_marker == (key if page.truncated else None)
                assert page.next_version_id_marker == (version if page.truncated else None)
                assert [(item.key, item.version_id) for item in page.versions] == (
                    [(key, version)] * 2
                    if name == "duplicates-markers-opaque-labels"
                    else [(key, version)] * 1000
                    if name == "thousand-observations"
                    else [(key, version)]
                    if name == "next-two-part-cursor"
                    else []
                )
                assert [(item.key, item.version_id) for item in page.delete_markers] == (
                    [(key, "null")] if name == "duplicates-markers-opaque-labels" else []
                )
            else:
                assert value is None
                _controlled(error, expected)
            assert worker["exit_code"] == 0
        receipts[name] = {
            "outcome": expected,
            "body_bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "physical_sends": len(server.requests),
            "worker": worker,
            "fixture_closed": True,
        }
    return {"cases": receipts, "input_vectors_sha256": vector_digest.hexdigest()}


def _tls_context(config: dict[str, Any], prefix: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(config[prefix + "_certificate"], config[prefix + "_private_key"])
    return context


def _routing(config: dict[str, Any]) -> dict[str, Any]:
    receipts = {}
    for name, certificate, host, expected in (
        ("trusted-tls", "trusted", "127.0.0.1", "accepted"),
        ("untrusted-tls", "untrusted", "127.0.0.1", "TRANSPORT_FAILURE"),
        ("wrong-host-tls", "trusted", "127.0.0.2", "TRANSPORT_FAILURE"),
    ):
        with _server(_page(), tls=_tls_context(config, certificate), host=host) as server:
            value, error, worker = _call(_reader(server, config, tls=True, host=host))
            if expected == "accepted":
                assert error is None and value.body == _page()
                _assert_request(server, config, None, None)
            else:
                assert value is None and server.requests == []
                _controlled(error, expected)
        receipts[name] = {
            "outcome": expected,
            "http_requests": len(server.requests),
            "worker": worker,
            "fixture_closed": True,
        }
    for route in ("redirect", "deny"):
        with _server(_page()) as target, _server(b"<Error/>", route=route) as server:
            server.redirect = f"http://127.0.0.1:{target.server_port}/forbidden"
            value, error, worker = _call(_reader(server, config))
            assert value is None
            _controlled(error, "PROVIDER_FAILURE")
            _assert_request(server, config, None, None)
            assert target.requests == []
        receipts[route] = {
            "outcome": "PROVIDER_FAILURE",
            "physical_sends": 1,
            "second_target_requests": 0,
            "worker": worker,
            "fixture_closed": True,
        }
    return receipts


def _late_hook_control(config: dict[str, Any]) -> dict[str, Any]:
    original = transport._create_client

    def late(reader: ExplicitHistoryReader, capture: Callable[..., None]) -> Any:
        client = original(reader, lambda **_kwargs: None)
        client.meta.events.register_first("before-parse.s3.ListObjectVersions", capture)
        return client

    transport._create_client = late
    try:
        with _server(b"<ListVersionsResult>") as server:
            try:
                transport.read_raw_checkpoint_version_page(_reader(server, config), _ORG)
            except transport.VersionPageReadError as error:
                # This is the identical malformed-200 contract used by _body_cases.
                # The deliberately late design cannot satisfy RESPONSE_INVALID.
                assert error.code == "PROVIDER_FAILURE"
            else:
                raise AssertionError("late capture unexpectedly accepted malformed XML")
            _assert_request(server, config, None, None)
    finally:
        transport._create_client = original
    return {
        "outcome": "PROVIDER_FAILURE",
        "required_outcome": "RESPONSE_INVALID",
        "contract_would_fail": True,
        "physical_sends": 1,
        "fixture_closed": True,
    }


def _containment(config: dict[str, Any]) -> dict[str, Any]:
    with _server(b"", route="buffered") as server:
        value, error, worker = _call(_reader(server, config))
        assert value is None and type(error) is isolated.IsolatedVersionPageError
        assert error.code == "WORKER_FAILED" and worker["exit_code"] != 0
        assert 0 < server.sent <= _BUFFERED_BYTES and server.maximum_chunk <= _CHUNK
        assert server.finished.wait(4) and server.disconnected
        _assert_request(server, config, None, None)
    receipts = {
        "oversized-buffered": {
            "outcome": "WORKER_FAILED",
            "planned_bytes": _BUFFERED_BYTES,
            "sent_bytes": server.sent,
            "maximum_chunk_bytes": server.maximum_chunk,
            "peer_disconnected": server.disconnected,
            "worker": worker,
            "physical_sends": 1,
            "fixture_closed": True,
        }
    }
    for route, cancellation, expected in (
        ("blocked", False, "TRANSPORT_FAILURE"),
        ("blocked", True, "cancelled"),
        ("trickle", False, "DEADLINE_EXCEEDED"),
    ):
        cancel = threading.Event()
        with _server(_page(), route=route) as server:

            def cancel_when_started(cancel: threading.Event = cancel) -> None:
                if server.started.wait(8):
                    cancel.set()

            thread = threading.Thread(target=cancel_when_started) if cancellation else None
            if thread is not None:
                thread.start()
            try:
                value, error, worker = _call(_reader(server, config), cancel=cancel)
            finally:
                if thread is not None:
                    thread.join(9)
                    assert not thread.is_alive()
            assert value is None
            if cancellation:
                assert type(error) is transport.VersionPageReadCancelled
                assert worker["elapsed_ms"] < 5000
            elif route == "blocked":
                _controlled(error, expected)
                assert 4500 <= worker["elapsed_ms"] < 10000
            else:
                assert type(error) is isolated.IsolatedVersionPageError and error.code == expected
                assert 29000 <= worker["elapsed_ms"] < 35000
                assert server.sent >= 2
            _assert_request(server, config, None, None)
        receipts["blocked-cancel" if cancellation else route] = {
            "outcome": expected,
            "worker": worker,
            "physical_sends": 1,
            "sent_bytes": server.sent,
            "fixture_closed": True,
        }
    with _server(_page()) as server:
        value, error, worker = _call(_reader(server, config))
        assert error is None and value.body == _page()
        _assert_request(server, config, None, None)
    receipts["parent-survives"] = {
        "outcome": "accepted",
        "worker": worker,
        "physical_sends": 1,
        "fixture_closed": True,
    }
    return receipts


_LIMIT_PREAMBLE = r"""
import errno
import importlib.util
import json
import os
import resource
import signal
import sys
import time

assert not any(name.startswith("easysynq_api") for name in sys.modules)
spec = importlib.util.spec_from_file_location("page_worker_limits", sys.argv[1])
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
worker._apply_limits()
assert not any(name.startswith("easysynq_api") for name in sys.modules)
limits = {
    name: list(resource.getrlimit(limit))
    for name, limit in (
        ("address_space", resource.RLIMIT_AS), ("cpu_seconds", resource.RLIMIT_CPU),
        ("file_descriptors", resource.RLIMIT_NOFILE), ("file_bytes", resource.RLIMIT_FSIZE),
        ("core_bytes", resource.RLIMIT_CORE),
    )
}
"""

_RESOURCE_SCRIPT = (
    _LIMIT_PREAMBLE
    + r"""
print(json.dumps({"limits": limits}), flush=True)
mode = sys.argv[2]
if mode == "cpu":
    print(json.dumps({"cpu": "started"}), flush=True)
    value = 0
    for _ in range(1_000_000_000_000):
        value = (value + 1) % 1000003
    raise SystemExit(91)
if mode == "memory":
    try:
        bytearray(603979776)
    except MemoryError:
        print(json.dumps({"memory": "MemoryError"}), flush=True)
    else:
        raise SystemExit(92)
elif mode == "file":
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    target = sys.argv[3]
    try:
        with open(target, "wb", buffering=0) as output:
            output.write(b"x")
    except OSError as error:
        print(json.dumps({"file_errno": error.errno, "bytes": os.path.getsize(target)}), flush=True)
    else:
        raise SystemExit(93)
elif mode == "descriptors":
    opened = []
    try:
        for _ in range(128):
            opened.append(os.open(os.devnull, os.O_RDONLY))
    except OSError as error:
        print(json.dumps({"descriptor_errno": error.errno, "opened": len(opened)}), flush=True)
    else:
        raise SystemExit(94)
    finally:
        for fd in opened:
            os.close(fd)
"""
)

_PRODUCER_SCRIPT = (
    _LIMIT_PREAMBLE
    + r"""
def frame(payload):
    return len(payload).to_bytes(4, "big") + payload

ready = {
    "version": 1, "operation": "checkpoint-version-page", "status": "ready",
    "address_space": 536870912, "cpu_seconds": 20, "file_descriptors": 64,
    "file_bytes": 0, "core_bytes": 0,
}
sys.stdout.buffer.write(frame(json.dumps(ready).encode()))
sys.stdout.buffer.flush()
declared = int.from_bytes(sys.stdin.buffer.read(4), "big")
request = json.loads(sys.stdin.buffer.read(declared))
assert sys.stdin.buffer.read(1) == b""
assert request["operation"] == "checkpoint-version-page"
mode = sys.argv[2]
result = b"VP1\x00" + bytes.fromhex(sys.argv[3])
if mode == "bad-magic":
    result = b"XX1\x00" + bytes.fromhex(sys.argv[3])
if mode == "unknown-error":
    result = b"VP1\x01UNREVIEWED_ERROR"
if mode == "oversized-frame":
    sys.stdout.buffer.write((16777221).to_bytes(4, "big"))
elif mode == "truncated-frame":
    sys.stdout.buffer.write(frame(result)[:-1])
else:
    sys.stdout.buffer.write(frame(result))
    if mode == "extra-frame":
        sys.stdout.buffer.write(frame(result))
sys.stdout.buffer.flush()
if mode == "post-result-failure":
    raise SystemExit(17)
if mode == "withheld-exit":
    os.close(1)
if mode in {"withheld-eof", "withheld-exit"}:
    time.sleep(60)
"""
)


def _fixture_process(script: str, mode: str, argument: str) -> subprocess.Popen[bytes]:
    worker = str(Path(isolated.__file__).with_name("_isolated_version_page_worker.py"))
    return subprocess.Popen(  # noqa: S603 - fixed finite installed-image fixture
        [sys.executable, "-I", "-B", "-u", "-c", script, worker, mode, argument],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        bufsize=0,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
    )


def _resources(config: dict[str, Any]) -> dict[str, Any]:
    writable = Path(config["writable"])
    target = writable / "page-limit.bin"
    target.write_bytes(b"writable-control")
    assert target.read_bytes() == b"writable-control"
    target.unlink()
    results = {}
    for mode in ("memory", "file", "descriptors", "cpu"):
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        process = _fixture_process(_RESOURCE_SCRIPT, mode, str(target))
        try:
            output, _stderr = process.communicate(timeout=45 if mode == "cpu" else 8)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    pipe.close()
        elapsed_ms = round((time.monotonic() - started) * 1000)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu_ms = round((after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime) * 1000)
        values = [json.loads(line) for line in output.splitlines()]
        assert len(values) == 2 and values[0] == {"limits": _LIMITS}
        if mode == "cpu":
            assert values[1] == {"cpu": "started"}
            assert process.returncode in {-signal.SIGKILL, -signal.SIGXCPU}
            assert 19000 <= cpu_ms <= 22000
        else:
            assert process.returncode == 0
            if mode == "memory":
                assert values[1] == {"memory": "MemoryError"}
            elif mode == "file":
                assert values[1] == {"file_errno": errno.EFBIG, "bytes": 0}
                assert target.stat().st_size == 0
                target.unlink()
            else:
                assert values[1]["descriptor_errno"] == errno.EMFILE
                assert 1 <= values[1]["opened"] <= 61
        assert not Path(f"/proc/{process.pid}").exists()
        assert process.stdin is not None and process.stdin.closed
        assert process.stdout is not None and process.stdout.closed
        results[mode] = {
            "outcome": values[1],
            "exit_code": process.returncode,
            "elapsed_ms": elapsed_ms,
            "child_cpu_ms": cpu_ms,
            "reaped": True,
            "pipes_closed": True,
        }
    assert list(writable.iterdir()) == []
    return {
        "effective": _LIMITS,
        "writable_control": True,
        "installed_worker_limit_function": True,
        "cases": results,
        "cleanup_complete": True,
    }


def _ipc(config: dict[str, Any]) -> dict[str, Any]:
    results = {}
    reader = ExplicitHistoryReader(
        "http://127.0.0.1:1", _BUCKET, "us-east-1", config["access_key"], config["secret_key"]
    )
    cases = (
        ("valid-result", "accepted"),
        ("bad-magic", "PROTOCOL_INVALID"),
        ("unknown-error", "PROTOCOL_INVALID"),
        ("oversized-frame", "OUTPUT_LIMIT"),
        ("truncated-frame", "PROTOCOL_INVALID"),
        ("extra-frame", "PROTOCOL_INVALID"),
        ("post-result-failure", "WORKER_FAILED"),
        ("withheld-eof", "DEADLINE_EXCEEDED"),
        ("withheld-exit", "cancelled"),
    )
    for mode, expected in cases:
        cancel = threading.Event()
        result_seen = threading.Event()
        eof_seen = threading.Event()

        def cancel_after_frame(
            cancel: threading.Event = cancel,
            seen: threading.Event = result_seen,
            eof: threading.Event = eof_seen,
        ) -> None:
            if seen.wait(10) and eof.wait(10):
                cancel.set()

        canceller = threading.Thread(target=cancel_after_frame) if mode == "withheld-exit" else None
        if canceller is not None:
            canceller.start()
        try:
            value, error, worker = _capture(
                lambda cancel=cancel: isolated.read_raw_checkpoint_version_page_isolated(
                    reader,
                    _ORG,
                    cancel=cancel,
                ),
                (reader.access_key, reader.secret_key),
                producer=lambda mode=mode: _fixture_process(_PRODUCER_SCRIPT, mode, _page().hex()),
                result_seen=result_seen,
                eof_seen=eof_seen,
            )
        finally:
            if canceller is not None:
                canceller.join(21)
                assert not canceller.is_alive()
        if expected == "accepted":
            assert error is None and type(value) is transport.RawCheckpointVersionPage
            assert value.body == _page()
        else:
            assert value is None
            if expected == "cancelled":
                assert type(error) is transport.VersionPageReadCancelled and result_seen.is_set()
                assert eof_seen.is_set() and worker["stdout_eof_observed"] is True
                assert worker["elapsed_ms"] < 5000
            else:
                assert type(error) is isolated.IsolatedVersionPageError and error.code == expected
        if mode == "withheld-eof":
            assert result_seen.is_set() and 29000 <= worker["elapsed_ms"] < 35000
            assert worker["stdout_eof_observed"] is False
        if mode == "post-result-failure":
            assert result_seen.is_set() and worker["exit_code"] == 17
        results[mode] = {
            "outcome": expected,
            "worker": worker,
            "valid_result_frame_seen": result_seen.is_set(),
        }
    return results


def _runtime() -> dict[str, Any]:
    missing = [
        name for name in ("mypy", "pytest", "ruff") if importlib.util.find_spec(name) is None
    ]
    assert missing == ["mypy", "pytest", "ruff"]
    assert os.getuid() == 10001
    return {
        "uid": os.getuid(),
        "dev_packages_absent": missing,
        "runtime_versions": {
            "python": platform.python_version(),
            "boto3": boto3.__version__,
            "botocore": botocore.__version__,
            "expat": expat.EXPAT_VERSION,
        },
    }


def _provider(config: dict[str, Any]) -> dict[str, Any]:
    reader = ExplicitHistoryReader(
        config["endpoint"],
        config["bucket"],
        "us-east-1",
        config["access_key"],
        config["secret_key"],
    )
    assert reader.endpoint == "https://127.0.0.1:9000"
    org = UUID(config["org_id"])
    expected = Counter(tuple(item) for item in config["expected"])
    assert sum(expected.values()) == 1007
    observed: list[tuple[str, str, str]] = []
    pages = []
    key = version = None
    seen = set()
    for _ in range(4):
        assert (key, version) not in seen
        seen.add((key, version))
        value, error, worker = _call(reader, key=key, version=version, org=org)
        assert error is None and type(value) is transport.RawCheckpointVersionPage
        page = value.page
        rows = [
            (kind, item.key, item.version_id)
            for kind, entries in (
                ("version", page.versions),
                ("delete_marker", page.delete_markers),
            )
            for item in entries
        ]
        assert len(rows) <= 1000
        observed.extend(rows)
        assert len(observed) <= 1007
        assert all(item[1].startswith(f"checkpoints/{org}/") for item in rows)
        assert worker["exit_code"] == 0 and worker["stdout_eof_observed"] is True
        pages.append(
            {
                "key_marker": key,
                "version_id_marker": version,
                "next_key_marker": page.next_key_marker,
                "next_version_id_marker": page.next_version_id_marker,
                "truncated": page.truncated,
                "observations": rows,
                "body_bytes": len(value.body),
                "body_sha256": hashlib.sha256(value.body).hexdigest(),
                "worker": worker,
            }
        )
        if not page.truncated:
            break
        assert page.next_key_marker is not None and page.next_version_id_marker is not None
        key, version = page.next_key_marker, page.next_version_id_marker
    assert len(pages) == 2 and pages[0]["truncated"] is True
    assert pages[-1]["truncated"] is False and len(pages[0]["observations"]) == 1000
    assert Counter(observed) == expected
    assert sum(item[0] == "delete_marker" for item in observed) == 3
    serialized = json.dumps(sorted(observed), ensure_ascii=True, separators=(",", ":")).encode()
    return {
        **_runtime(),
        "pages": pages,
        "observation_count": len(observed),
        "observations_sha256": hashlib.sha256(serialized).hexdigest(),
        "delete_markers": 3,
        "exact_expected_multiset": True,
        "cleanup_complete": True,
    }


def _synthetic(config: dict[str, Any]) -> dict[str, Any]:
    bodies = _body_cases(config)
    routing = _routing(config)
    control = _late_hook_control(config)
    containment = _containment(config)
    ipc = _ipc(config)
    resources = _resources(config)
    return {
        **_runtime(),
        **bodies,
        "routing": routing,
        "late_hook_control": control,
        "containment": containment,
        "ipc": ipc,
        "resources": resources,
        "cleanup_complete": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("certifi", "synthetic", "provider"))
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    config = json.loads(Path(arguments.config).read_text())
    if arguments.mode == "certifi":
        bundle = Path(certifi.where())
        result = {
            "certifi_path": str(bundle),
            "certifi_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        }
    elif arguments.mode == "provider":
        result = _provider(config)
    else:
        result = _synthetic(config)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
