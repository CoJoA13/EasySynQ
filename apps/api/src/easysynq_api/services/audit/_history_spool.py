"""Parent ownership of one private, persistent, storage-only worker."""

from __future__ import annotations

import dataclasses
import functools
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event
from types import TracebackType
from typing import Any, Concatenate, NoReturn
from uuid import UUID

from . import _history_spool_protocol as wire
from ._history_spool_protocol import _PageAdmission, _PageTicket, _SpoolSummary, _SpoolWitness
from .history_collection import (
    HistoryCollectionCancelled,
    HistoryCollectionError,
    HistoryCollectionLimits,
)

_monotonic = time.monotonic
_killpg = os.killpg
_read_fd = os.read
_write_fd = os.write
_REAP_SECONDS = 2.0


def _raise_faults(faults: list[BaseException]) -> NoReturn:
    if len(faults) == 1:
        raise faults[0] from None
    raise BaseExceptionGroup("history spool operation and cleanup failed", faults) from None


def _owned[**P, Result](
    method: Callable[Concatenate[_SpoolSession, P], Result],
) -> Callable[Concatenate[_SpoolSession, P], Result]:
    @functools.wraps(method)
    def operation(self: _SpoolSession, /, *args: P.args, **kwargs: P.kwargs) -> Result:
        if not self._active or self._poisoned or self._finished:
            raise HistoryCollectionError("PROTOCOL_INVALID") from None
        try:
            self._check()
            result = method(self, *args, **kwargs)
            self._check()
            return result
        except BaseException as error:  # noqa: BLE001 - every failure ends this ownership lifetime
            self._abandon(error)

    return operation


class _SpoolSession:
    def __init__(
        self,
        org_id: UUID,
        witnesses: tuple[_SpoolWitness, ...],
        limits: HistoryCollectionLimits,
        *,
        cancel: Event,
        deadline: float,
    ) -> None:
        self._org = org_id
        self._witnesses = witnesses
        self._limits = limits
        self._cancel = cancel
        self._deadline = deadline
        self._process: subprocess.Popen[bytes] | None = None
        self._selector: selectors.BaseSelector | None = None
        self._directory: str | None = None
        self._identity: tuple[int, int] | None = None
        self._sequence = 0
        self._active = False
        self._poisoned = False
        self._finished = False

    def _check(self, command_deadline: float | None = None) -> None:
        if self._cancel.is_set():
            raise HistoryCollectionCancelled()
        if _monotonic() >= min(self._deadline, command_deadline or self._deadline):
            raise HistoryCollectionError("DEADLINE_EXCEEDED")

    def __enter__(self) -> _SpoolSession:
        if self._active or self._poisoned or self._finished:
            raise HistoryCollectionError("PROTOCOL_INVALID")
        try:
            self._check()
            if not sys.platform.startswith("linux") or not hasattr(os, "set_blocking"):
                raise HistoryCollectionError("RUNTIME_UNSUPPORTED")
            self._directory = tempfile.mkdtemp(prefix="easysynq-history-")
            info = os.lstat(self._directory)
            self._identity = (info.st_dev, info.st_ino)
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.geteuid()
                or os.listdir(self._directory)
            ):
                raise HistoryCollectionError("STORAGE_FAILED")
            worker = Path(__file__).with_name("_history_spool_worker.py").resolve()
            source = Path(__file__).resolve().parents[3]
            try:
                self._process = subprocess.Popen(  # noqa: S603 - fixed executable and arguments
                    [sys.executable, "-I", "-B", "-u", str(worker), str(source)],
                    cwd=self._directory,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    close_fds=True,
                    start_new_session=True,
                    env={"LANG": "C.UTF-8", "TZ": "UTC"},
                    bufsize=0,
                )
            except OSError:
                raise HistoryCollectionError("WORKER_START_FAILED") from None
            self._selector = selectors.DefaultSelector()
            process = self._process
            if process.stdin is None or process.stdout is None:
                raise HistoryCollectionError("WORKER_START_FAILED")
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            ready = wire.metadata(self._receive(self._command_deadline()))
            wire.fields(ready, {"op", "version"})
            if ready["op"] != "READY" or type(ready["version"]) is not int or ready["version"] != 1:
                wire.invalid()
            self._active = True
            self._rpc(
                "INIT",
                {
                    "version": 1,
                    "org_id": str(self._org),
                    "witnesses": [
                        {
                            "witness_id": str(scope.witness_id),
                            "namespace_hash": scope.namespace_hash,
                            "bucket": scope.bucket,
                        }
                        for scope in self._witnesses
                    ],
                    "limits": dataclasses.asdict(self._limits),
                },
                expected={"version"},
            )
            self._check()
            return self
        except BaseException as error:  # noqa: BLE001 - failed enter must clean its own resources
            self._abandon(error)

    def _abandon(self, primary: BaseException) -> NoReturn:
        self._poisoned = True
        faults = self._cleanup()
        # Only a plain controlled operation error can be superseded. Cleanup
        # failures, exception groups and unexpected/fatal identities remain intact.
        if type(primary) is HistoryCollectionError and primary.code != "CLEANUP_FAILED":
            try:
                self._check()
            except (HistoryCollectionCancelled, HistoryCollectionError) as checkpoint:
                primary = checkpoint
            except BaseException as checkpoint:  # noqa: BLE001 - preserve unexpected checks too
                faults.append(checkpoint)
        _raise_faults([primary, *faults])

    def _command_deadline(self) -> float:
        return min(self._deadline, _monotonic() + 10.0)

    def _wait(self, fd: int, event: int, deadline: float) -> None:
        self._wait_for(((fd, event),), deadline)

    def _wait_for(self, interests: tuple[tuple[int, int], ...], deadline: float) -> None:
        selected = self._selector
        if selected is None:
            wire.invalid()
        registered: list[int] = []
        faults: list[BaseException] = []
        try:
            for fd, event in interests:
                selected.register(fd, event)
                registered.append(fd)
            while True:
                self._check(deadline)
                if selected.select(min(0.05, max(0, deadline - _monotonic()))):
                    break
        except BaseException as error:  # noqa: BLE001 - preserve operation and unregister failures
            faults.append(error)
        finally:
            for fd in reversed(registered):
                try:
                    selected.unregister(fd)
                except BaseException as error:  # noqa: BLE001 - every registration is released
                    faults.append(error)
        if faults:
            _raise_faults(faults)

    def _early_terminal(self, first: bytes, deadline: float, request_id: int) -> NoReturn:
        if not first:
            raise HistoryCollectionError("WORKER_FAILED")
        length = int.from_bytes(first + self._exact(3, deadline), "big")
        if not 0 < length <= wire.FRAME_MAX:
            wire.invalid()
        response = wire.metadata(self._exact(length, deadline))
        self._accept_response(response, request_id, set(), deadline)
        # No success response is permitted before the complete upload was sent.
        wire.invalid()

    def _send_upload(self, payload: bytes, deadline: float, request_id: int) -> None:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            wire.invalid()
        stdin_fd, stdout_fd = process.stdin.fileno(), process.stdout.fileno()
        body = wire.frame(payload)
        offset = 0
        while offset < len(body):
            self._check(deadline)
            try:
                first = _read_fd(stdout_fd, 1)
            except BlockingIOError:
                pass
            except OSError:
                raise HistoryCollectionError("WORKER_FAILED") from None
            else:
                self._early_terminal(first, deadline, request_id)
            try:
                count = _write_fd(stdin_fd, body[offset : offset + wire.CHUNK_MAX])
            except BlockingIOError:
                self._wait_for(
                    ((stdout_fd, selectors.EVENT_READ), (stdin_fd, selectors.EVENT_WRITE)), deadline
                )
                continue
            except BrokenPipeError:
                # A terminal response may have raced the read above. Drain only its
                # bounded frame under this same command deadline before classifying EOF.
                self._early_terminal(self._read(1, deadline), deadline, request_id)
            except OSError:
                raise HistoryCollectionError("WORKER_FAILED") from None
            if type(count) is not int or not 0 < count <= min(wire.CHUNK_MAX, len(body) - offset):
                wire.invalid()
            offset += count

    def _send(self, payload: bytes, deadline: float) -> None:
        process = self._process
        if process is None or process.stdin is None or process.stdin.closed:
            wire.invalid()
        fd = process.stdin.fileno()
        body = wire.frame(payload)
        offset = 0
        while offset < len(body):
            self._check(deadline)
            try:
                count = _write_fd(fd, body[offset : offset + wire.CHUNK_MAX])
            except BlockingIOError:
                self._wait(fd, selectors.EVENT_WRITE, deadline)
                continue
            except OSError:
                raise HistoryCollectionError("WORKER_FAILED") from None
            if type(count) is not int or not 0 < count <= min(wire.CHUNK_MAX, len(body) - offset):
                wire.invalid()
            offset += count

    def _read(self, maximum: int, deadline: float) -> bytes:
        process = self._process
        if process is None or process.stdout is None:
            wire.invalid()
        fd = process.stdout.fileno()
        while True:
            self._check(deadline)
            try:
                return _read_fd(fd, maximum)
            except BlockingIOError:
                self._wait(fd, selectors.EVENT_READ, deadline)
            except OSError:
                raise HistoryCollectionError("WORKER_FAILED") from None

    def _exact(self, length: int, deadline: float) -> bytes:
        output = bytearray()
        while len(output) < length:
            body = self._read(min(wire.CHUNK_MAX, length - len(output)), deadline)
            if not body:
                raise HistoryCollectionError("WORKER_FAILED")
            output.extend(body)
        return bytes(output)

    def _receive(self, deadline: float) -> bytes:
        length = int.from_bytes(self._exact(4, deadline), "big")
        if not 0 < length <= wire.FRAME_MAX:
            wire.invalid()
        return self._exact(length, deadline)

    def _rpc(
        self,
        op: str,
        args: dict[str, Any],
        *,
        expected: set[str] | None = None,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        self._check()
        deadline = self._command_deadline()
        self._sequence += 1
        request_id = self._sequence
        self._send(wire.encode({"op": op, "id": request_id, **args}), deadline)
        if body is not None:
            for offset in range(0, len(body), wire.CHUNK_MAX):
                self._send_upload(
                    wire.chunk(request_id, offset, body[offset : offset + wire.CHUNK_MAX]),
                    deadline,
                    request_id,
                )
        if op == "PAGE_BEGIN":
            self._send_upload(
                wire.encode({"op": "PAGE_END", "id": request_id}), deadline, request_id
            )
        if op == "FINISH":
            process = self._process
            if process is None or process.stdin is None:
                wire.invalid()
            process.stdin.close()
        response = wire.metadata(self._receive(deadline))
        self._accept_response(response, request_id, expected or set(), deadline)
        if op == "INIT" and (type(response["version"]) is not int or response["version"] != 1):
            wire.invalid()
        self._check(deadline)
        return response

    def _accept_response(
        self, response: dict[str, Any], request_id: int, expected: set[str], deadline: float
    ) -> None:
        self._check(deadline)
        if wire.integer(response.get("id"), 1, 2_147_483_647) != request_id:
            wire.invalid()
        if response.get("op") == "ERROR":
            wire.fields(response, {"op", "id", "code"})
            try:
                error = HistoryCollectionError(response["code"])
            except ValueError:
                wire.invalid()
            raise error
        wire.fields(response, {"op", "id"} | expected)
        if response["op"] != "ACK":
            wire.invalid()
        self._check(deadline)

    @_owned
    def reserve_page(
        self, witness_index: int, key_marker: str | None, version_id_marker: str | None
    ) -> _PageTicket | None:
        response = self._rpc(
            "RESERVE_PAGE",
            {"witness": witness_index, "key": key_marker, "version": version_id_marker},
            expected={"ticket"},
        )
        return None if response["ticket"] is None else wire.ticket(response["ticket"])

    @_owned
    def admit_page(self, ticket: _PageTicket, raw_body: bytes) -> _PageAdmission:
        if type(ticket) is not _PageTicket or type(raw_body) is not bytes:
            wire.invalid()
        wire.integer(len(raw_body), 1, wire.PAGE_MAX)
        result = self._rpc(
            "PAGE_BEGIN",
            {"ticket": dataclasses.asdict(ticket), "length": len(raw_body)},
            body=raw_body,
            expected={"admission"},
        )["admission"]
        wire.fields(result, {"first_ordinal", "version_count", "delete_count"})
        first = wire.integer(result["first_ordinal"], 0, self._limits.maximum_observations)
        versions = wire.integer(result["version_count"], 0, 1_000)
        deletes = wire.integer(result["delete_count"], 0, 1_000)
        if (
            versions + deletes > 1_000
            or (first == 0) != (versions + deletes == 0)
            or first + versions + deletes - 1 > self._limits.maximum_observations
        ):
            wire.invalid()
        return _PageAdmission(first, versions, deletes)

    @_owned
    def record_list_failure(self, ticket: _PageTicket, code: str) -> None:
        if type(ticket) is not _PageTicket:
            wire.invalid()
        self._rpc("LIST_FAILURE", {"ticket": dataclasses.asdict(ticket), "code": code})

    @_owned
    def record_body(self, ordinal: int, body: bytes) -> None:
        if type(body) is not bytes or len(body) > wire.CHUNK_MAX:
            wire.invalid()
        self._rpc("BODY", {"ordinal": ordinal, "length": len(body)}, body=body)

    @_owned
    def record_version_failure(
        self, ordinal: int, code: str, *, ineligible: bool = False, delete_marker: bool = False
    ) -> None:
        self._rpc(
            "VERSION_FAILURE",
            {
                "ordinal": ordinal,
                "code": code,
                "ineligible": ineligible,
                "delete_marker": delete_marker,
            },
        )

    @_owned
    def record_cycle(self, witness_index: int) -> None:
        self._rpc("CYCLE", {"witness": witness_index})

    @_owned
    def finish(self) -> _SpoolSummary:
        response = self._rpc("FINISH", {}, expected={"summary"})
        summary = wire.decode_summary(response["summary"], self._witnesses, self._limits)
        shutdown_deadline = min(self._deadline, _monotonic() + _REAP_SECONDS)
        if self._read(1, shutdown_deadline) != b"":
            wire.invalid()
        process = self._process
        if process is None:
            wire.invalid()
        try:
            status = process.wait(timeout=max(0, shutdown_deadline - _monotonic()))
        except subprocess.TimeoutExpired:
            raise HistoryCollectionError("WORKER_FAILED") from None
        if status != 0:
            raise HistoryCollectionError("WORKER_FAILED")
        faults = self._cleanup()
        if faults:
            _raise_faults(faults)
        self._check()
        self._finished = True
        return summary

    def _cleanup(self) -> list[BaseException]:
        faults: list[BaseException] = []
        fixed_failure = False

        def retain(error: BaseException, *, unproved: bool = False) -> None:
            nonlocal fixed_failure
            if isinstance(error, OSError):
                fixed_failure = True
            else:
                faults.append(error)
            fixed_failure |= unproved

        if self._selector is not None:
            try:
                self._selector.close()
            except BaseException as error:  # noqa: BLE001 - attempt every independent cleanup
                retain(error, unproved=True)
            else:
                self._selector = None
        process = self._process
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except BaseException as error:  # noqa: BLE001 - a failed pipe close cannot skip reaping
                        retain(error, unproved=True)
            if process.returncode is None:
                try:
                    _killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as error:  # noqa: BLE001 - reaping is independently attempted
                    retain(error)
                try:
                    process.wait(timeout=_REAP_SECONDS)
                except subprocess.TimeoutExpired:
                    fixed_failure = True
                except BaseException as error:  # noqa: BLE001 - retain fatal identities
                    retain(error, unproved=True)
        if self._directory is not None and (process is None or process.returncode is not None):
            try:
                info = os.lstat(self._directory)
                if (
                    (info.st_dev, info.st_ino) != self._identity
                    or not stat.S_ISDIR(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != 0o700
                    or info.st_uid != os.geteuid()
                ):
                    fixed_failure = True
                else:
                    shutil.rmtree(self._directory)
                    if os.path.lexists(self._directory):
                        fixed_failure = True
                    else:
                        self._directory = None
            except BaseException as error:  # noqa: BLE001 - failed removal must be visible
                retain(error, unproved=True)
        elif self._directory is not None:
            fixed_failure = True
        if fixed_failure:
            faults.append(HistoryCollectionError("CLEANUP_FAILED"))
        return faults

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._active = False
        faults = self._cleanup()
        if faults:
            _raise_faults(([exc] if exc is not None else []) + faults)
