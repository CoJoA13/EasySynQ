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
from xml.sax.saxutils import escape

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
_HISTORY_NAME = (
    "test_history_collection_runtime_preserves_required_witnesses_and_resource_boundaries"
)
_HISTORY_FAILURE_PREFIX = "AUDIT_HISTORY_COLLECTION_FAILURE "
_HISTORY_SOURCE = "audit_history_collection_runtime_acceptance.py"
_PROVIDER_RECEIPT_PREFIX = "provider exact-read multiset differs; history_provider_v1 "
_MANDATORY_NAMES = (
    "test_external_cli_runtime_is_public_only_and_read_only",
    "test_external_cli_runtime_preserves_enrolled_obligation_after_db_selection_attack",
    "test_external_cli_runtime_accepts_historical_target_with_newer_witness",
    "test_raw_version_runtime_preserves_exact_provider_bytes_and_bridge",
    "test_raw_version_runtime_enforces_routing_and_tls",
    "test_raw_version_runtime_bounds_streams_and_cleans_up",
    "test_isolated_raw_runtime_enforces_process_and_byte_boundaries",
    "test_version_page_decoder_runtime_rejects_lossy_provider_pages",
    "test_version_page_transport_runtime_preserves_original_observations_and_limits",
    _HISTORY_NAME,
)
_RESOURCE_SUBCASES = (
    "cases/memory",
    "cases/descriptors",
    "cases/file-limit",
    "cases/cpu",
    "cases/heap",
    "cases/store",
    "cases/disk-journal",
    "cases/disk-sort",
    "open_transaction_death",
    "cleanup_failure",
    "upload_deadline",
    "ipc/valid-result",
    "ipc/oversized",
    "ipc/truncated",
    "ipc/flood",
    "ipc/stale",
    "ipc/late-output",
    "ipc/withheld-eof",
    "raw_worker/valid-empty",
    "raw_worker/oversized-input",
    "raw_worker/truncated-input",
    "raw_worker/stale-input",
    "raw_worker/duplicate-field",
    "raw_worker/chunk-sequence",
    "raw_worker/trailing-chunk",
    "raw_worker/entry-over",
    "raw_worker/xml-over",
    "raw_worker/body-over",
)
# Current Dockerfile COPY sources plus the Dockerfile and context-exclusion policy.
_BUILD_FILE_INPUTS = (
    ".dockerignore",
    "apps/api/Dockerfile",
    "apps/api/pyproject.toml",
    "apps/api/uv.lock",
    "apps/api/LICENSE",
    "apps/api/alembic.ini",
)
_BUILD_TREE_SAMPLES = {
    "apps/api/src": "apps/api/src/easysynq_api/__init__.py",
    "migrations": "migrations/001.py",
}


def _write(path: Path, value: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    for relative in (
        *_BUILD_FILE_INPUTS,
        *_BUILD_TREE_SAMPLES.values(),
        "scripts/run-audit-external-acceptance.py",
        "apps/api/tests/conftest.py",
        "apps/api/tests/integration/audit_external_runtime_acceptance.py",
        "apps/api/tests/integration/audit_raw_runtime_acceptance.py",
        "apps/api/tests/integration/audit_raw_runtime_probe.py",
        "apps/api/tests/integration/audit_isolated_raw_runtime_acceptance.py",
        "apps/api/tests/integration/audit_isolated_raw_runtime_probe.py",
        "apps/api/tests/integration/audit_version_page_runtime_acceptance.py",
        "apps/api/tests/integration/audit_version_page_runtime_probe.py",
        "apps/api/tests/integration/audit_version_page_transport_runtime_acceptance.py",
        "apps/api/tests/integration/audit_version_page_transport_runtime_probe.py",
        "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py",
        "apps/api/tests/integration/audit_history_collection_runtime_probe.py",
        "apps/api/tests/fixtures/audit_bootstrap_bridge_vectors.json",
        "apps/api/tests/fixtures/audit_history_collection_vectors.json",
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


def _history_junit(
    detail: str,
    *,
    status: str = "failure",
    name: str = _HISTORY_NAME,
    copies: int = 1,
) -> str:
    cases = [f'<testcase name="{item}" />' for item in _MANDATORY_NAMES if item != _HISTORY_NAME]
    child = (
        f'<{status} message="private-message">{escape(detail)}</{status}>'
        "<system-out>private-junit-output</system-out>"
        "<system-err>private-junit-error</system-err>"
    )
    cases.extend(
        f'<testcase name="{name}" classname="private-class" file="private-file" '
        f'time="private-time">{child}</testcase>'
        for _ in range(copies)
    )
    return (
        '<testsuites><testsuite name="private-suite">'
        '<properties><property name="token" value="private-property" /></properties>'
        + "".join(cases)
        + "</testsuite></testsuites>"
    )


def _history_stdout(
    phase: str,
    *,
    completed: dict[str, int] | None = None,
    resource_subcases: dict[str, int] | None = None,
    phase_elapsed_ms: object = 23,
    total_elapsed_ms: object = 101,
    progress: str = ".........",
) -> str:
    payload = {
        "completed_phases_ms": {} if completed is None else completed,
        "phase": phase,
        "phase_elapsed_ms": phase_elapsed_ms,
        "resource_subcases_ms": {} if resource_subcases is None else resource_subcases,
        "total_elapsed_ms": total_elapsed_ms,
    }
    return (
        progress
        + _HISTORY_FAILURE_PREFIX
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )


def _provider_receipt(**overrides: str) -> str:
    fields = {
        "outcome": "report",
        "status": "traversed",
        "elapsed_ms": "42",
        "attempted_reads": "2",
        "returned_reads": "2",
        "attempted_pages": "1",
        "returned_pages": "1",
        "expected_reads": "2",
        "locator_matches": "2",
        "exact_matches": "2",
        "terminal_witnesses": "2",
        "unavailable_reads": "0",
    }
    fields.update(overrides)
    return _PROVIDER_RECEIPT_PREFIX + " ".join(f"{name}={value}" for name, value in fields.items())


def _history_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("runtime_history_")]


def test_history_source_line_cap_matches_formatted_fixture() -> None:
    source = _REPOSITORY_ROOT / "apps/api/tests/integration" / _HISTORY_SOURCE

    assert _RUNNER._HISTORY_SOURCE_MAX_LINE == len(source.read_text(encoding="utf-8").splitlines())


class _FakeCommands:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[list[str], Path, int, dict[str, str] | None]] = []
        self.build_returncode = 0
        self.harness_returncode = 0
        self.harness_stdout = "private-child-stdout"
        self.harness_stderr = "private-child-stderr"
        self.harness_timeout = False
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
            if self.harness_timeout:
                raise _RUNNER.AcceptanceError("command timed out")
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
                elif action == "missing":
                    path.unlink()
                else:
                    target = path.with_name(path.name + ".target")
                    target.write_text("target\n", encoding="utf-8")
                    path.unlink()
                    path.symlink_to(target)
            return self._result(
                self.harness_returncode,
                stdout=self.harness_stdout,
                stderr=self.harness_stderr,
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
    assert harness[0][:11] == [
        "/tools/uv",
        "run",
        "--project",
        str(root / "apps/api"),
        "pytest",
        "tests/integration/audit_external_runtime_acceptance.py",
        "tests/integration/audit_raw_runtime_acceptance.py",
        "tests/integration/audit_isolated_raw_runtime_acceptance.py",
        "tests/integration/audit_version_page_runtime_acceptance.py",
        "tests/integration/audit_version_page_transport_runtime_acceptance.py",
        "tests/integration/audit_history_collection_runtime_acceptance.py"
        "::test_history_collection_runtime_preserves_required_witnesses_and_resource_boundaries",
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
    assert "mandatory_tests=10" in output
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
    ("mode", "message"),
    [
        ("missing", "missing a mandatory test"),
        ("substituted", "missing a mandatory test"),
        ("skipped", "contains a nonpassing test"),
        ("failure", "contains a nonpassing test"),
        ("error", "contains a nonpassing test"),
    ],
)
def test_transport_runtime_case_cannot_pass_incomplete_junit(
    mode: str,
    message: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.xml"
    path.write_text(_junit(mode=mode, affected=8), encoding="utf-8")

    with pytest.raises(_RUNNER.AcceptanceError, match=message):
        _RUNNER._validate_junit(path)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("missing", "missing a mandatory test"),
        ("substituted", "missing a mandatory test"),
        ("skipped", "contains a nonpassing test"),
        ("failure", "contains a nonpassing test"),
        ("error", "contains a nonpassing test"),
    ],
)
def test_collection_runtime_case_cannot_pass_incomplete_junit(
    mode: str,
    message: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.xml"
    path.write_text(_junit(mode=mode, affected=9), encoding="utf-8")

    with pytest.raises(_RUNNER.AcceptanceError, match=message):
        _RUNNER._validate_junit(path)


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
    assert output.out.count("runtime_case=") == 10
    assert "private-" not in output.out + output.err
    assert "secret-never-print" not in output.out + output.err
    assert "runtime_acceptance=failed" in output.out
    assert list((root / ".pytest_cache").iterdir()) == []
    assert any(call[0][:3] == ["/tools/docker", "rm", "-f"] for call in fake.calls)
    assert any(call[0][:4] == ["/tools/docker", "image", "rm", "-f"] for call in fake.calls)


@pytest.mark.parametrize(
    ("phase", "status", "progress", "completed", "with_resources"),
    [
        ("identity", "failure", "", {}, False),
        ("resources", "error", ".sFxXE", {"synthetic": 11}, True),
        (
            "provider",
            "failure",
            ".........",
            {"synthetic": 11, "resources": 22, "certifi": 33},
            True,
        ),
    ],
)
def test_failed_history_case_emits_only_validated_phase_timings_and_source(
    phase: str,
    status: str,
    progress: str,
    completed: dict[str, int],
    with_resources: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    resource_subcases = {name: 7 for name in _RESOURCE_SUBCASES} if with_resources else {}
    fake.harness_stdout = (
        "private-before\n"
        + _history_stdout(
            phase,
            completed=completed,
            resource_subcases=resource_subcases,
            phase_elapsed_ms=23,
            total_elapsed_ms=101,
            progress=progress,
        )
        + "\nprivate-after"
    )
    fake.harness_stderr = "private-harness-stderr"
    fake.junit_content = _history_junit(
        "private assertion\n"
        "apps/api/tests/integration/"
        f"{_HISTORY_SOURCE}:1269: RuntimeError\n"
        "/private-host/private-project/apps/api/tests/integration/"
        f"{_HISTORY_SOURCE}:1283: AssertionError",
        status=status,
    )

    assert _run(root, fake, monkeypatch) == 1

    output = capsys.readouterr()
    expected = [
        "runtime_history_diagnostic=available",
        f"runtime_history_phase={phase}",
        "runtime_history_phase_elapsed_ms=23",
        "runtime_history_total_elapsed_ms=101",
    ]
    expected.extend(
        f"runtime_history_completed_phase={name} elapsed_ms={elapsed}"
        for name, elapsed in completed.items()
    )
    expected.extend(
        f"runtime_history_resource_subcase={name} elapsed_ms=7" for name in resource_subcases
    )
    expected.extend(
        [
            "runtime_history_provider_diagnostic=unavailable",
            f"runtime_history_failure_source={_HISTORY_SOURCE}:1283",
        ]
    )
    assert _history_lines(output.out) == expected
    assert "private-" not in output.out + output.err
    assert _HISTORY_FAILURE_PREFIX not in output.out + output.err
    assert "/private-host/" not in output.out + output.err
    assert list((root / ".pytest_cache").iterdir()) == []


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "duplicate-prefix",
        "oversized",
        "malformed",
        "duplicate-key",
        "noncanonical",
        "extra-key",
        "missing-key",
        "invalid-phase",
        "string-duration",
        "float-duration",
        "bool-duration",
        "negative-duration",
        "over-bound-duration",
        "nested-bool-duration",
        "subordinate-over-total",
        "total-bool-duration",
        "completed-over-total",
        "resource-bool-duration",
        "resource-over-total",
        "inconsistent-completed-prefix",
        "unknown-completed-phase",
        "unknown-resource-subcase",
        "incomplete-resource-subcases",
        "invalid-progress-prefix",
        "trailing-text",
    ],
)
def test_failed_history_case_rejects_malformed_or_ambiguous_stdout_diagnostic(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    payload: dict[str, object] = {
        "completed_phases_ms": {"synthetic": 11, "resources": 22, "certifi": 33},
        "phase": "provider",
        "phase_elapsed_ms": 23,
        "resource_subcases_ms": {},
        "total_elapsed_ms": 101,
    }
    if case == "absent":
        stdout = "private-child-stdout"
    elif case == "duplicate-prefix":
        valid = _history_stdout("provider", completed=payload["completed_phases_ms"])
        stdout = valid + "\n" + valid
    elif case == "oversized":
        stdout = _HISTORY_FAILURE_PREFIX + json.dumps(payload) + (" " * 4097)
    elif case == "malformed":
        stdout = _HISTORY_FAILURE_PREFIX + "{private-unclosed"
    elif case == "duplicate-key":
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        stdout = _HISTORY_FAILURE_PREFIX + canonical.replace(
            '"phase":"provider"', '"phase":"provider","phase":"identity"'
        )
    else:
        if case == "extra-key":
            payload["private-extra"] = "private-value"
        elif case == "missing-key":
            payload.pop("phase_elapsed_ms")
        elif case == "invalid-phase":
            payload["phase"] = "private-phase"
        elif case == "string-duration":
            payload["phase_elapsed_ms"] = "23"
        elif case == "float-duration":
            payload["phase_elapsed_ms"] = 23.0
        elif case == "bool-duration":
            payload["phase_elapsed_ms"] = True
        elif case == "negative-duration":
            payload["phase_elapsed_ms"] = -1
        elif case == "over-bound-duration":
            payload["phase_elapsed_ms"] = 1_200_001
        elif case == "nested-bool-duration":
            payload["completed_phases_ms"] = {
                "synthetic": True,
                "resources": 22,
                "certifi": 33,
            }
        elif case == "subordinate-over-total":
            payload["phase_elapsed_ms"] = 102
        elif case == "total-bool-duration":
            payload["total_elapsed_ms"] = True
        elif case == "completed-over-total":
            payload["completed_phases_ms"] = {
                "synthetic": 102,
                "resources": 22,
                "certifi": 33,
            }
        elif case == "resource-bool-duration":
            payload["resource_subcases_ms"] = {
                name: True if name == "cases/memory" else 7 for name in _RESOURCE_SUBCASES
            }
        elif case == "resource-over-total":
            payload["resource_subcases_ms"] = {
                name: 102 if name == "cases/memory" else 7 for name in _RESOURCE_SUBCASES
            }
        elif case == "inconsistent-completed-prefix":
            payload["completed_phases_ms"] = {"synthetic": 11}
        elif case == "unknown-completed-phase":
            payload["completed_phases_ms"] = {
                "synthetic": 11,
                "resources": 22,
                "private-phase": 33,
            }
        elif case == "unknown-resource-subcase":
            payload["resource_subcases_ms"] = {"private-subcase": 7}
        elif case == "incomplete-resource-subcases":
            payload["resource_subcases_ms"] = {"cases/memory": 7}
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if case == "noncanonical":
            serialized = json.dumps(payload, sort_keys=True)
        stdout = _HISTORY_FAILURE_PREFIX + serialized
        if case == "invalid-progress-prefix":
            stdout = "private-progress" + stdout
        elif case == "trailing-text":
            stdout += " private-trailing"
    fake.harness_stdout = stdout
    fake.junit_content = _history_junit(
        f"apps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError"
    )

    assert _run(root, fake, monkeypatch) == 1

    output = capsys.readouterr()
    assert _history_lines(output.out) == [
        "runtime_history_diagnostic=unavailable",
        "runtime_history_provider_diagnostic=unavailable",
        f"runtime_history_failure_source={_HISTORY_SOURCE}:1283",
    ]
    assert "private-" not in output.out + output.err
    assert _HISTORY_FAILURE_PREFIX not in output.out + output.err
    assert list((root / ".pytest_cache").iterdir()) == []


def test_failed_history_case_emits_only_validated_provider_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    fake.junit_content = _history_junit(
        "private before\n"
        "E       AssertionError: " + _provider_receipt() + "\nprivate after\n"
        f"apps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError"
    )

    assert _run(root, fake, monkeypatch) == 1

    assert _history_lines(capsys.readouterr().out) == [
        "runtime_history_diagnostic=unavailable",
        "runtime_history_provider_diagnostic=available",
        "runtime_history_provider_outcome=report",
        "runtime_history_provider_status=traversed",
        "runtime_history_provider_elapsed_ms=42",
        "runtime_history_provider_attempted_reads=2",
        "runtime_history_provider_returned_reads=2",
        "runtime_history_provider_attempted_pages=1",
        "runtime_history_provider_returned_pages=1",
        "runtime_history_provider_expected_reads=2",
        "runtime_history_provider_locator_matches=2",
        "runtime_history_provider_exact_matches=2",
        "runtime_history_provider_terminal_witnesses=2",
        "runtime_history_provider_unavailable_reads=0",
        f"runtime_history_failure_source={_HISTORY_SOURCE}:1283",
    ]


def test_failed_history_case_emits_deadline_receipt_without_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    receipt = _provider_receipt(
        outcome="DEADLINE_EXCEEDED",
        status="none",
        attempted_reads="2",
        returned_reads="1",
        attempted_pages="2",
        returned_pages="1",
        expected_reads="2",
        locator_matches="1",
        exact_matches="1",
        terminal_witnesses="none",
        unavailable_reads="none",
    )
    fake.junit_content = _history_junit(
        "E AssertionError: "
        + receipt
        + f"\napps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError"
    )

    assert _run(root, fake, monkeypatch) == 1

    assert _history_lines(capsys.readouterr().out) == [
        "runtime_history_diagnostic=unavailable",
        "runtime_history_provider_diagnostic=available",
        "runtime_history_provider_outcome=DEADLINE_EXCEEDED",
        "runtime_history_provider_status=none",
        "runtime_history_provider_elapsed_ms=42",
        "runtime_history_provider_attempted_reads=2",
        "runtime_history_provider_returned_reads=1",
        "runtime_history_provider_attempted_pages=2",
        "runtime_history_provider_returned_pages=1",
        "runtime_history_provider_expected_reads=2",
        "runtime_history_provider_locator_matches=1",
        "runtime_history_provider_exact_matches=1",
        "runtime_history_provider_terminal_witnesses=none",
        "runtime_history_provider_unavailable_reads=none",
        f"runtime_history_failure_source={_HISTORY_SOURCE}:1283",
    ]


def test_provider_receipt_ignores_oversized_unrelated_junit_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    fake.junit_content = _history_junit(
        "E AssertionError: "
        + _provider_receipt()
        + "\nprivate-surrounding="
        + ("x" * 1_025)
        + f"\napps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError"
    )

    assert _run(root, fake, monkeypatch) == 1

    assert tuple(
        line
        for line in _history_lines(capsys.readouterr().out)
        if line.startswith("runtime_history_provider_")
    ) == (
        "runtime_history_provider_diagnostic=available",
        "runtime_history_provider_outcome=report",
        "runtime_history_provider_status=traversed",
        "runtime_history_provider_elapsed_ms=42",
        "runtime_history_provider_attempted_reads=2",
        "runtime_history_provider_returned_reads=2",
        "runtime_history_provider_attempted_pages=1",
        "runtime_history_provider_returned_pages=1",
        "runtime_history_provider_expected_reads=2",
        "runtime_history_provider_locator_matches=2",
        "runtime_history_provider_exact_matches=2",
        "runtime_history_provider_terminal_witnesses=2",
        "runtime_history_provider_unavailable_reads=0",
    )


@pytest.mark.parametrize(
    "detail",
    [
        "private assertion",
        "E AssertionError: " + _provider_receipt(elapsed_ms="1200001"),
        "E AssertionError: " + _provider_receipt(elapsed_ms="99999999999999999999"),
        "E AssertionError: " + _provider_receipt(elapsed_ms="True"),
        "E AssertionError: " + _provider_receipt(elapsed_ms="01"),
        "E AssertionError: " + _provider_receipt(returned_reads="3"),
        "E AssertionError: " + _provider_receipt(locator_matches="3"),
        "E AssertionError: " + _provider_receipt(exact_matches="3"),
        "E AssertionError: " + _provider_receipt(outcome="private-error"),
        "E AssertionError: " + _provider_receipt(status="private-status"),
        "E AssertionError: " + _provider_receipt(status="none"),
        "E AssertionError: " + _provider_receipt(outcome="DEADLINE_EXCEEDED", status="traversed"),
        "E AssertionError: " + _provider_receipt() + " private-extra=secret",
        "E AssertionError: " + _provider_receipt() + "\nE AssertionError: " + _provider_receipt(),
        "E AssertionError: "
        + _provider_receipt()
        + "\nE AssertionError: "
        + _PROVIDER_RECEIPT_PREFIX
        + "private-malformed",
    ],
)
def test_failed_history_case_rejects_malformed_provider_receipts(
    detail: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    fake.junit_content = _history_junit(
        detail + f"\napps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError"
    )

    assert _run(root, fake, monkeypatch) == 1

    output = capsys.readouterr().out
    assert _history_lines(output) == [
        "runtime_history_diagnostic=unavailable",
        "runtime_history_provider_diagnostic=unavailable",
        f"runtime_history_failure_source={_HISTORY_SOURCE}:1283",
    ]
    assert "secret" not in output


def test_real_pytest_junit_provider_receipt_reaches_bounded_runner_consumer(
    tmp_path: Path,
) -> None:
    producer = tmp_path / "test_provider_receipt.py"
    report = tmp_path / "runtime.xml"
    producer.write_text(
        "from tests.integration import "
        "audit_history_collection_runtime_acceptance as acceptance\n\n"
        "def test_history_collection_runtime_preserves_required_witnesses_"
        "and_resource_boundaries():\n"
        "    acceptance._assert_runtime = lambda _result: None\n"
        "    expected = [['bucket-a', 'checkpoints/a', 'version-a', 3, 'a' * 64], "
        "['bucket-b', 'checkpoints/b', 'version-b', 5, 'b' * 64]]\n"
        "    case = {'outcome': 'report', 'report': {'status': 'traversed', 'witnesses': "
        "[{'terminal_reached': True, 'unavailable_reads': 0}, "
        "{'terminal_reached': True, 'unavailable_reads': 0}]}, 'elapsed_ms': 42, "
        "'reads': [expected[0]], 'attempted_exact_reads': 2, 'pages': [{}], "
        "'list_attempts': 1}\n"
        "    acceptance._assert_provider({'provider': case}, [], expected, [])\n",
        encoding="utf-8",
    )

    result = subprocess.run(  # noqa: S603 - fixed pytest argv and owned temporary fixture
        [sys.executable, "-m", "pytest", str(producer), "-q", "--junitxml", str(report)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        cwd=_REPOSITORY_ROOT / "apps/api",
    )

    assert result.returncode == 1
    assert report.is_file()
    summary = _RUNNER._runtime_failure_summary(report, 1)
    assert tuple(line for line in summary if line.startswith("runtime_history_provider_")) == (
        "runtime_history_provider_diagnostic=available",
        "runtime_history_provider_outcome=report",
        "runtime_history_provider_status=traversed",
        "runtime_history_provider_elapsed_ms=42",
        "runtime_history_provider_attempted_reads=2",
        "runtime_history_provider_returned_reads=1",
        "runtime_history_provider_attempted_pages=1",
        "runtime_history_provider_returned_pages=1",
        "runtime_history_provider_expected_reads=2",
        "runtime_history_provider_locator_matches=1",
        "runtime_history_provider_exact_matches=1",
        "runtime_history_provider_terminal_witnesses=2",
        "runtime_history_provider_unavailable_reads=0",
    )


@pytest.mark.parametrize(
    "gate",
    ["history-passed", "history-skipped", "history-missing", "parameterized", "other-failed"],
)
def test_history_diagnostics_require_one_exact_failed_or_errored_case(
    gate: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    fake.harness_stdout = _history_stdout("identity") + "\n" + _provider_receipt()
    frame = (
        "E AssertionError: "
        + _provider_receipt()
        + f"\napps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError"
    )
    if gate == "history-passed":
        fake.junit_content = _junit()
    elif gate == "other-failed":
        fake.junit_content = _junit(mode="failure", affected=0)
    elif gate == "history-skipped":
        fake.junit_content = _junit(mode="skipped", affected=9)
    elif gate == "history-missing":
        fake.junit_content = _junit(mode="missing", affected=9)
    else:
        fake.junit_content = _history_junit(frame, name=_HISTORY_NAME + "[private-param]")

    assert _run(root, fake, monkeypatch) == 1

    output = capsys.readouterr().out
    assert _history_lines(output) == []
    assert _HISTORY_FAILURE_PREFIX not in output


def test_duplicate_exact_failed_history_cases_make_both_diagnostics_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    fake.harness_stdout = _history_stdout("identity") + "\n" + _provider_receipt()
    fake.junit_content = _history_junit(
        "E AssertionError: "
        + _provider_receipt()
        + f"\napps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError",
        copies=2,
    )

    assert _run(root, fake, monkeypatch) == 1

    assert _history_lines(capsys.readouterr().out) == [
        "runtime_history_diagnostic=unavailable",
        "runtime_history_provider_diagnostic=unavailable",
        "runtime_history_failure_source=unavailable",
    ]
    assert list((root / ".pytest_cache").iterdir()) == []


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            "/private-host/project/apps/api/tests/integration/"
            "audit_history_collection_runtime_acceptance.py:291: AssertionError",
            "audit_history_collection_runtime_acceptance.py:291",
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:1: Error",
            "audit_history_collection_runtime_acceptance.py:1",
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:1484: Error",
            "audit_history_collection_runtime_acceptance.py:1484",
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:1269: "
            "RuntimeError\n"
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:1283: "
            "AssertionError",
            "audit_history_collection_runtime_acceptance.py:1283",
        ),
        ("apps/api/tests/integration/private.py:291: AssertionError", None),
        ("apps/api/tests/integration/audit_history_collection_runtime_probe.py:291: Error", None),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:0: Error",
            None,
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:+1: Error",
            None,
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:-1: Error",
            None,
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:01: Error",
            None,
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:1485: Error",
            None,
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:"
            + ("9" * 5_000)
            + ": Error",
            None,
        ),
        (
            "E AssertionError: apps/api/tests/integration/"
            "audit_history_collection_runtime_acceptance.py:291: private assertion",
            None,
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:291",
            None,
        ),
        (
            "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py:291:Error",
            None,
        ),
    ],
)
def test_history_source_reconstructs_only_complete_allowlisted_traceback_frames(
    detail: str,
    expected: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    fake.harness_stdout = "private-child-stdout"
    fake.junit_content = _history_junit(detail)

    assert _run(root, fake, monkeypatch) == 1

    source = "unavailable" if expected is None else expected
    output = capsys.readouterr()
    assert _history_lines(output.out) == [
        "runtime_history_diagnostic=unavailable",
        "runtime_history_provider_diagnostic=unavailable",
        f"runtime_history_failure_source={source}",
    ]
    assert "runtime_junit=available" in output.out
    assert output.out.count("runtime_case=") == 10
    assert "private-" not in output.out + output.err


def test_history_diagnostic_does_not_consume_valid_record_from_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    fake.harness_stdout = "private-child-stdout"
    fake.harness_stderr = _history_stdout("identity") + "\n" + _provider_receipt()
    fake.junit_content = _history_junit(
        f"apps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError"
    )

    assert _run(root, fake, monkeypatch) == 1

    output = capsys.readouterr().out
    assert _history_lines(output) == [
        "runtime_history_diagnostic=unavailable",
        "runtime_history_provider_diagnostic=unavailable",
        f"runtime_history_failure_source={_HISTORY_SOURCE}:1283",
    ]
    assert _HISTORY_FAILURE_PREFIX not in output


def test_input_recheck_failure_suppresses_valid_history_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_returncode = 1
    fake.harness_stdout = _history_stdout("identity")
    fake.junit_content = _history_junit(
        "E AssertionError: "
        + _provider_receipt()
        + f"\napps/api/tests/integration/{_HISTORY_SOURCE}:1283: AssertionError"
    )
    fake.change_after_harness = (root / "apps/api/LICENSE", "content")

    assert _run(root, fake, monkeypatch) == 1

    output = capsys.readouterr().out
    assert "failure_stage=input_recheck" in output
    assert _history_lines(output) == []
    assert _HISTORY_FAILURE_PREFIX not in output


def test_cleanup_only_failure_does_not_consume_forged_history_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_stdout = _history_stdout("identity")
    fake.container_remove_returncode = 1

    assert _run(root, fake, monkeypatch) == 1

    output = capsys.readouterr().out
    assert "cleanup_status=failed" in output
    assert _history_lines(output) == []
    assert _HISTORY_FAILURE_PREFIX not in output


def test_harness_timeout_has_no_partial_history_diagnostic_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.harness_timeout = True
    fake.harness_stdout = _history_stdout("identity")

    assert _run(root, fake, monkeypatch) == 1

    output = capsys.readouterr().out
    assert "runtime_harness_exit=unavailable" in output
    assert "runtime_junit=unavailable" in output
    assert _history_lines(output) == []
    assert _HISTORY_FAILURE_PREFIX not in output
    assert list((root / ".pytest_cache").iterdir()) == []


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
    fake.harness_stdout = _history_stdout("identity")
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
    assert _history_lines(output) == []
    assert _HISTORY_FAILURE_PREFIX not in output
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


@pytest.mark.parametrize("relative", (*_BUILD_FILE_INPUTS, *_BUILD_TREE_SAMPLES.values()))
def test_build_digest_changes_with_each_api_image_input(relative: str, tmp_path: Path) -> None:
    root = _repository(tmp_path)
    before = _RUNNER._build_manifest(root).digest
    (root / relative).write_text("changed image input\n", encoding="utf-8")

    assert _RUNNER._build_manifest(root).digest != before


def test_collection_vector_content_changes_proof_digest(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    before = _RUNNER._proof_manifest(root).digest
    (root / "apps/api/tests/fixtures/audit_history_collection_vectors.json").write_text(
        '{"changed": "collection vector bytes"}\n', encoding="utf-8"
    )

    assert _RUNNER._proof_manifest(root).digest != before


@pytest.mark.parametrize("unavailable", ["missing", "symlink"])
def test_collection_vector_unavailable_fails_before_build(
    unavailable: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    path = root / "apps/api/tests/fixtures/audit_history_collection_vectors.json"
    target = tmp_path / "outside-collection-vectors.json"
    path.rename(target)
    if unavailable == "symlink":
        path.symlink_to(target)
    fake = _FakeCommands(root)

    assert _run(root, fake, monkeypatch) == 1
    assert all(call[0][:2] != ["/tools/docker", "build"] for call in fake.calls)
    assert "failure_stage=input_manifest" in capsys.readouterr().out
    assert list((root / ".pytest_cache").iterdir()) == []


@pytest.mark.parametrize("relative", (*_BUILD_FILE_INPUTS, *_BUILD_TREE_SAMPLES))
@pytest.mark.parametrize("unavailable", ["missing", "symlink"])
def test_unavailable_build_input_fails_before_build_and_cleans_owned_directory(
    relative: str,
    unavailable: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    path = root / relative
    target = tmp_path / "outside-build-input"
    path.rename(target)
    if unavailable == "symlink":
        path.symlink_to(target, target_is_directory=target.is_dir())
    fake = _FakeCommands(root)

    assert _run(root, fake, monkeypatch) == 1
    assert all(call[0][:2] != ["/tools/docker", "build"] for call in fake.calls)
    assert "failure_stage=input_manifest" in capsys.readouterr().out
    assert list((root / ".pytest_cache").iterdir()) == []


@pytest.mark.parametrize(
    ("relative", "action"),
    [
        ("apps/api/LICENSE", "content"),
        ("apps/api/LICENSE", "missing"),
        ("apps/api/LICENSE", "symlink"),
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
        (
            "apps/api/tests/integration/audit_version_page_transport_runtime_acceptance.py",
            "content",
        ),
        ("apps/api/tests/integration/audit_version_page_transport_runtime_probe.py", "content"),
        ("apps/api/tests/integration/audit_history_collection_runtime_acceptance.py", "content"),
        ("apps/api/tests/integration/audit_history_collection_runtime_probe.py", "content"),
        ("apps/api/tests/fixtures/audit_bootstrap_bridge_vectors.json", "content"),
        ("apps/api/tests/fixtures/audit_history_collection_vectors.json", "content"),
        ("apps/api/tests/fixtures/audit_history_collection_vectors.json", "missing"),
        ("apps/api/tests/fixtures/audit_history_collection_vectors.json", "symlink"),
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
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repository(tmp_path)
    fake = _FakeCommands(root)
    fake.change_after_harness = (root / relative, action)

    assert _run(root, fake, monkeypatch) == 1
    assert any(call[0][0] == "/tools/uv" for call in fake.calls)
    assert "failure_stage=input_recheck" in capsys.readouterr().out
    assert list((root / ".pytest_cache").iterdir()) == []


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
        "apps/api/tests/integration/audit_version_page_transport_runtime_acceptance.py",
        "apps/api/tests/integration/audit_version_page_transport_runtime_probe.py",
        "apps/api/tests/integration/audit_history_collection_runtime_acceptance.py",
        "apps/api/tests/integration/audit_history_collection_runtime_probe.py",
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
