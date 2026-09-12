"""Deterministic ownership checks for the history-collection runtime probe."""

from __future__ import annotations

import importlib.util
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_RUNTIME_PROBE_PATH = (
    Path(__file__).resolve().parents[1] / "integration/audit_history_collection_runtime_probe.py"
)
_MODULE_NAME = "easysynq_audit_history_collection_runtime_probe"
_SPEC = importlib.util.spec_from_file_location(_MODULE_NAME, _RUNTIME_PROBE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_RUNTIME_PROBE = importlib.util.module_from_spec(_SPEC)
sys.modules[_MODULE_NAME] = _RUNTIME_PROBE
_SPEC.loader.exec_module(_RUNTIME_PROBE)

pytestmark = pytest.mark.unit


class _Pipe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Process:
    def __init__(
        self,
        *,
        exit_at_timeout: int | None,
        drain_error: subprocess.TimeoutExpired | None = None,
    ) -> None:
        self.args = ["synthetic-resource-child"]
        self.pid = 4242
        self.returncode = exit_at_timeout
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.communicate_timeouts: list[int | None] = []
        self.initial_error = subprocess.TimeoutExpired(self.args, 45)
        self.drain_error = drain_error

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, timeout: int | None = None) -> tuple[bytes, bytes]:
        self.communicate_timeouts.append(timeout)
        if len(self.communicate_timeouts) == 1:
            raise self.initial_error
        if self.drain_error is not None:
            raise self.drain_error
        return b"", b""


def _install_process(
    monkeypatch: pytest.MonkeyPatch,
    process: _Process,
    signals: list[tuple[int, signal.Signals]],
) -> None:
    def popen(_argv: list[str], **_kwargs: Any) -> _Process:
        return process

    monkeypatch.setattr(_RUNTIME_PROBE.subprocess, "Popen", popen)
    monkeypatch.setattr(
        _RUNTIME_PROBE.os,
        "killpg",
        lambda pid, signum: signals.append((pid, signum)),
    )


def test_exited_at_timeout_is_drained_and_closes_pipes_without_signaling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(exit_at_timeout=0)
    signals: list[tuple[int, signal.Signals]] = []
    _install_process(monkeypatch, process, signals)

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _RUNTIME_PROBE._child_case(
            {
                "writable": str(tmp_path),
                "config_path": str(tmp_path / "synthetic-config.json"),
            },
            "descriptors",
        )

    assert caught.value is process.initial_error
    assert process.stdout.closed and process.stderr.closed
    assert process.communicate_timeouts == [45, 2]
    assert signals == []
    assert list(tmp_path.iterdir()) == []


def test_timeout_cleanup_closes_pipes_when_bounded_drain_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drain_error = subprocess.TimeoutExpired(["synthetic-resource-child"], 2)
    process = _Process(exit_at_timeout=None, drain_error=drain_error)
    signals: list[tuple[int, signal.Signals]] = []
    _install_process(monkeypatch, process, signals)

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _RUNTIME_PROBE._child_case(
            {
                "writable": str(tmp_path),
                "config_path": str(tmp_path / "synthetic-config.json"),
            },
            "descriptors",
        )

    assert caught.value is drain_error
    assert signals == [(process.pid, signal.SIGKILL)]
    assert process.communicate_timeouts == [45, 2]
    assert process.stdout.closed and process.stderr.closed
    assert list(tmp_path.iterdir()) == []
