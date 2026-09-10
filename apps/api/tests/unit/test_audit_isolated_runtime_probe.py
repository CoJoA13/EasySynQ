"""Regression proofs for process-exit races in the actual-image memory sampler."""

from __future__ import annotations

import errno
import runpy
import select
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO, cast

import pytest

_PROBE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "integration/audit_isolated_raw_runtime_probe.py")
)
_memory_sample = _PROBE["_memory_sample"]

pytestmark = pytest.mark.unit


@pytest.fixture
def ready_child() -> Iterator[subprocess.Popen[str]]:
    child = subprocess.Popen(
        [sys.executable, "-c", 'import sys; print("READY", flush=True); sys.stdin.read()'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert select.select([child.stdout], [], [], 3)[0]
        assert child.stdout.readline() == "READY\n"
        yield child
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


def _exit_child(child: subprocess.Popen[str]) -> None:
    assert child.stdin is not None
    child.stdin.close()
    assert child.wait(timeout=3) == 0


def test_live_ready_child_still_supplies_memory_evidence(
    ready_child: subprocess.Popen[str],
) -> None:
    memory = _memory_sample(ready_child.pid)
    assert memory is not None
    rss, vms = memory
    assert vms >= rss > 0


@pytest.mark.parametrize("exit_at", ["before_open", "after_open"])
def test_exited_child_does_not_crash_memory_sampler(
    exit_at: str,
    ready_child: subprocess.Popen[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if exit_at == "before_open":
        _exit_child(ready_child)
    else:
        original_open = Path.open
        status_path = Path(f"/proc/{ready_child.pid}/status")

        def open_then_exit(path: Path, *args: Any, **kwargs: Any) -> TextIO:
            stream = original_open(path, *args, **kwargs)
            if path == status_path:
                try:
                    # Exercise the real kernel read after exit, not a manufactured exception.
                    _exit_child(ready_child)
                except BaseException:
                    stream.close()
                    raise
            return cast(TextIO, stream)

        monkeypatch.setattr(Path, "open", open_then_exit)

    assert _memory_sample(ready_child.pid) is None


@pytest.mark.parametrize("number", [errno.EACCES, errno.EIO])
def test_unexpected_memory_read_errors_still_fail(
    number: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = OSError(number, "controlled read failure")

    def failed_read(_path: Path, **_kwargs: Any) -> str:
        raise error

    monkeypatch.setattr(Path, "read_text", failed_read)
    with pytest.raises(OSError) as caught:
        _memory_sample(123)
    assert caught.value is error
