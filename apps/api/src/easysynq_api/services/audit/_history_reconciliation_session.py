"""Parent owns the reconciliation worker through evidence, seal and cleanup."""

from __future__ import annotations

import os
import selectors
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any, Concatenate

from . import _history_reconciliation_protocol as protocol
from . import _history_spool as base
from . import _history_spool_protocol as wire
from .bootstrap_bridge import BridgePageObservation
from .history_collection import HistoryCollectionError


def _owned[**P, Result](
    method: Callable[Concatenate[_ReconciliationSession, P], Result],
) -> Callable[Concatenate[_ReconciliationSession, P], Result]:
    @base._owned
    def run(self: base._SpoolSession, /, *args: P.args, **kwargs: P.kwargs) -> Result:
        if not isinstance(self, _ReconciliationSession):
            wire.invalid()
        return method(self, *args, **kwargs)

    return run


class _ReconciliationSession(base._SpoolSession):
    def __init__(self, scope: protocol._PublicScope, *, cancel: Event, deadline: float) -> None:
        self._scope = scope
        self._package_uploaded = False
        super().__init__(
            scope.enrollment.stream.org_id,
            scope.witnesses,
            protocol.collection_limits(scope.limits),
            cancel=cancel,
            deadline=deadline,
        )

    def __enter__(self) -> _ReconciliationSession:
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
            worker = Path(__file__).with_name("_history_reconciliation_worker.py").resolve()
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
            if (
                ready["op"] != "READY"
                or type(ready["version"]) is not int
                or ready["version"] != protocol.VERSION
            ):
                wire.invalid()
            self._active = True
            self._rpc("INIT", protocol.scope_payload(self._scope), expected={"version"})
            self._check()
            return self
        except BaseException as error:  # noqa: BLE001 - failed enter must clean its own resources
            self._abandon(error)

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
        if op in {"PAGE_BEGIN", "PACKAGE_BEGIN"}:
            self._send_upload(
                wire.encode(
                    {"op": "PAGE_END" if op == "PAGE_BEGIN" else "PACKAGE_END", "id": request_id}
                ),
                deadline,
                request_id,
            )
        if op == "FINISH_RECONCILIATION":
            process = self._process
            if process is None or process.stdin is None:
                wire.invalid()
            process.stdin.close()
        response = wire.metadata(self._receive(deadline))
        self._accept_response(response, request_id, expected or set(), deadline)
        if op == "INIT" and (
            type(response["version"]) is not int or response["version"] != protocol.VERSION
        ):
            wire.invalid()
        self._check(deadline)
        return response

    @_owned
    def upload_package(
        self, root_body: bytes | None, pages: tuple[BridgePageObservation, ...]
    ) -> None:
        if (
            self._package_uploaded
            or type(pages) is not tuple
            or len(pages) != len(self._scope.page_lengths)
        ):
            wire.invalid()
        if (root_body is None) != (self._scope.root_length is None):
            wire.invalid()
        if root_body is not None and (
            type(root_body) is not bytes or len(root_body) != self._scope.root_length
        ):
            wire.invalid()
        for page, length in zip(pages, self._scope.page_lengths, strict=True):
            if (
                type(page) is not BridgePageObservation
                or type(page.body) is not bytes
                or len(page.body) != length
            ):
                wire.invalid()
        if root_body is not None:
            self._rpc(
                "PACKAGE_BEGIN",
                {"kind": "root", "index": 0, "length": len(root_body)},
                body=root_body,
            )
        for index, page in enumerate(pages):
            self._rpc(
                "PACKAGE_BEGIN",
                {"kind": "page", "index": index, "length": len(page.body)},
                body=page.body,
            )
        self._package_uploaded = True

    @_owned
    def seal(self, expected_observations: int) -> wire._SpoolSummary:
        response = self._rpc(
            "SEAL", {"expected_observations": expected_observations}, expected={"summary"}
        )
        summary = wire.decode_summary(response["summary"], self._witnesses, self._limits)
        if (
            sum(w.version_observations + w.delete_observations for w in summary.witnesses)
            != expected_observations
        ):
            wire.invalid()
        return summary

    @_owned
    def step(self) -> protocol._Progress:
        response = self._rpc("STEP", {}, expected={"progress"})
        return protocol.decode_progress(response["progress"])

    @_owned
    def finish_reconciliation(self) -> protocol._RawResult:
        self._rpc("FINISH_RECONCILIATION", {}, expected={"result"})
        # Real result decoding and post-result EOF/reaping/publication belong to
        # engine wiring. A premature or fabricated success cannot be returned.
        wire.invalid()
