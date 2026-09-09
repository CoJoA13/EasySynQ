"""Resource-constrained process boundary for one exact retained-version read."""

from __future__ import annotations

import base64
import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from . import raw_transport
from .raw_transport import (
    RawCheckpointVersion,
    RawVersionReadCancelled,
    RawVersionReadError,
)
from .sink import CheckpointVersionRef, ExplicitHistoryReader

_ADDRESS_SPACE_BYTES = 536_870_912
_CPU_SECONDS = 10
_FILE_DESCRIPTORS = 64
_FILE_BYTES = 0
_CORE_BYTES = 0
_REQUEST_MAX_BYTES = 131_072
_READY_MAX_BYTES = 512
_RESULT_MAX_BYTES = 131_072
_BODY_MAX_BYTES = 65_536
_DEADLINE_SECONDS = 20.0
_POLL_SECONDS = 0.05
_REAP_SECONDS = 2.0
_IO_CHUNK_BYTES = 8_192
_ERROR_TEXT = "isolated raw checkpoint read failed"
_GROUP_TEXT = "isolated raw checkpoint operation and cleanup failed"
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
        "endpoint",
        "bucket",
        "region",
        "access_key",
        "secret_key",
        "key",
        "version_id",
    }
)
_READY_FIELDS = frozenset(
    {
        "version",
        "status",
        "address_space",
        "cpu_seconds",
        "file_descriptors",
        "file_bytes",
        "core_bytes",
    }
)
_OK_FIELDS = frozenset({"version", "status", "key", "version_id", "body_base64"})
_READ_ERROR_FIELDS = frozenset({"version", "status", "code"})

_monotonic = time.monotonic
_killpg = os.killpg
_read_fd = os.read
_write_fd = os.write


class IsolatedRawReadError(Exception):
    """A fixed, secret-free process, protocol, deadline, or cleanup failure."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("invalid isolated raw checkpoint read code")
        self.code = code
        super().__init__(_ERROR_TEXT)


def _isolated_error(code: str) -> IsolatedRawReadError:
    error = IsolatedRawReadError(code)
    error.__suppress_context__ = True
    return error


def _runtime_supported() -> bool:
    return all(
        (
            sys.platform.startswith("linux"),
            hasattr(os, "killpg"),
            hasattr(os, "set_blocking"),
        )
    )


def _spawn_worker() -> subprocess.Popen[bytes]:
    worker = Path(__file__).with_name("_isolated_raw_worker.py").resolve()
    source_root = Path(__file__).resolve().parents[3]
    return subprocess.Popen(  # noqa: S603 - fixed interpreter, flags, script, and source root
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
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


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
            continue
        if character == '"':
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
    value = json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if type(value) is not dict:
        raise ValueError("protocol payload is not an object")
    return value


def _encode_payload(value: Mapping[str, Any], maximum: int) -> bytes:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > maximum:
        raise _isolated_error("PROTOCOL_INVALID")
    return payload


def _frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def _request_payload(reader: ExplicitHistoryReader, ref: CheckpointVersionRef) -> bytes:
    return _encode_payload(
        {
            "version": 1,
            "endpoint": reader.endpoint,
            "bucket": reader.bucket,
            "region": reader.region,
            "access_key": reader.access_key,
            "secret_key": reader.secret_key,
            "key": ref.key,
            "version_id": ref.version_id,
        },
        _REQUEST_MAX_BYTES,
    )


def _decode_request(payload: bytes) -> tuple[ExplicitHistoryReader, CheckpointVersionRef]:
    value = _decode_json(payload)
    if set(value) != _REQUEST_FIELDS or type(value["version"]) is not int:
        raise ValueError("invalid request schema")
    if value["version"] != 1 or any(
        type(value[field]) is not str for field in _REQUEST_FIELDS - {"version"}
    ):
        raise ValueError("invalid request values")
    reader = ExplicitHistoryReader(
        value["endpoint"],
        value["bucket"],
        value["region"],
        value["access_key"],
        value["secret_key"],
    )
    ref = CheckpointVersionRef(value["key"], value["version_id"])
    raw_transport._validate_inputs(reader, ref, None)
    return reader, ref


def _ready_payload() -> bytes:
    return _encode_payload(
        {
            "version": 1,
            "status": "ready",
            "address_space": _ADDRESS_SPACE_BYTES,
            "cpu_seconds": _CPU_SECONDS,
            "file_descriptors": _FILE_DESCRIPTORS,
            "file_bytes": _FILE_BYTES,
            "core_bytes": _CORE_BYTES,
        },
        _READY_MAX_BYTES,
    )


def _decode_ready(payload: bytes) -> None:
    try:
        value = _decode_json(payload)
    except (UnicodeError, ValueError, TypeError):
        raise _isolated_error("PROTOCOL_INVALID") from None
    expected = {
        "version": 1,
        "status": "ready",
        "address_space": _ADDRESS_SPACE_BYTES,
        "cpu_seconds": _CPU_SECONDS,
        "file_descriptors": _FILE_DESCRIPTORS,
        "file_bytes": _FILE_BYTES,
        "core_bytes": _CORE_BYTES,
    }
    if set(value) != _READY_FIELDS:
        raise _isolated_error("PROTOCOL_INVALID")
    if any(type(value[key]) is not type(expected[key]) for key in expected):
        raise _isolated_error("PROTOCOL_INVALID")
    if value != expected:
        raise _isolated_error("PROTOCOL_INVALID")


def _result_payload(result: RawCheckpointVersion | RawVersionReadError) -> bytes:
    if isinstance(result, RawCheckpointVersion):
        value: Mapping[str, Any] = {
            "version": 1,
            "status": "ok",
            "key": result.key,
            "version_id": result.version_id,
            "body_base64": base64.b64encode(result.body).decode("ascii"),
        }
    else:
        value = {"version": 1, "status": "read-error", "code": result.code}
    return _encode_payload(value, _RESULT_MAX_BYTES)


def _decode_result(
    payload: bytes,
    ref: CheckpointVersionRef,
) -> RawCheckpointVersion | RawVersionReadError:
    try:
        value = _decode_json(payload)
    except (UnicodeError, ValueError, TypeError):
        raise _isolated_error("PROTOCOL_INVALID") from None
    if type(value.get("version")) is not int or value.get("version") != 1:
        raise _isolated_error("PROTOCOL_INVALID")
    status = value.get("status")
    if status == "read-error":
        code = value.get("code")
        if set(value) != _READ_ERROR_FIELDS or type(code) is not str:
            raise _isolated_error("PROTOCOL_INVALID")
        if code not in raw_transport._READ_CODES:
            raise _isolated_error("PROTOCOL_INVALID")
        error = RawVersionReadError(code)
        error.__suppress_context__ = True
        return error
    if status != "ok" or set(value) != _OK_FIELDS:
        raise _isolated_error("PROTOCOL_INVALID")
    key = value.get("key")
    version_id = value.get("version_id")
    encoded = value.get("body_base64")
    if (
        type(key) is not str
        or type(version_id) is not str
        or type(encoded) is not str
        or key != ref.key
        or version_id != ref.version_id
        or len(encoded) > ((_BODY_MAX_BYTES + 2) // 3) * 4
    ):
        raise _isolated_error("PROTOCOL_INVALID")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError):
        raise _isolated_error("PROTOCOL_INVALID") from None
    if len(body) > _BODY_MAX_BYTES or base64.b64encode(body).decode("ascii") != encoded:
        raise _isolated_error("PROTOCOL_INVALID")
    return RawCheckpointVersion(key, version_id, body)


def _checkpoint(cancel: threading.Event | None, deadline: float) -> None:
    if cancel is not None and cancel.is_set():
        raise RawVersionReadCancelled()
    if _monotonic() >= deadline:
        raise _isolated_error("DEADLINE_EXCEEDED")


def _select(
    selected: selectors.BaseSelector,
    cancel: threading.Event | None,
    deadline: float,
) -> list[tuple[selectors.SelectorKey, int]]:
    _checkpoint(cancel, deadline)
    remaining = deadline - _monotonic()
    events = selected.select(timeout=max(0.0, min(_POLL_SECONDS, remaining)))
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
    if len(buffer) == end:
        return bytes(buffer[4:])
    return None


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
                raise _isolated_error("WORKER_FAILED")


def _close_stdin(
    selected: selectors.BaseSelector,
    process: subprocess.Popen[bytes],
    stdin_fd: int,
) -> None:
    try:
        selected.unregister(stdin_fd)
    except KeyError:
        pass
    stdin = process.stdin
    if stdin is not None and not stdin.closed:
        stdin.close()


def _drive_request_and_result(
    selected: selectors.BaseSelector,
    process: subprocess.Popen[bytes],
    stdin_fd: int,
    stdout_fd: int,
    request: bytes,
    ref: CheckpointVersionRef,
    cancel: threading.Event | None,
    deadline: float,
) -> RawCheckpointVersion | RawVersionReadError:
    selected.register(stdin_fd, selectors.EVENT_WRITE)
    sent = 0
    output = bytearray()
    result: RawCheckpointVersion | RawVersionReadError | None = None
    stdout_eof = False
    while True:
        for key, events in _select(selected, cancel, deadline):
            if key.fd == stdin_fd and events & selectors.EVENT_WRITE:
                try:
                    written = _write_fd(stdin_fd, request[sent : sent + _IO_CHUNK_BYTES])
                except BlockingIOError:
                    written = 0
                except BrokenPipeError:
                    written = 0
                    _close_stdin(selected, process, stdin_fd)
                if written:
                    sent += written
                if sent == len(request):
                    _close_stdin(selected, process, stdin_fd)
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
                    candidate, eof = _read_frame_piece(stdout_fd, output, _RESULT_MAX_BYTES)
                    if candidate is not None:
                        if sent != len(request):
                            raise _isolated_error("PROTOCOL_INVALID")
                        result = _decode_result(candidate, ref)
                    if eof:
                        stdout_eof = True
                        selected.unregister(stdout_fd)
        if stdout_eof:
            status = process.poll()
            if status is not None:
                if result is None or status != 0:
                    raise _isolated_error("WORKER_FAILED")
                return result


def _run_protocol(
    selected: selectors.BaseSelector,
    process: subprocess.Popen[bytes],
    request: bytes,
    ref: CheckpointVersionRef,
    cancel: threading.Event | None,
    deadline: float,
) -> RawCheckpointVersion | RawVersionReadError:
    stdin = process.stdin
    stdout = process.stdout
    if stdin is None or stdout is None:
        raise RuntimeError("isolated worker pipes were not created")
    stdin_fd = stdin.fileno()
    stdout_fd = stdout.fileno()
    os.set_blocking(stdin_fd, False)
    os.set_blocking(stdout_fd, False)
    selected.register(stdout_fd, selectors.EVENT_READ)
    ready = _receive_ready(selected, stdout_fd, cancel, deadline)
    _decode_ready(ready)
    payload = _drive_request_and_result(
        selected,
        process,
        stdin_fd,
        stdout_fd,
        _frame(request),
        ref,
        cancel,
        deadline,
    )
    return payload


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
        except BaseException as error:  # noqa: BLE001 - preserve fatal parent fault identity
            retain(error)
    if process is None:
        if fixed_failure:
            outcomes.append(_isolated_error("CLEANUP_FAILED"))
        return outcomes
    if process.returncode is None:
        if not operation_failed:
            fixed_failure = True
        try:
            _killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except BaseException as error:  # noqa: BLE001 - preserve fatal parent fault identity
            retain(error)
        try:
            process.wait(timeout=_REAP_SECONDS)
        except subprocess.TimeoutExpired:
            fixed_failure = True
        except BaseException as error:  # noqa: BLE001 - preserve identity and mark unreaped
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
    return isinstance(error, (IsolatedRawReadError, RawVersionReadError, RawVersionReadCancelled))


def read_raw_checkpoint_version_isolated(
    reader: ExplicitHistoryReader,
    ref: CheckpointVersionRef,
    *,
    cancel: threading.Event | None = None,
) -> RawCheckpointVersion:
    """Read one retained version through a fresh, bounded private worker process."""
    raw_transport._validate_inputs(reader, ref, cancel)
    if cancel is not None and cancel.is_set():
        raise RawVersionReadCancelled()
    if not _runtime_supported():
        raise _isolated_error("RUNTIME_UNSUPPORTED")
    request = _request_payload(reader, ref)
    if cancel is not None and cancel.is_set():
        raise RawVersionReadCancelled()
    deadline = _monotonic() + _DEADLINE_SECONDS

    selected: selectors.BaseSelector | None = None
    process: subprocess.Popen[bytes] | None = None
    primary: BaseException | None = None
    result: RawCheckpointVersion | None = None
    try:
        try:
            process = _spawn_worker()
        except OSError:
            primary = _isolated_error("WORKER_START_FAILED")
        if primary is None:
            selected = selectors.DefaultSelector()
            if process is None:
                raise RuntimeError("isolated worker start produced no process")
            outcome = _run_protocol(selected, process, request, ref, cancel, deadline)
            if isinstance(outcome, RawVersionReadError):
                primary = outcome
            else:
                result = outcome
    except BaseException as error:  # noqa: BLE001 - preserve fatal parent exception identity
        primary = error
    cleanup_outcomes = _cleanup_worker(selected, process, operation_failed=primary is not None)

    cancelled = cancel is not None and cancel.is_set()
    expired = False if cancelled else _monotonic() >= deadline
    outcomes: list[BaseException] = []
    if primary is not None and not _is_controlled(primary):
        outcomes.append(primary)
        if cancelled:
            outcomes.append(RawVersionReadCancelled())
    elif cancelled:
        outcomes.append(
            primary if isinstance(primary, RawVersionReadCancelled) else RawVersionReadCancelled()
        )
    elif expired and (primary is None or isinstance(primary, RawVersionReadError)):
        outcomes.append(_isolated_error("DEADLINE_EXCEEDED"))
    elif primary is not None:
        outcomes.append(primary)
    outcomes.extend(cleanup_outcomes)
    if outcomes:
        _raise_outcomes(outcomes)
    if result is None:
        raise RuntimeError("isolated raw checkpoint read produced no outcome")
    return result
