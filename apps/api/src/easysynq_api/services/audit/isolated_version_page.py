"""Bounded executed-process boundary for one original checkpoint version page."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from . import version_page, version_page_transport
from .sink import ExplicitHistoryReader
from .version_page_transport import (
    RawCheckpointVersionPage,
    VersionPageReadCancelled,
    VersionPageReadError,
)

_ADDRESS_SPACE_BYTES = 536_870_912
_CPU_SECONDS = 20
_FILE_DESCRIPTORS = 64
_FILE_BYTES = 0
_CORE_BYTES = 0
_REQUEST_MAX_BYTES = 131_072
_READY_MAX_BYTES = 512
_RESULT_MAX_BYTES = 16_777_220
_BODY_MAX_BYTES = 16_777_216
_DEADLINE_SECONDS = 30.0
_POLL_SECONDS = 0.05
_REAP_SECONDS = 2.0
_IO_CHUNK_BYTES = 8_192
_OPERATION = "checkpoint-version-page"
_ERROR_TEXT = "isolated checkpoint version page read failed"
_GROUP_TEXT = "isolated checkpoint version page operation and cleanup failed"
_ERROR_CODES = frozenset(
    {
        "RUNTIME_UNSUPPORTED",
        "WORKER_START_FAILED",
        "WORKER_FAILED",
        "PROTOCOL_INVALID",
        "OUTPUT_LIMIT",
        "DEADLINE_EXCEEDED",
        "CLEANUP_FAILED",
    }
)
_REQUEST_FIELDS = frozenset(
    {
        "version",
        "operation",
        "endpoint",
        "bucket",
        "region",
        "access_key",
        "secret_key",
        "org_id",
        "key_marker",
        "version_id_marker",
    }
)
_monotonic = time.monotonic
_killpg = os.killpg
_read_fd = os.read
_write_fd = os.write


class IsolatedVersionPageError(Exception):
    """A fixed, secret-free process, protocol, deadline, or cleanup failure."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError("invalid isolated checkpoint version page read code")
        self.code = code
        super().__init__(_ERROR_TEXT)


def _isolated_error(code: str) -> IsolatedVersionPageError:
    error = IsolatedVersionPageError(code)
    error.__suppress_context__ = True
    return error


def _runtime_supported() -> bool:
    return (
        sys.platform.startswith("linux") and hasattr(os, "killpg") and hasattr(os, "set_blocking")
    )


def _spawn_worker() -> subprocess.Popen[bytes]:
    worker = Path(__file__).with_name("_isolated_version_page_worker.py").resolve()
    source_root = Path(__file__).resolve().parents[3]
    return subprocess.Popen(  # noqa: S603 - fixed interpreter, flags, worker, and source path
        [sys.executable, "-I", "-B", "-u", str(worker), str(source_root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
        start_new_session=True,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
        bufsize=0,
    )


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("nonfinite JSON scalar")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON property")
        value[key] = item
    return value


def _check_json_depth(text: str) -> None:
    depth = 0
    quoted = False
    escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > 2:
                raise ValueError("JSON nesting exceeds protocol limit")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("invalid JSON nesting")


def _decode_json(payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8", errors="strict")
    _check_json_depth(text)
    value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if type(value) is not dict:
        raise ValueError("protocol payload is not an object")
    return value


def _encode_payload(value: Mapping[str, Any], maximum: int) -> bytes:
    payload = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(payload) > maximum:
        raise _isolated_error("PROTOCOL_INVALID")
    return payload


def _frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def _request_payload(
    reader: ExplicitHistoryReader,
    org_id: uuid.UUID,
    key_marker: str | None,
    version_id_marker: str | None,
) -> bytes:
    return _encode_payload(
        {
            "version": 1,
            "operation": _OPERATION,
            "endpoint": reader.endpoint,
            "bucket": reader.bucket,
            "region": reader.region,
            "access_key": reader.access_key,
            "secret_key": reader.secret_key,
            "org_id": str(org_id),
            "key_marker": key_marker,
            "version_id_marker": version_id_marker,
        },
        _REQUEST_MAX_BYTES,
    )


def _decode_request(
    payload: bytes,
) -> tuple[ExplicitHistoryReader, uuid.UUID, str | None, str | None]:
    if len(payload) > _REQUEST_MAX_BYTES:
        raise ValueError("request exceeds protocol limit")
    value = _decode_json(payload)
    if set(value) != _REQUEST_FIELDS or type(value["version"]) is not int:
        raise ValueError("invalid request schema")
    if value["version"] != 1 or value["operation"] != _OPERATION:
        raise ValueError("invalid request operation")
    strings = _REQUEST_FIELDS - {"version", "key_marker", "version_id_marker"}
    if any(type(value[field]) is not str for field in strings):
        raise ValueError("invalid request field types")
    if any(
        value[field] is not None and type(value[field]) is not str
        for field in ("key_marker", "version_id_marker")
    ):
        raise ValueError("invalid cursor types")
    org_id = uuid.UUID(value["org_id"])
    if str(org_id) != value["org_id"]:
        raise ValueError("noncanonical organization")
    reader = ExplicitHistoryReader(
        value["endpoint"],
        value["bucket"],
        value["region"],
        value["access_key"],
        value["secret_key"],
    )
    key_marker, version_id_marker = value["key_marker"], value["version_id_marker"]
    version_page_transport._validate_inputs(reader, org_id, key_marker, version_id_marker, None)
    return reader, org_id, key_marker, version_id_marker


def _ready_values() -> dict[str, Any]:
    return {
        "version": 1,
        "operation": _OPERATION,
        "status": "ready",
        "address_space": _ADDRESS_SPACE_BYTES,
        "cpu_seconds": _CPU_SECONDS,
        "file_descriptors": _FILE_DESCRIPTORS,
        "file_bytes": _FILE_BYTES,
        "core_bytes": _CORE_BYTES,
    }


def _ready_payload() -> bytes:
    return _encode_payload(_ready_values(), _READY_MAX_BYTES)


def _decode_ready(payload: bytes) -> None:
    try:
        value = _decode_json(payload)
    except (UnicodeError, ValueError, TypeError):
        raise _isolated_error("PROTOCOL_INVALID") from None
    expected = _ready_values()
    if set(value) != set(expected):
        raise _isolated_error("PROTOCOL_INVALID")
    if any(type(value[key]) is not type(expected[key]) for key in expected) or value != expected:
        raise _isolated_error("PROTOCOL_INVALID")


def _result_payload(result: RawCheckpointVersionPage | VersionPageReadError) -> bytes:
    if isinstance(result, VersionPageReadError):
        return b"VP1\x01" + result.code.encode("ascii")
    if type(result.body) is not bytes or len(result.body) > _BODY_MAX_BYTES:
        raise ValueError("invalid result body")
    return b"VP1\x00" + result.body


def _decode_result(
    payload: bytes,
    reader: ExplicitHistoryReader,
    org_id: uuid.UUID,
    key_marker: str | None,
    version_id_marker: str | None,
) -> RawCheckpointVersionPage | VersionPageReadError:
    if payload[:4] == b"VP1\x01":
        try:
            code = payload[4:].decode("ascii")
        except UnicodeError:
            raise _isolated_error("PROTOCOL_INVALID") from None
        if code not in version_page_transport._READ_CODES:
            raise _isolated_error("PROTOCOL_INVALID")
        return version_page_transport._read_error(code)
    if payload[:4] != b"VP1\x00" or len(payload) > _RESULT_MAX_BYTES:
        raise _isolated_error("PROTOCOL_INVALID")
    body = payload[4:]
    try:
        page = version_page.decode_checkpoint_version_page(
            body,
            bucket=reader.bucket,
            org_id=org_id,
            key_marker=key_marker,
            version_id_marker=version_id_marker,
        )
    except version_page.CheckpointVersionPageDecodeError:
        raise _isolated_error("PROTOCOL_INVALID") from None
    return RawCheckpointVersionPage(body, page)


def _checkpoint(cancel: threading.Event | None, deadline: float) -> None:
    if cancel is not None and cancel.is_set():
        raise VersionPageReadCancelled()
    if _monotonic() >= deadline:
        raise _isolated_error("DEADLINE_EXCEEDED")


def _select(
    selected: selectors.BaseSelector, cancel: threading.Event | None, deadline: float
) -> list[tuple[selectors.SelectorKey, int]]:
    _checkpoint(cancel, deadline)
    events = selected.select(timeout=max(0.0, min(_POLL_SECONDS, deadline - _monotonic())))
    _checkpoint(cancel, deadline)
    return events


def _inspect_frame(buffer: bytearray, maximum: int) -> bytes | None:
    if len(buffer) < 4:
        return None
    declared = int.from_bytes(buffer[:4], "big")
    if declared > maximum:
        raise _isolated_error("OUTPUT_LIMIT")
    end = 4 + declared
    if len(buffer) > end:
        raise _isolated_error("PROTOCOL_INVALID")
    return bytes(buffer[4:]) if len(buffer) == end else None


def _read_frame_piece(fd: int, buffer: bytearray, maximum: int) -> tuple[bytes | None, bool]:
    allowance = 4 + maximum + 1 - len(buffer)
    if allowance <= 0:
        raise _isolated_error("OUTPUT_LIMIT")
    try:
        chunk = _read_fd(fd, min(_IO_CHUNK_BYTES, allowance))
    except BlockingIOError:
        return _inspect_frame(buffer, maximum), False
    if not chunk:
        return _inspect_frame(buffer, maximum), True
    buffer.extend(chunk)
    return _inspect_frame(buffer, maximum), False


def _receive_ready(
    selected: selectors.BaseSelector,
    stdout_fd: int,
    cancel: threading.Event | None,
    deadline: float,
) -> bytes:
    buffer = bytearray()
    while True:
        for key, events in _select(selected, cancel, deadline):
            if key.fd != stdout_fd or not events & selectors.EVENT_READ:
                continue
            payload, eof = _read_frame_piece(stdout_fd, buffer, _READY_MAX_BYTES)
            if payload is not None:
                return payload
            if eof:
                raise _isolated_error("PROTOCOL_INVALID" if buffer else "WORKER_FAILED")


def _close_stdin(
    selected: selectors.BaseSelector, process: subprocess.Popen[bytes], stdin_fd: int
) -> None:
    try:
        selected.unregister(stdin_fd)
    except KeyError:
        pass
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()


def _drive_request_and_result(
    selected: selectors.BaseSelector,
    process: subprocess.Popen[bytes],
    stdin_fd: int,
    stdout_fd: int,
    request: bytes,
    cancel: threading.Event | None,
    deadline: float,
) -> bytes:
    selected.register(stdin_fd, selectors.EVENT_WRITE)
    sent = 0
    output = bytearray()
    result: bytes | None = None
    stdout_eof = False
    while True:
        # Inspect readable output before writable input within a single poll. An
        # already-available result must not be legalized by sending credentials.
        events_ready = _select(selected, cancel, deadline)
        events_ready.sort(key=lambda event: event[0].fd != stdout_fd)
        for key, events in events_ready:
            if key.fd == stdout_fd and events & selectors.EVENT_READ:
                if result is not None:
                    try:
                        trailing = _read_fd(stdout_fd, 1)
                    except BlockingIOError:
                        trailing = None
                    if trailing:
                        raise _isolated_error("PROTOCOL_INVALID")
                    if trailing == b"":
                        stdout_eof = True
                        selected.unregister(stdout_fd)
                else:
                    previous = len(output)
                    candidate, eof = _read_frame_piece(stdout_fd, output, _RESULT_MAX_BYTES)
                    if len(output) > previous and sent != len(request):
                        raise _isolated_error("PROTOCOL_INVALID")
                    if candidate is not None:
                        result = candidate
                    if eof:
                        if result is None:
                            raise _isolated_error("PROTOCOL_INVALID" if output else "WORKER_FAILED")
                        stdout_eof = True
                        selected.unregister(stdout_fd)
            if key.fd == stdin_fd and events & selectors.EVENT_WRITE:
                try:
                    written = _write_fd(stdin_fd, request[sent : sent + _IO_CHUNK_BYTES])
                except BlockingIOError:
                    continue
                except BrokenPipeError:
                    raise _isolated_error("WORKER_FAILED") from None
                if type(written) is not int or not 0 < written <= min(
                    _IO_CHUNK_BYTES, len(request) - sent
                ):
                    raise _isolated_error("PROTOCOL_INVALID")
                sent += written
                if sent == len(request):
                    _close_stdin(selected, process, stdin_fd)
        if stdout_eof:
            status = process.poll()
            if status is not None:
                if result is None or sent != len(request) or status != 0:
                    raise _isolated_error("WORKER_FAILED")
                return result


def _run_protocol(
    selected: selectors.BaseSelector,
    process: subprocess.Popen[bytes],
    request: bytes,
    cancel: threading.Event | None,
    deadline: float,
) -> bytes:
    stdin, stdout = process.stdin, process.stdout
    if stdin is None or stdout is None:
        raise RuntimeError("isolated worker pipes were not created")
    stdin_fd, stdout_fd = stdin.fileno(), stdout.fileno()
    os.set_blocking(stdin_fd, False)
    os.set_blocking(stdout_fd, False)
    selected.register(stdout_fd, selectors.EVENT_READ)
    _decode_ready(_receive_ready(selected, stdout_fd, cancel, deadline))
    _checkpoint(cancel, deadline)
    return _drive_request_and_result(
        selected, process, stdin_fd, stdout_fd, _frame(request), cancel, deadline
    )


def _cleanup_worker(
    selected: selectors.BaseSelector | None,
    process: subprocess.Popen[bytes] | None,
    *,
    operation_failed: bool,
) -> list[BaseException]:
    outcomes: list[BaseException] = []
    fixed_failure = False

    def retain(error: BaseException, *, reaping_unconfirmed: bool = False) -> None:
        nonlocal fixed_failure
        if isinstance(error, OSError):
            fixed_failure = True
        else:
            outcomes.append(error)
        fixed_failure |= reaping_unconfirmed

    if selected is not None:
        try:
            selected.close()
        except BaseException as error:  # noqa: BLE001 - cleanup must retain fatal identities
            retain(error)
    if process is not None:
        # A known return code means wait/poll has released the PID. Never signal
        # that PID again: it may already name an unrelated process group.
        if process.returncode is None:
            if not operation_failed:
                fixed_failure = True
            try:
                _killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as error:  # noqa: BLE001 - always attempt reaping
                retain(error)
            try:
                process.wait(timeout=_REAP_SECONDS)
            except subprocess.TimeoutExpired:
                fixed_failure = True
            except BaseException as error:  # noqa: BLE001 - retain identity and unreaped status
                retain(error, reaping_unconfirmed=True)
        for stream in (process.stdin, process.stdout):
            if stream is None or stream.closed:
                continue
            try:
                stream.close()
            except BaseException as error:  # noqa: BLE001 - every owned close is attempted
                retain(error)
    if fixed_failure:
        outcomes.append(_isolated_error("CLEANUP_FAILED"))
    return outcomes


def _raise_outcomes(outcomes: Sequence[BaseException]) -> NoReturn:
    if len(outcomes) == 1:
        raise outcomes[0]
    raise BaseExceptionGroup(_GROUP_TEXT, list(outcomes))


def _is_controlled(error: BaseException) -> bool:
    return isinstance(
        error, (IsolatedVersionPageError, VersionPageReadError, VersionPageReadCancelled)
    )


def read_raw_checkpoint_version_page_isolated(
    reader: ExplicitHistoryReader,
    org_id: uuid.UUID,
    *,
    key_marker: str | None = None,
    version_id_marker: str | None = None,
    cancel: threading.Event | None = None,
) -> RawCheckpointVersionPage:
    """Admit one original page only after its fresh worker and cleanup finish."""
    version_page_transport._validate_inputs(reader, org_id, key_marker, version_id_marker, cancel)
    deadline = _monotonic() + _DEADLINE_SECONDS
    _checkpoint(cancel, deadline)
    if not _runtime_supported():
        raise _isolated_error("RUNTIME_UNSUPPORTED")
    request = _request_payload(reader, org_id, key_marker, version_id_marker)
    _checkpoint(cancel, deadline)
    selected: selectors.BaseSelector | None = None
    process: subprocess.Popen[bytes] | None = None
    primary: BaseException | None = None
    result: RawCheckpointVersionPage | None = None
    checkpoints: list[BaseException] = []

    def observe() -> None:
        try:
            _checkpoint(cancel, deadline)
        except BaseException as error:  # noqa: BLE001 - an observation cannot prevent cleanup
            checkpoints.append(error)

    try:
        try:
            process = _spawn_worker()
        except OSError:
            raise _isolated_error("WORKER_START_FAILED") from None
        _checkpoint(cancel, deadline)
        selected = selectors.DefaultSelector()
        payload = _run_protocol(selected, process, request, cancel, deadline)
        outcome = _decode_result(payload, reader, org_id, key_marker, version_id_marker)
        if isinstance(outcome, VersionPageReadError):
            primary = outcome
        else:
            result = outcome
    except BaseException as error:  # noqa: BLE001 - preserve unexpected parent exception identity
        primary = error
    observe()
    cleanup = _cleanup_worker(
        selected, process, operation_failed=primary is not None or bool(checkpoints)
    )
    observe()
    cancellation = next(
        (fault for fault in checkpoints if isinstance(fault, VersionPageReadCancelled)), None
    )
    expired = any(
        isinstance(fault, IsolatedVersionPageError) and fault.code == "DEADLINE_EXCEEDED"
        for fault in checkpoints
    )
    outcomes: list[BaseException] = []
    if primary is not None and not _is_controlled(primary):
        outcomes.append(primary)
        if cancellation is not None:
            outcomes.append(cancellation)
    elif cancellation is not None:
        outcomes.append(primary if isinstance(primary, VersionPageReadCancelled) else cancellation)
    elif expired and (primary is None or isinstance(primary, VersionPageReadError)):
        outcomes.append(_isolated_error("DEADLINE_EXCEEDED"))
    elif primary is not None:
        outcomes.append(primary)
    outcomes.extend(cleanup)
    for fault in checkpoints:
        if not _is_controlled(fault) and not any(fault is existing for existing in outcomes):
            outcomes.append(fault)
    if outcomes:
        _raise_outcomes(outcomes)
    if result is None:
        raise RuntimeError("isolated checkpoint version page read produced no outcome")
    return result
