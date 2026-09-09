"""Actual-image probe for the isolated exact-version process boundary."""

from __future__ import annotations

import argparse
import copy
import errno
import hashlib
import http.server
import json
import os
import resource
import select
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import boto3
import certifi

from easysynq_api.services.audit import bootstrap_bridge as bridge
from easysynq_api.services.audit import isolated_raw, raw_transport
from easysynq_api.services.audit.lineage import (
    AuditHead,
    BootstrapPin,
    RequiredCheckpointPin,
    StreamEnrollment,
)
from easysynq_api.services.audit.sink import CheckpointVersionRef, ExplicitHistoryReader

_ADDRESS_SPACE_BYTES = 536_870_912
_CPU_SECONDS = 10
_FILE_DESCRIPTORS = 64
_FILE_BYTES = 0
_CORE_BYTES = 0
_CHUNK_BYTES = 65_536
_STREAMED_ERROR_BYTES = _ADDRESS_SPACE_BYTES + _CHUNK_BYTES
_EXPECTED_LIMITS = {
    "address_space": [_ADDRESS_SPACE_BYTES, _ADDRESS_SPACE_BYTES],
    "cpu_seconds": [_CPU_SECONDS, _CPU_SECONDS],
    "file_descriptors": [_FILE_DESCRIPTORS, _FILE_DESCRIPTORS],
    "file_bytes": [_FILE_BYTES, _FILE_BYTES],
    "core_bytes": [_CORE_BYTES, _CORE_BYTES],
}


def _load(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.route = "success"
        self.payload = b"runtime-body"
        self.version_id = "runtime-version"
        self.redirect = ""
        self.requests: list[dict[str, str]] = []
        self.bytes_sent = 0
        self.maximum_chunk = 0
        self.peer_disconnect = ""
        self.finished = threading.Event()
        self.handler_errors: list[str] = []

    def handle_error(self, _request: object, _client_address: object) -> None:
        error = sys.exception()
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            self.peer_disconnect = type(error).__name__
            return
        self.handler_errors.append(type(error).__name__ if error is not None else "unknown")


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def owned_server(self) -> _Server:
        assert isinstance(self.server, _Server)
        return self.server

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _write(self, payload: bytes, *, account: bool = True) -> bool:
        server = self.owned_server
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError) as error:
            server.peer_disconnect = type(error).__name__
            return False
        if account:
            server.bytes_sent += len(payload)
            server.maximum_chunk = max(server.maximum_chunk, len(payload))
        return True

    def _write_chunk(self, payload: bytes) -> bool:
        if not self._write(f"{len(payload):X}\r\n".encode(), account=False):
            return False
        if not self._write(payload):
            return False
        return self._write(b"\r\n", account=False)

    def _peer_closed(self) -> bool:
        readable, _writable, _exceptional = select.select([self.connection], [], [], 0)
        if not readable:
            return False
        try:
            value = self.connection.recv(1, socket.MSG_PEEK)
        except (BrokenPipeError, ConnectionResetError) as error:
            self.owned_server.peer_disconnect = type(error).__name__
            return True
        if value == b"":
            self.owned_server.peer_disconnect = "PeerEOF"
            return True
        return False

    def do_GET(self) -> None:
        server = self.owned_server
        server.requests.append(
            {
                "method": "GET",
                "target": self.path,
                "authorization": self.headers.get("Authorization", ""),
            }
        )
        self.close_connection = True
        try:
            if server.route == "redirect":
                body = b"<Error><Code>PermanentRedirect</Code></Error>"
                self.send_response(301)
                self.send_header("Location", server.redirect)
                self.send_header("x-amz-bucket-region", "us-west-2")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self._write(body)
                return
            if server.route == "streamed-error":
                self.send_response(404)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                prefix = b"<Error><Code>NoSuchVersion</Code><Message>"
                suffix = b"</Message></Error>"
                first = prefix + b"x" * (_CHUNK_BYTES - len(prefix))
                if not self._write_chunk(first):
                    return
                remaining = _STREAMED_ERROR_BYTES - len(first)
                filler = b"x" * _CHUNK_BYTES
                while remaining > len(suffix):
                    size = min(_CHUNK_BYTES, remaining - len(suffix))
                    if not self._write_chunk(filler[:size]):
                        return
                    remaining -= size
                if self._write_chunk(suffix):
                    self._write(b"0\r\n\r\n", account=False)
                return
            payload = server.payload
            self.send_response(200)
            self.send_header("x-amz-version-id", server.version_id)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            if server.route == "trickle":
                for value in payload:
                    if self._peer_closed():
                        return
                    if not self._write(bytes((value,))):
                        return
                    time.sleep(3.5)
                return
            self._write(payload)
        finally:
            server.finished.set()


@contextmanager
def _server(
    *,
    route: str = "success",
    payload: bytes = b"runtime-body",
    version_id: str = "runtime-version",
    tls: ssl.SSLContext | None = None,
) -> Iterator[_Server]:
    server = _Server()
    server.route = route
    server.payload = payload
    server.version_id = version_id
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()
        if server.requests:
            assert server.finished.wait(6)
        assert server.handler_errors == []


def _reader(server: _Server, *, tls: bool = False) -> ExplicitHistoryReader:
    return ExplicitHistoryReader(
        f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}",
        "runtime-bucket",
        "us-east-1",
        os.environ["ISOLATED_ACCESS_KEY"],
        os.environ["ISOLATED_SECRET_KEY"],
    )


def _ref(key: str, version_id: str) -> CheckpointVersionRef:
    return CheckpointVersionRef(key, version_id)


def _limits(pid: int) -> dict[str, list[int]]:
    observed = {
        "address_space": resource.prlimit(pid, resource.RLIMIT_AS),
        "cpu_seconds": resource.prlimit(pid, resource.RLIMIT_CPU),
        "file_descriptors": resource.prlimit(pid, resource.RLIMIT_NOFILE),
        "file_bytes": resource.prlimit(pid, resource.RLIMIT_FSIZE),
        "core_bytes": resource.prlimit(pid, resource.RLIMIT_CORE),
    }
    return {name: [int(values[0]), int(values[1])] for name, values in observed.items()}


def _memory_sample(pid: int) -> tuple[int, int] | None:
    try:
        lines = Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    values: dict[str, int] = {}
    for line in lines:
        name, separator, remainder = line.partition(":")
        if separator and name in {"VmRSS", "VmSize"}:
            amount, unit = remainder.split()
            assert unit == "kB"
            values[name] = int(amount)
    if set(values) != {"VmRSS", "VmSize"}:
        return None
    return values["VmRSS"], values["VmSize"]


def _exit_category(returncode: int) -> str:
    if returncode == 0:
        return "zero"
    if returncode < 0:
        return f"signal-{abs(returncode)}"
    return f"exit-{returncode}"


def _capture_public(
    operation: Callable[[], Any],
    *,
    spawn: Callable[[], subprocess.Popen[bytes]] | None = None,
    after_result: Callable[[], None] | None = None,
) -> tuple[Any | None, BaseException | None, dict[str, Any]]:
    original_spawn = isolated_raw._spawn_worker
    original_ready = isolated_raw._decode_ready
    original_result = isolated_raw._decode_result
    processes: list[subprocess.Popen[bytes]] = []
    effective: list[dict[str, list[int]]] = []
    worker_argv: list[bytes] = []
    worker_environment: set[bytes] = set()
    maximum_rss_kib = 0
    maximum_vms_kib = 0
    stop = threading.Event()
    samplers: list[threading.Thread] = []

    def sample(process: subprocess.Popen[bytes]) -> None:
        nonlocal maximum_rss_kib, maximum_vms_kib
        while not stop.wait(0.01):
            value = _memory_sample(process.pid)
            if value is not None:
                maximum_rss_kib = max(maximum_rss_kib, value[0])
                maximum_vms_kib = max(maximum_vms_kib, value[1])

    def observed_spawn() -> subprocess.Popen[bytes]:
        process = original_spawn() if spawn is None else spawn()
        processes.append(process)
        sampler = threading.Thread(target=sample, args=(process,))
        sampler.start()
        samplers.append(sampler)
        return process

    def observed_ready(payload: bytes) -> None:
        nonlocal maximum_rss_kib, maximum_vms_kib
        original_ready(payload)
        assert len(processes) == 1
        process = processes[0]
        effective.append(_limits(process.pid))
        memory = _memory_sample(process.pid)
        assert memory is not None
        maximum_rss_kib = max(maximum_rss_kib, memory[0])
        maximum_vms_kib = max(maximum_vms_kib, memory[1])
        worker_argv.extend(Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")[:-1])
        worker_environment.update(
            value
            for value in Path(f"/proc/{process.pid}/environ").read_bytes().split(b"\0")
            if value
        )

    def observed_result(
        payload: bytes,
        reference: CheckpointVersionRef,
    ) -> raw_transport.RawCheckpointVersion | raw_transport.RawVersionReadError:
        value = original_result(payload, reference)
        if after_result is not None:
            after_result()
        return value

    isolated_raw._spawn_worker = observed_spawn
    isolated_raw._decode_ready = observed_ready
    isolated_raw._decode_result = observed_result
    value: Any | None = None
    error: BaseException | None = None
    started = time.monotonic()
    try:
        try:
            value = operation()
        except BaseException as caught:  # noqa: BLE001 - report exact public outcome
            error = caught
    finally:
        isolated_raw._spawn_worker = original_spawn
        isolated_raw._decode_ready = original_ready
        isolated_raw._decode_result = original_result
        stop.set()
        for sampler in samplers:
            sampler.join(2)
            assert not sampler.is_alive()
    elapsed_ms = round((time.monotonic() - started) * 1_000)
    assert len(processes) == 1
    process = processes[0]
    assert process.returncode is not None
    assert process.stdin is not None and process.stdin.closed
    assert process.stdout is not None and process.stdout.closed
    assert not Path(f"/proc/{process.pid}").exists()
    assert effective == [_EXPECTED_LIMITS]
    environment = set(worker_environment)
    assert environment == {b"LANG=C.UTF-8", b"TZ=UTC"}
    combined = b"\0".join(worker_argv) + b"\0".join(environment)
    for secret in (
        os.environ["ISOLATED_ACCESS_KEY"],
        os.environ["ISOLATED_SECRET_KEY"],
        os.environ["AWS_ACCESS_KEY_ID"],
        os.environ["AWS_SECRET_ACCESS_KEY"],
        os.environ["HTTPS_PROXY"],
    ):
        assert secret.encode() not in combined
    assert 0 < maximum_vms_kib <= _ADDRESS_SPACE_BYTES // 1024
    assert 0 < maximum_rss_kib <= _ADDRESS_SPACE_BYTES // 1024
    return (
        value,
        error,
        {
            "elapsed_ms": elapsed_ms,
            "exit_category": _exit_category(process.returncode),
            "limits": effective[0],
            "maximum_rss_kib": maximum_rss_kib,
            "maximum_vms_kib": maximum_vms_kib,
            "pipes_closed": True,
            "reaped": True,
            "worker_environment_fixed": True,
            "worker_argv_private": True,
        },
    )


def _observation(
    item: dict[str, Any], reference: dict[str, Any], body_override: bytes | None = None
) -> bridge.LegacyObservation:
    witness = UUID(item["witness_id"])
    if item["kind"] == "gap":
        return bridge.WitnessCollectionGap(witness, item["reason"])
    locator = (witness, item["object_key"], item["version_id"])
    if item["kind"] == "body":
        body = bytes.fromhex(reference["legacy_vectors"][item["vector"]]["body_hex"])
        return bridge.LegacyBodyObservation(
            *locator, body if body_override is None else body_override
        )
    if item["kind"] == "unavailable":
        return bridge.LegacyUnavailableObservation(*locator)
    assert item["kind"] == "delete"
    return bridge.LegacyDeleteObservation(*locator)


def _bridge_case(
    reference: dict[str, Any],
    *,
    retained_body: bytes,
    changed: bool,
) -> bridge.BridgeEvaluation:
    package = reference["packages"]["transport"]
    external = copy.deepcopy(package["enrollment"])
    pin = external["bootstrap"]
    head = pin["audit_boundary"]
    required = external["required_checkpoint"]
    enrollment = bridge.BridgeEnrollment(
        StreamEnrollment(
            UUID(external["org_id"]),
            UUID(external["stream_id"]),
            BootstrapPin(
                pin["commitment_hash"],
                pin["initial_key_id"],
                bytes.fromhex(pin["initial_public_key_hex"]),
                pin["initial_key_epoch"],
                AuditHead(head["latest_id"], head["latest_row_hash"]),
            ),
            None
            if required is None
            else RequiredCheckpointPin(required["anchor_hash"], required["sequence"]),
        ),
        tuple(
            bridge.BridgeWitnessPin(UUID(item["witness_id"]), item["namespace_hash"])
            for item in external["witnesses"]
        ),
        tuple(
            bridge.LegacyPublicMaterial(item["key_id"], bytes.fromhex(item["public_key_hex"]))
            for item in external["legacy_keys"]
        ),
    )
    selected = next(
        index
        for index, item in enumerate(package["observations"])
        if item.get("vector") == "old_transport"
    )
    alternate = bytes.fromhex(reference["legacy_vectors"]["old"]["body_hex"])
    observations = tuple(
        _observation(
            item,
            reference,
            alternate
            if changed and index == selected
            else retained_body
            if index == selected
            else None,
        )
        for index, item in enumerate(package["observations"])
    )
    return bridge.evaluate_bootstrap_bridge(
        enrollment,
        bytes.fromhex(package["root_body_hex"]),
        tuple(
            bridge.BridgePageObservation(bytes.fromhex(item["body_hex"]))
            for item in package["pages"]
        ),
        observations,
        limits=bridge.BridgeLimits(4096, 16 * 1024 * 1024, 32),
    )


def _provider_and_bridge(config: dict[str, Any]) -> dict[str, Any]:
    reference = _load(config["fixture"])
    vector = reference["legacy_vectors"]["old_transport"]
    body = bytes.fromhex(vector["body_hex"])
    key = config["provider_key"]
    version_id = config["provider_version_id"]
    value, error, process = _capture_public(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            ExplicitHistoryReader(
                config["provider_endpoint"],
                config["provider_bucket"],
                "us-east-1",
                os.environ["ISOLATED_ACCESS_KEY"],
                os.environ["ISOLATED_SECRET_KEY"],
            ),
            _ref(key, version_id),
        )
    )
    assert error is None
    assert type(value) is raw_transport.RawCheckpointVersion
    assert value.key == key and value.version_id == version_id and value.body == body
    positive = _bridge_case(reference, retained_body=value.body, changed=False)
    altered = _bridge_case(reference, retained_body=value.body, changed=True)
    assert positive.status == "consistent"
    assert altered.status == "failed"
    assert {issue.code for issue in altered.issues} == {"LEGACY_BODY_COMMITMENT_MISMATCH"}
    return {
        "process": process,
        "retained": {
            "body_bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "exact_identity": True,
            "noncanonical": body != json.dumps(json.loads(body), separators=(",", ":")).encode(),
            "intended_destination": True,
        },
        "bridge": {"positive": positive.status, "altered": altered.status},
    }


def _tls_context(certificate: str, private_key: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    return context


def _expect_code(error: BaseException | None, code: str) -> raw_transport.RawVersionReadError:
    assert type(error) is raw_transport.RawVersionReadError
    assert error.code == code
    assert error.__cause__ is None and error.__suppress_context__ is True
    return error


def _tls_and_routing(config: dict[str, Any]) -> dict[str, Any]:
    key = "opaque/%2F+snow/../q?#fragment"
    version_id = "v%2F+opaque/1"
    with _server(
        payload=b"trusted",
        version_id=version_id,
        tls=_tls_context(config["trusted_certificate"], config["trusted_private_key"]),
    ) as server:
        value, error, trusted_process = _capture_public(
            lambda: isolated_raw.read_raw_checkpoint_version_isolated(
                _reader(server, tls=True), _ref(key, version_id)
            )
        )
        assert error is None and type(value) is raw_transport.RawCheckpointVersion
        assert value.body == b"trusted"
        assert len(server.requests) == 1
        assert server.requests[0]["target"] == (
            f"/runtime-bucket/{quote(key, safe='/~')}?versionId={quote(version_id, safe='-_.~')}"
        )
    with _server(
        payload=b"untrusted",
        version_id=version_id,
        tls=_tls_context(config["untrusted_certificate"], config["untrusted_private_key"]),
    ) as server:
        value, error, untrusted_process = _capture_public(
            lambda: isolated_raw.read_raw_checkpoint_version_isolated(
                _reader(server, tls=True), _ref(key, version_id)
            )
        )
        assert value is None
        _expect_code(error, "TRANSPORT_FAILURE")
        assert server.requests == []
    with _server() as target, _server(route="redirect") as original:
        original.redirect = f"http://127.0.0.1:{target.server_port}/forbidden"
        value, error, redirect_process = _capture_public(
            lambda: isolated_raw.read_raw_checkpoint_version_isolated(
                _reader(original), _ref("redirect-key", "redirect-version")
            )
        )
        assert value is None
        _expect_code(error, "ROUTING_REJECTED")
        assert len(original.requests) == 1
        assert target.requests == []
    return {
        "trusted": True,
        "untrusted_rejected": True,
        "opaque_target_exact": True,
        "redirect_target_requests": 0,
        "processes": [trusted_process, untrusted_process, redirect_process],
    }


def _streamed_error() -> dict[str, Any]:
    with _server(route="streamed-error") as server:
        value, error, process = _capture_public(
            lambda: isolated_raw.read_raw_checkpoint_version_isolated(
                _reader(server), _ref("large-error", "large-error-version")
            )
        )
        assert value is None
        assert type(error) is isolated_raw.IsolatedRawReadError and error.code == "WORKER_FAILED"
        assert 0 < server.bytes_sent <= _STREAMED_ERROR_BYTES
        assert server.maximum_chunk <= _CHUNK_BYTES
    sent = server.bytes_sent
    maximum_chunk = server.maximum_chunk
    peer_disconnect = server.peer_disconnect
    assert peer_disconnect in {"BrokenPipeError", "ConnectionResetError", "PeerEOF"}
    with _server(payload=b"parent-survived") as server:
        value, error, survivor = _capture_public(
            lambda: isolated_raw.read_raw_checkpoint_version_isolated(
                _reader(server), _ref("survivor", "runtime-version")
            )
        )
        assert error is None and type(value) is raw_transport.RawCheckpointVersion
        assert value.body == b"parent-survived"
    return {
        "planned_bytes": _STREAMED_ERROR_BYTES,
        "sent_bytes": sent,
        "maximum_chunk_bytes": maximum_chunk,
        "outcome": "WORKER_FAILED",
        "peer_close": peer_disconnect,
        "worker": process,
        "parent_survival_worker": survivor,
    }


_STALL_SCRIPT = r"""
import base64
import json
import resource
import sys
import time

def frame(value):
    payload = json.dumps(value, separators=(",", ":")).encode()
    return len(payload).to_bytes(4, "big") + payload

ready = {
    "version": 1,
    "status": "ready",
    "address_space": 536870912,
    "cpu_seconds": 10,
    "file_descriptors": 64,
    "file_bytes": 0,
    "core_bytes": 0,
}
for limit, value in (
    (resource.RLIMIT_AS, 536870912),
    (resource.RLIMIT_CPU, 10),
    (resource.RLIMIT_NOFILE, 64),
    (resource.RLIMIT_FSIZE, 0),
    (resource.RLIMIT_CORE, 0),
):
    resource.setrlimit(limit, (value, value))
sys.stdout.buffer.write(frame(ready))
sys.stdout.buffer.flush()
declared = int.from_bytes(sys.stdin.buffer.read(4), "big")
request = json.loads(sys.stdin.buffer.read(declared))
assert sys.stdin.buffer.read(1) == b""
result = {
    "version": 1,
    "status": "ok",
    "key": request["key"],
    "version_id": request["version_id"],
    "body_base64": base64.b64encode(b"late-result").decode(),
}
sys.stdout.buffer.write(frame(result))
sys.stdout.buffer.flush()
time.sleep(60)
"""


def _spawn_stall() -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed finite owned test protocol fixture
        [sys.executable, "-I", "-B", "-u", "-c", _STALL_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
        bufsize=0,
    )


def _deadlines() -> dict[str, Any]:
    trickle_payload = b"trickle"
    with _server(route="trickle", payload=trickle_payload) as server:
        value, error, trickle = _capture_public(
            lambda: isolated_raw.read_raw_checkpoint_version_isolated(
                _reader(server), _ref("trickle", "runtime-version")
            )
        )
        assert value is None
        assert type(error) is isolated_raw.IsolatedRawReadError
        assert error.code == "DEADLINE_EXCEEDED"
        assert trickle["elapsed_ms"] >= 19_000
        assert trickle["elapsed_ms"] <= 24_000
        assert server.bytes_sent >= 2
    trickle_sent = server.bytes_sent
    trickle_peer_close = server.peer_disconnect
    assert trickle_peer_close in {"BrokenPipeError", "ConnectionResetError", "PeerEOF"}

    cancel = threading.Event()
    result_seen = threading.Event()

    def cancel_after_result() -> None:
        result_seen.set()
        cancel.set()

    value, error, stalled = _capture_public(
        lambda: isolated_raw.read_raw_checkpoint_version_isolated(
            ExplicitHistoryReader(
                "http://127.0.0.1:1",
                "runtime-bucket",
                "us-east-1",
                os.environ["ISOLATED_ACCESS_KEY"],
                os.environ["ISOLATED_SECRET_KEY"],
            ),
            _ref("stall-result", "stall-version"),
            cancel=cancel,
        ),
        spawn=_spawn_stall,
        after_result=cancel_after_result,
    )
    assert value is None
    assert type(error) is raw_transport.RawVersionReadCancelled
    assert result_seen.is_set()
    assert stalled["elapsed_ms"] < 3_000
    return {
        "sdk_read_timeout_seconds": 5,
        "trickle_interval_ms": 3_500,
        "trickle_bytes_sent": trickle_sent,
        "trickle_peer_close": trickle_peer_close,
        "watchdog": trickle,
        "post_result_cancellation": stalled,
        "valid_result_observed_before_cancel": True,
    }


_LIMIT_PREAMBLE = r"""
import errno
import importlib.util
import json
import os
import resource
import signal
import sys

worker_path = sys.argv[1]
assert not any(name == "easysynq_api" or name.startswith("easysynq_api.") for name in sys.modules)
spec = importlib.util.spec_from_file_location("isolated_actual_image_limit_probe", worker_path)
assert spec is not None and spec.loader is not None
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
assert not any(name == "easysynq_api" or name.startswith("easysynq_api.") for name in sys.modules)
worker._apply_limits()
limits = {
    "address_space": resource.getrlimit(resource.RLIMIT_AS),
    "cpu_seconds": resource.getrlimit(resource.RLIMIT_CPU),
    "file_descriptors": resource.getrlimit(resource.RLIMIT_NOFILE),
    "file_bytes": resource.getrlimit(resource.RLIMIT_FSIZE),
    "core_bytes": resource.getrlimit(resource.RLIMIT_CORE),
}
print(json.dumps({"limits": limits}), flush=True)
"""


def _spawn_limit(mode: str, writable: str) -> subprocess.Popen[bytes]:
    worker = str(Path(isolated_raw.__file__).with_name("_isolated_raw_worker.py"))
    if mode == "memory":
        body = r"""
try:
    bytearray(600_000_000)
except MemoryError:
    print(json.dumps({"memory": "MemoryError"}), flush=True)
else:
    raise SystemExit(31)
"""
    elif mode == "file":
        body = r"""
target = os.path.join(sys.argv[2], "limited.bin")
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
try:
    with open(target, "wb", buffering=0) as output:
        output.write(b"x")
except OSError as error:
    print(json.dumps({"file_errno": error.errno, "file_size": os.path.getsize(target)}), flush=True)
else:
    raise SystemExit(32)
"""
    else:
        assert mode == "cpu"
        body = r"""
print(json.dumps({"cpu": "ready"}), flush=True)
value = 0
for _ in range(1_000_000_000_000):
    value = (value + 1) % 1000003
"""
    return subprocess.Popen(  # noqa: S603 - fixed finite owned resource probe
        [sys.executable, "-I", "-B", "-u", "-c", _LIMIT_PREAMBLE + body, worker, writable],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        start_new_session=True,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
    )


def _communicate(
    process: subprocess.Popen[bytes], *, timeout: float
) -> tuple[list[dict[str, Any]], bytes, int]:
    started = time.monotonic()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
        raise
    elapsed_ms = round((time.monotonic() - started) * 1_000)
    assert process.returncode is not None
    assert not Path(f"/proc/{process.pid}").exists()
    values = [json.loads(line) for line in stdout.splitlines()]
    assert all(type(value) is dict for value in values)
    return values, stderr, elapsed_ms


def _resource_limits(config: dict[str, Any]) -> dict[str, Any]:
    writable = Path(config["writable"])
    control = writable / "write-control.bin"
    control.write_bytes(b"writable")
    assert control.read_bytes() == b"writable"
    control.unlink()

    memory = _spawn_limit("memory", str(writable))
    memory_values, memory_stderr, memory_ms = _communicate(memory, timeout=8)
    assert memory.returncode == 0, memory_stderr.decode(errors="replace")
    assert memory_values == [{"limits": _EXPECTED_LIMITS}, {"memory": "MemoryError"}]

    file_process = _spawn_limit("file", str(writable))
    file_values, file_stderr, file_ms = _communicate(file_process, timeout=5)
    assert file_process.returncode == 0, file_stderr.decode(errors="replace")
    assert file_values == [
        {"limits": _EXPECTED_LIMITS},
        {"file_errno": errno.EFBIG, "file_size": 0},
    ]
    assert (writable / "limited.bin").stat().st_size == 0
    (writable / "limited.bin").unlink()

    cpu = _spawn_limit("cpu", str(writable))
    cpu_values, cpu_stderr, cpu_ms = _communicate(cpu, timeout=15)
    assert cpu_values == [{"limits": _EXPECTED_LIMITS}, {"cpu": "ready"}], cpu_stderr.decode(
        errors="replace"
    )
    assert cpu.returncode in {-signal.SIGXCPU, -signal.SIGKILL}
    assert cpu_ms >= 8_000
    return {
        "effective": _EXPECTED_LIMITS,
        "writable_mount_control": True,
        "address_space": {
            "outcome": "MemoryError",
            "elapsed_ms": memory_ms,
            "exit_category": _exit_category(memory.returncode),
            "reaped": True,
        },
        "file_growth": {
            "errno": errno.EFBIG,
            "size": 0,
            "elapsed_ms": file_ms,
            "exit_category": _exit_category(file_process.returncode),
            "reaped": True,
        },
        "cpu": {
            "elapsed_ms": cpu_ms,
            "exit_category": _exit_category(cpu.returncode),
            "reaped": True,
        },
    }


def _all(config: dict[str, Any]) -> dict[str, Any]:
    original_session = boto3.DEFAULT_SESSION
    try:
        boto3.setup_default_session(
            aws_access_key_id="cached-default-hostile-access",
            aws_secret_access_key="cached-default-hostile-secret",
            region_name="ap-southeast-2",
        )
        provider = _provider_and_bridge(config)
        routing = _tls_and_routing(config)
        streamed = _streamed_error()
        deadlines = _deadlines()
        limits = _resource_limits(config)
    finally:
        boto3.DEFAULT_SESSION = original_session
    assert boto3.DEFAULT_SESSION is original_session
    return {
        "uid": os.getuid(),
        "provider": provider,
        "routing": routing,
        "streamed": streamed,
        "deadlines": deadlines,
        "limits": limits,
        "cleanup_complete": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("all", "certifi"))
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    config = _load(arguments.config)
    value = (
        _all(config)
        if arguments.mode == "all"
        else {
            "certifi_path": certifi.where(),
            "certifi_sha256": hashlib.sha256(Path(certifi.where()).read_bytes()).hexdigest(),
        }
    )
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
