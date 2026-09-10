"""Source-bound unit checks for the external-audit acceptance runner."""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_RUNNER_PATH = _REPOSITORY_ROOT / "scripts/run-audit-external-acceptance.py"
_MODULE_NAME = "easysynq_audit_external_acceptance_runner"
_SPEC = importlib.util.spec_from_file_location(_MODULE_NAME, _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_RUNNER = importlib.util.module_from_spec(_SPEC)
sys.modules[_MODULE_NAME] = _RUNNER
_SPEC.loader.exec_module(_RUNNER)

pytestmark = pytest.mark.unit

_RUN_ID = "00000000-0000-4000-8000-000000000001"
_IMAGE_ID = "sha256:" + ("a" * 64)
_CONTAINER_ID = "b" * 64
_SESSION_ID = "testcontainers-session-1"
_MANDATORY_NAMES = (
    "test_external_cli_runtime_is_public_only_and_read_only",
    "test_external_cli_runtime_preserves_enrolled_obligation_after_db_selection_attack",
    "test_external_cli_runtime_accepts_historical_target_with_newer_witness",
    "test_raw_version_runtime_preserves_exact_provider_bytes_and_bridge",
    "test_raw_version_runtime_enforces_routing_and_tls",
    "test_raw_version_runtime_bounds_streams_and_cleans_up",
    "test_isolated_raw_runtime_enforces_process_and_byte_boundaries",
    "test_version_page_decoder_runtime_rejects_lossy_provider_pages",
)


def _write(path: Path, value: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    for relative in (
        ".dockerignore",
        "apps/api/Dockerfile",
        "apps/api/pyproject.toml",
        "apps/api/uv.lock",
        "apps/api/alembic.ini",
        "apps/api/src/easysynq_api/__init__.py",
        "migrations/001.py",
        "scripts/run-audit-external-acceptance.py",
        "apps/api/tests/conftest.py",
        "apps/api/tests/integration/audit_external_runtime_acceptance.py",
        "apps/api/tests/integration/audit_raw_runtime_acceptance.py",
        "apps/api/tests/integration/audit_raw_runtime_probe.py",
        "apps/api/tests/integration/audit_isolated_raw_runtime_acceptance.py",
        "apps/api/tests/integration/audit_isolated_raw_runtime_probe.py",
        "apps/api/tests/integration/audit_version_page_runtime_acceptance.py",
        "apps/api/tests/integration/audit_version_page_runtime_probe.py",
        "apps/api/tests/fixtures/audit_bootstrap_bridge_vectors.json",
        "apps/api/tests/unit/test_sample.py",
        "infra/images.lock",
        "infra/compose/minio/minio-init.sh",
    ):
        _write(root / relative, relative)
    return root


def _junit(*, mode: str = "passing", affected: int = 0) -> str:
    cases: list[str] = []
    names = tuple(
        name
        for index, name in enumerate(_MANDATORY_NAMES)
        if mode != "missing" or index != affected
    )
    for original_name in names:
        selected = original_name == _MANDATORY_NAMES[affected]
        name = (
            original_name + "_substitute" if mode == "substituted" and selected else original_name
        )
        child = f"<{mode} />" if mode in {"skipped", "failure", "error"} and selected else ""
        cases.append(f'<testcase name="{name}">{child}</testcase>')
    return "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>"


class _FakeCommands:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[list[str], Path, int, dict[str, str] | None]] = []
        self.build_returncode = 0
        self.harness_returncode = 0
        self.junit_mode = "passing"
        self.junit_affected = 0
        self.junit_content: str | None = None
        self.initial_inspect_mode = "valid"
        self.cleanup_inspect_mode = "valid"
        self.image_remove_returncode = 0
        self.container_remove_returncode = 0
        self.container_label_matches = True
        self.container_ids = [_CONTAINER_ID]
        self.change_after_harness: tuple[Path, str] | None = None
        self._image_inspections = 0
        self.run_id = ""
        self.source_digest = ""
        self.tag = ""
        self.harness_environment: dict[str, str] | None = None

    def _result(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> object:
        return _RUNNER._CommandResult(returncode, stdout, stderr)

    def _image_metadata(self, mode: str) -> object:
        if mode == "missing":
            return self._result(1, stderr="Error: No such image")
        if mode == "daemon_error":
            return self._result(1, stderr="controlled daemon failure")
        image_id = _IMAGE_ID if mode != "missing_id" else ""
        run_label = self.run_id if mode != "mismatched_label" else "another-run"
        return self._result(
            stdout=json.dumps(
                [
                    {
                        "Id": image_id,
                        "RepoTags": [self.tag],
                        "Config": {
                            "Labels": {
                                _RUNNER._RUN_LABEL: run_label,
                                _RUNNER._SOURCE_LABEL: self.source_digest,
                            }
                        },
                    }
                ]
            )
        )

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: int,
        environ: Mapping[str, str] | None = None,
    ) -> object:
        assert isinstance(argv, list)
        copied_environment = None if environ is None else dict(environ)
        arguments = list(argv)
        self.calls.append((arguments, cwd, timeout, copied_environment))
        if arguments == ["/tools/git", "rev-parse", "HEAD"]:
            return self._result(stdout=("c" * 40) + "\n")
        if arguments[:2] == ["/tools/docker", "build"]:
            self.run_id = arguments[3].split("=", 1)[1]
            self.source_digest = arguments[5].split("=", 1)[1]
            self.tag = arguments[-2]
            return self._result(self.build_returncode)
        if arguments[:3] == ["/tools/docker", "image", "inspect"]:
            self._image_inspections += 1
            mode = (
                self.initial_inspect_mode
                if self._image_inspections == 1
                else self.cleanup_inspect_mode
            )
            return self._image_metadata(mode)
        if arguments[:5] == [
            "/tools/uv",
            "run",
            "--project",
            str(self.root / "apps/api"),
            "pytest",
        ]:
            assert copied_environment is not None
            self.harness_environment = copied_environment
            record = Path(copied_environment["EASYSYNQ_ACCEPTANCE_RESOURCE_RECORD"])
            _write(
                record,
                json.dumps(
                    {
                        "run_id": copied_environment["EASYSYNQ_ACCEPTANCE_RUN_ID"],
                        "session_id": _SESSION_ID,
                    }
                ),
            )
            junit_path = Path(arguments[-1])
            if self.junit_mode != "absent":
                _write(
                    junit_path,
                    self.junit_content
                    if self.junit_content is not None
                    else _junit(mode=self.junit_mode, affected=self.junit_affected),
                )
            if self.change_after_harness is not None:
                path, action = self.change_after_harness
                if action == "content":
                    path.write_text("changed\n", encoding="utf-8")
                else:
                    target = path.with_name(path.name + ".target")
                    target.write_text("target\n", encoding="utf-8")
                    path.unlink()
                    path.symlink_to(target)
            return self._result(
                self.harness_returncode,
                stdout="private-child-stdout",
                stderr="private-child-stderr",
            )
        if arguments[:4] == ["/tools/docker", "ps", "-aq", "--filter"]:
            return self._result(stdout="\n".join(self.container_ids))
        if arguments[:3] == ["/tools/docker", "container", "inspect"]:
            label = _SESSION_ID if self.container_label_matches else "another-session"
            return self._result(
                stdout=json.dumps([{"Config": {"Labels": {_RUNNER._SESSION_LABEL: label}}}])
            )
        if arguments[:3] == ["/tools/docker", "rm", "-f"]:
            return self._result(self.container_remove_returncode)
        if arguments[:4] == ["/tools/docker", "image", "rm", "-f"]:
            return self._result(self.image_remove_returncode)
        raise AssertionError(f"unexpected command: {arguments!r}")


@pytest.fixture
def runner_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("RUNNER_LOG_SENTINEL", "secret-never-print")
    monkeypatch.setattr(
        _RUNNER.shutil,
        "which",
        lambda name: f"/tools/{name}",
    )
    monkeypatch.setattr(
        _RUNNER.uuid,
        "uuid4",
        lambda: uuid.UUID(_RUN_ID),
    )


def _run(root: Path, fake: _FakeCommands, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(_RUNNER, "_run_command", fake)
    return int(_RUNNER.run_acceptance(root))


def test_runner_uses_owned_cache_immutable_image_and_exact_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    cache = root / ".pytest_cache"
    sibling = cache / "sibling" / "keep"
    _write(sibling)
    fake = _FakeCommands(root)

    assert _run(root, fake, monkeypatch) == 0

    assert sibling.read_text(encoding="utf-8") == "fixture\n"
    assert list(cache.glob("audit-external-*")) == []
    build = next(call for call in fake.calls if call[0][:2] == ["/tools/docker", "build"])
    assert build[0] == [
        "/tools/docker",
        "build",
        "--label",
        f"{_RUNNER._RUN_LABEL}={_RUN_ID}",
        "--label",
        f"{_RUNNER._SOURCE_LABEL}={fake.source_digest}",
        "-f",
        "apps/api/Dockerfile",
        "-t",
        f"easysynq-audit-external:{_RUN_ID}",
        ".",
    ]
    harness = next(call for call in fake.calls if call[0][0] == "/tools/uv")
    assert harness[0][:9] == [
        "/tools/uv",
        "run",
        "--project",
        str(root / "apps/api"),
        "pytest",
        "tests/integration/audit_external_runtime_acceptance.py",
        "tests/integration/audit_raw_runtime_acceptance.py",
        "tests/integration/audit_isolated_raw_runtime_acceptance.py",
        "tests/integration/audit_version_page_runtime_acceptance.py",
    ]
    assert harness[1] == root / "apps/api"
    assert harness[2] == 1_200
    assert fake.harness_environment is not None
    assert fake.harness_environment["EASYSYNQ_TEST_API_IMAGE"] == _IMAGE_ID
    assert fake.harness_environment["UV_PYTHON_DOWNLOADS"] == "never"
    assert fake.tag not in fake.harness_environment.values()
    assert [call[0] for call in fake.calls if call[0][:3] == ["/tools/docker", "rm", "-f"]] == [
        ["/tools/docker", "rm", "-f", _CONTAINER_ID]
    ]
    output = capsys.readouterr().out
    assert "runtime_acceptance=passed" in output
    assert "mandatory_tests=8" in output
    assert _RUNNER._MANDATORY_TESTS == frozenset(_MANDATORY_NAMES)
    assert "secret-never-print" not in output


def test_runner_creates_and_retains_only_the_shared_cache_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    cache = root / ".pytest_cache"
    fake = _FakeCommands(root)
    fake.container_ids = []

    assert _run(root, fake, monkeypatch) == 0
    assert cache.is_dir() and not cache.is_symlink()
    assert list(cache.iterdir()) == []


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_runner_rejects_non_directory_or_symlink_cache_parent(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    cache = root / ".pytest_cache"
    if kind == "symlink":
        target = root / "cache-target"
        target.mkdir()
        cache.symlink_to(target, target_is_directory=True)
    else:
        _write(cache)
    fake = _FakeCommands(root)

    assert _run(root, fake, monkeypatch) == 1
    assert fake.calls == []


def test_build_failure_never_launches_pytest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.build_returncode = 1
    fake.initial_inspect_mode = "missing"

    assert _run(root, fake, monkeypatch) == 1
    assert all(call[0][0] != "/tools/uv" for call in fake.calls)


@pytest.mark.parametrize("mode", ["missing", "missing_id", "mismatched_label"])
def test_invalid_image_inspect_fails_before_pytest(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.initial_inspect_mode = mode
    fake.cleanup_inspect_mode = mode

    assert _run(root, fake, monkeypatch) == 1
    assert all(call[0][0] != "/tools/uv" for call in fake.calls)


@pytest.mark.parametrize(
    ("harness_returncode", "junit_mode"),
    [(1, "passing"), (0, "skipped"), (0, "missing"), (0, "absent")],
)
def test_pytest_or_junit_failure_propagates(
    harness_returncode: int,
    junit_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = harness_returncode
    fake.junit_mode = junit_mode

    assert _run(root, fake, monkeypatch) == 1


@pytest.mark.parametrize("mandatory_index", range(len(_MANDATORY_NAMES)))
@pytest.mark.parametrize("junit_mode", ["missing", "substituted", "skipped", "failure", "error"])
def test_runner_rejects_each_missing_or_nonpassing_mandatory_case(
    mandatory_index: int,
    junit_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.junit_mode = junit_mode
    fake.junit_affected = mandatory_index

    assert _run(root, fake, monkeypatch) == 1
    status = {"substituted": "missing", "failure": "failed"}.get(junit_mode, junit_mode)
    output = capsys.readouterr().out
    assert f"runtime_case={_MANDATORY_NAMES[mandatory_index]} status={status}" in output
    assert "runtime_junit=available" in output
    assert "runtime_acceptance=failed" in output
    assert "private-child-" not in output
    assert list((root / ".pytest_cache").iterdir()) == []


@pytest.mark.parametrize(
    ("returncode", "outcome"),
    [
        (1, "1"),
        (2, "2"),
        (3, "3"),
        (4, "4"),
        (5, "5"),
        (-9, "-9"),
        (256, "abnormal_exit"),
    ],
)
def test_failure_summary_redacts_all_report_and_child_details(
    returncode: int,
    outcome: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = returncode
    fake.junit_content = """<testsuites><testsuite name="private-suite">
      <properties><property name="token" value="private-property" /></properties>
      <testcase name="test_external_cli_runtime_is_public_only_and_read_only[private-param]"
                classname="private-class" file="private-file" time="private-time">
        <failure message="private-message">private-trace</failure>
        <system-out>private-output</system-out><system-err>private-error</system-err>
      </testcase>
      <testcase name="test_external_cli_runtime_is_public_only_and_read_only" />
      <testcase name="private-unknown"><error>private-other</error></testcase>
    </testsuite></testsuites>"""

    assert _run(root, fake, monkeypatch) == 1
    output = capsys.readouterr()
    assert f"runtime_harness_exit={outcome}" in output.out
    assert "runtime_other_cases=present" in output.out
    assert (
        "runtime_case=test_external_cli_runtime_is_public_only_and_read_only status=failed"
        in output.out
    )
    assert output.out.count("runtime_case=") == 8
    assert "private-" not in output.out + output.err
    assert "secret-never-print" not in output.out + output.err
    assert "runtime_acceptance=failed" in output.out
    assert list((root / ".pytest_cache").iterdir()) == []
    assert any(call[0][:3] == ["/tools/docker", "rm", "-f"] for call in fake.calls)
    assert any(call[0][:4] == ["/tools/docker", "image", "rm", "-f"] for call in fake.calls)


@pytest.mark.parametrize("report", ["absent", "malformed", "oversized", "entity"])
def test_unreadable_failure_report_preserves_failure_and_cleanup(
    report: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    if report == "absent":
        fake.junit_mode = "absent"
    else:
        fake.junit_content = {
            "malformed": "<private-unclosed>",
            "oversized": " " * (1_048_576 + 1),
            "entity": '<!DOCTYPE testsuites [<!ENTITY data "private-entity">]>'
            "<testsuites>&data;</testsuites>",
        }[report]

    assert _run(root, fake, monkeypatch) == 1
    output = capsys.readouterr().out
    assert "runtime_junit=unavailable" in output
    assert "failure_stage=runtime_harness" in output
    assert "runtime_acceptance=failed" in output
    assert "private-" not in output
    assert "runtime_case=" not in output
    assert list((root / ".pytest_cache").iterdir()) == []


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_failure_summary_rejects_links_and_special_files_without_blocking(
    kind: str,
    tmp_path: Path,
) -> None:
    report = tmp_path / "runtime.xml"
    if kind == "symlink":
        target = tmp_path / "private-target.xml"
        _write(target, _junit())
        report.symlink_to(target)
    elif kind == "directory":
        report.mkdir()
    else:
        os.mkfifo(report)
    # A separate process makes an accidental blocking open fail with a bounded timeout.
    result = subprocess.run(  # noqa: S603 - fixed Python helper, controlled temporary path
        [
            sys.executable,
            "-c",
            "import runpy, sys; from pathlib import Path; "
            "runner = runpy.run_path(sys.argv[1]); "
            'print("\\n".join(runner["_runtime_failure_summary"](Path(sys.argv[2]), 1)))',
            str(_RUNNER_PATH),
            str(report),
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "runtime_harness_exit=1",
        "runtime_junit=unavailable",
    ]
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("relative", "action"),
    [
        ("apps/api/src/easysynq_api/__init__.py", "content"),
        ("infra/images.lock", "content"),
        ("infra/compose/minio/minio-init.sh", "content"),
        ("apps/api/tests/integration/audit_external_runtime_acceptance.py", "content"),
        ("apps/api/tests/integration/audit_raw_runtime_acceptance.py", "content"),
        ("apps/api/tests/integration/audit_raw_runtime_probe.py", "content"),
        ("apps/api/tests/integration/audit_isolated_raw_runtime_acceptance.py", "content"),
        ("apps/api/tests/integration/audit_isolated_raw_runtime_probe.py", "content"),
        ("apps/api/tests/integration/audit_version_page_runtime_acceptance.py", "content"),
        ("apps/api/tests/integration/audit_version_page_runtime_probe.py", "content"),
        ("apps/api/tests/fixtures/audit_bootstrap_bridge_vectors.json", "content"),
        ("infra/images.lock", "symlink"),
        ("infra/compose/minio/minio-init.sh", "symlink"),
    ],
)
def test_source_or_provider_input_change_fails(
    relative: str,
    action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.change_after_harness = (root / relative, action)

    assert _run(root, fake, monkeypatch) == 1


@pytest.mark.parametrize(
    "relative",
    [
        "infra/images.lock",
        "infra/compose/minio/minio-init.sh",
        "apps/api/tests/integration/audit_raw_runtime_acceptance.py",
        "apps/api/tests/integration/audit_raw_runtime_probe.py",
        "apps/api/tests/integration/audit_isolated_raw_runtime_acceptance.py",
        "apps/api/tests/integration/audit_isolated_raw_runtime_probe.py",
        "apps/api/tests/integration/audit_version_page_runtime_acceptance.py",
        "apps/api/tests/integration/audit_version_page_runtime_probe.py",
        "apps/api/tests/fixtures/audit_bootstrap_bridge_vectors.json",
    ],
)
def test_provider_input_symlink_is_rejected_before_build(
    relative: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    path = root / relative
    target = path.with_name(path.name + ".target")
    _write(target)
    path.unlink()
    path.symlink_to(target)
    fake = _FakeCommands(root)

    assert _run(root, fake, monkeypatch) == 1
    assert all(call[0][:2] != ["/tools/docker", "build"] for call in fake.calls)


def test_cleanup_refuses_container_whose_session_label_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.container_label_matches = False

    assert _run(root, fake, monkeypatch) == 1
    assert all(call[0][:3] != ["/tools/docker", "rm", "-f"] for call in fake.calls)


@pytest.mark.parametrize("failure", ["container", "image", "image_inspect"])
def test_cleanup_error_cannot_return_success(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    if failure == "container":
        fake.container_remove_returncode = 1
    elif failure == "image":
        fake.image_remove_returncode = 1
    else:
        fake.cleanup_inspect_mode = "daemon_error"

    assert _run(root, fake, monkeypatch) == 1


class _FakeProcess:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.pid = 4242
        self.returncode = 0
        self.communications: list[int | None] = []
        self.done = False

    def poll(self) -> int | None:
        return self.returncode if self.done else None

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        self.communications.append(timeout)
        if len(self.communications) == 1:
            if self.mode == "timeout":
                raise subprocess.TimeoutExpired(["controlled"], timeout)
            raise _RUNNER._SignalReceived(signal.SIGTERM)
        if self.mode == "timeout" and len(self.communications) == 2:
            raise subprocess.TimeoutExpired(["controlled"], timeout)
        self.done = True
        return "", ""


@pytest.mark.parametrize(
    ("mode", "expected_signal_sequence"),
    [
        ("timeout", [signal.SIGTERM, signal.SIGKILL]),
        ("signal", [signal.SIGTERM]),
    ],
)
def test_command_timeout_and_signal_terminate_only_the_fake_process_group(
    mode: str,
    expected_signal_sequence: list[signal.Signals],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(mode)
    popen_arguments: list[tuple[list[str], dict[str, object]]] = []
    kill_calls: list[tuple[int, signal.Signals]] = []

    def fake_popen(argv: list[str], **kwargs: object) -> _FakeProcess:
        popen_arguments.append((argv, kwargs))
        return process

    monkeypatch.setattr(_RUNNER.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        _RUNNER.os,
        "killpg",
        lambda pid, signum: kill_calls.append((pid, signum)),
    )

    expected = _RUNNER.AcceptanceError if mode == "timeout" else _RUNNER._SignalReceived
    with pytest.raises(expected):
        _RUNNER._run_command(["/controlled/tool", "argument"], cwd=tmp_path, timeout=1_200)

    assert popen_arguments[0][0] == ["/controlled/tool", "argument"]
    assert popen_arguments[0][1]["start_new_session"] is True
    assert kill_calls == [(4242, signum) for signum in expected_signal_sequence]
