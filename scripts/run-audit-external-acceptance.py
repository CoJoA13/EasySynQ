#!/usr/bin/env python3
"""Build and run the exact external-audit custody acceptance, then clean owned resources."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

_BUILD_TIMEOUT_SECONDS = 1_200
_HARNESS_TIMEOUT_SECONDS = 1_200
_TERMINATE_GRACE_SECONDS = 10
_DIAGNOSTIC_JUNIT_MAX_BYTES = 1_048_576
_HISTORY_DIAGNOSTIC_MAX_RECORD_BYTES = 4_096
_HISTORY_DIAGNOSTIC_MAX_DURATION_MS = 1_200_000
_HISTORY_PROVIDER_RECEIPT_MAX_BYTES = 1_024
_RUN_LABEL = "com.easysynq.audit-external.run"
_SOURCE_LABEL = "com.easysynq.audit-external.source"
_SESSION_LABEL = "org.testcontainers.session-id"
_HISTORY_FAILURE_PREFIX = "AUDIT_HISTORY_COLLECTION_FAILURE "
_HISTORY_PROVIDER_RECEIPT_PREFIX = "provider exact-read multiset differs; history_provider_v1 "
_HISTORY_TEST_NAME = (
    "test_history_collection_runtime_preserves_required_witnesses_and_resource_boundaries"
)
_HISTORY_SOURCE_NAME = "audit_history_collection_runtime_acceptance.py"
_HISTORY_SOURCE_MAX_LINE = 1_484
_HISTORY_PHASES = (
    "identity",
    "synthetic",
    "resources",
    "certifi",
    "provider-budget",
    "provider",
)
_HISTORY_COMPLETED_PREFIXES = {
    "identity": (),
    "synthetic": (),
    "resources": ("synthetic",),
    "certifi": ("synthetic", "resources"),
    "provider-budget": ("synthetic", "resources", "certifi"),
    "provider": ("synthetic", "resources", "certifi"),
}
_HISTORY_RESOURCE_SUBCASES = (
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
_HISTORY_PROGRESS_RECORD = re.compile(
    rf"[.sFxXE]*{re.escape(_HISTORY_FAILURE_PREFIX)}(?P<payload>.+)\Z"
)
_HISTORY_SOURCE_FRAME = re.compile(
    rf"(?:/?(?:[A-Za-z0-9_.-]+/)*){re.escape(_HISTORY_SOURCE_NAME)}:"
    r"(?P<line>[1-9][0-9]{0,3}): .+\Z"
)
_HISTORY_PROVIDER_ASSERTION = re.compile(
    r"(?:E\s+)?AssertionError: (?P<receipt>provider exact-read multiset differs; "
    r"history_provider_v1 .+)\Z"
)
_HISTORY_PROVIDER_OUTCOMES = frozenset(
    {
        "report",
        "cancelled",
        "RESOURCE_LIMIT",
        "RUNTIME_UNSUPPORTED",
        "WORKER_START_FAILED",
        "WORKER_FAILED",
        "PROTOCOL_INVALID",
        "STORAGE_FAILED",
        "DEADLINE_EXCEEDED",
        "CLEANUP_FAILED",
    }
)
_HISTORY_PROVIDER_STATUSES = frozenset({"traversed", "failed", "incomplete"})
_HISTORY_PROVIDER_FIELDS = (
    "outcome",
    "status",
    "elapsed_ms",
    "attempted_reads",
    "returned_reads",
    "attempted_pages",
    "returned_pages",
    "expected_reads",
    "locator_matches",
    "exact_matches",
    "terminal_witnesses",
    "unavailable_reads",
)
_RECONCILIATION_TEST_NAME = (
    "test_history_reconciliation_runtime_preserves_global_closure_and_owned_limits"
)
_MANDATORY_TESTS = frozenset(
    {
        "test_external_cli_runtime_is_public_only_and_read_only",
        "test_external_cli_runtime_preserves_enrolled_obligation_after_db_selection_attack",
        "test_external_cli_runtime_accepts_historical_target_with_newer_witness",
        "test_raw_version_runtime_preserves_exact_provider_bytes_and_bridge",
        "test_raw_version_runtime_enforces_routing_and_tls",
        "test_raw_version_runtime_bounds_streams_and_cleans_up",
        "test_isolated_raw_runtime_enforces_process_and_byte_boundaries",
        "test_version_page_decoder_runtime_rejects_lossy_provider_pages",
        "test_version_page_transport_runtime_preserves_original_observations_and_limits",
        _HISTORY_TEST_NAME,
        _RECONCILIATION_TEST_NAME,
    }
)
_EXCLUDED_DIRECTORIES = frozenset({".pytest_cache", ".venv", "__pycache__"})
_HEX_ID = re.compile(r"[0-9a-f]{12,64}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SESSION_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


class AcceptanceError(RuntimeError):
    """A controlled runner failure whose child output must remain private."""


class _SignalReceived(BaseException):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclasses.dataclass(frozen=True, slots=True)
class _Manifest:
    digest: str
    paths: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class _Tools:
    docker: str
    uv: str
    git: str


_active_process: subprocess.Popen[str] | None = None


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.communicate(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.communicate()


def _run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    environ: Mapping[str, str] | None = None,
) -> _CommandResult:
    global _active_process
    if not argv or any(not isinstance(argument, str) for argument in argv):
        raise AcceptanceError("invalid command")
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv arrays, no shell
            list(argv),
            cwd=cwd,
            env=None if environ is None else dict(environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError:
        raise AcceptanceError("command start failed") from None
    _active_process = process
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            raise AcceptanceError("command timed out") from None
        return _CommandResult(process.returncode, stdout, stderr)
    except BaseException:
        _terminate_process(process)
        raise
    finally:
        _active_process = None


def _signal_handler(signum: int, _frame: object) -> NoReturn:
    raise _SignalReceived(signum)


def _required_tools() -> _Tools:
    resolved = {name: shutil.which(name) for name in ("docker", "uv", "git")}
    if any(value is None for value in resolved.values()):
        raise AcceptanceError("required tool unavailable")
    return _Tools(
        docker=str(resolved["docker"]),
        uv=str(resolved["uv"]),
        git=str(resolved["git"]),
    )


def _require_real_directory(path: Path, *, create: bool = False) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if not create:
            raise AcceptanceError("required directory unavailable") from None
        try:
            path.mkdir(mode=0o700)
            metadata = path.lstat()
        except OSError:
            raise AcceptanceError("acceptance cache unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise AcceptanceError("acceptance cache is not a real directory")


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        raise AcceptanceError("runner record write failed") from None


def _allocate_owned_directory(root: Path, run_id: str) -> Path:
    cache = root / ".pytest_cache"
    _require_real_directory(cache, create=True)
    try:
        owned = Path(tempfile.mkdtemp(prefix="audit-external-", dir=cache))
        owned.chmod(0o700)
    except OSError:
        raise AcceptanceError("owned directory allocation failed") from None
    try:
        _write_json_atomic(owned / "runner.json", {"run_id": run_id})
    except AcceptanceError:
        try:
            shutil.rmtree(owned)
        except OSError:
            pass
        raise
    return owned


def _assert_path_components(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise AcceptanceError("manifest input escaped repository") from None
    current = root
    for part in relative.parts:
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            raise AcceptanceError("manifest input unavailable") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise AcceptanceError("manifest input is a symlink")


def _regular_file_digest(root: Path, path: Path) -> str:
    _assert_path_components(root, path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise AcceptanceError("manifest input unavailable") from None
    digest = hashlib.sha256()
    try:
        try:
            metadata = os.fstat(descriptor)
        except OSError:
            raise AcceptanceError("manifest input unavailable") from None
        if not stat.S_ISREG(metadata.st_mode):
            raise AcceptanceError("manifest input is not a regular file")
        while True:
            try:
                chunk = os.read(descriptor, 1024 * 1024)
            except OSError:
                raise AcceptanceError("manifest input read failed") from None
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _walk_inputs(root: Path, directory: Path, *, python_only: bool) -> list[Path]:
    _assert_path_components(root, directory)
    _require_real_directory(directory)
    result: list[Path] = []
    for current_text, directory_names, file_names in os.walk(directory, followlinks=False):
        current = Path(current_text)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            candidate = current / name
            if candidate.is_symlink():
                raise AcceptanceError("manifest directory is a symlink")
            if name not in _EXCLUDED_DIRECTORIES:
                kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names):
            candidate = current / name
            if python_only and candidate.suffix != ".py":
                continue
            _regular_file_digest(root, candidate)
            result.append(candidate)
    return result


def _manifest(root: Path, paths: Sequence[Path]) -> _Manifest:
    unique = sorted(set(paths), key=lambda item: item.relative_to(root).as_posix())
    aggregate = hashlib.sha256()
    relative_paths: list[str] = []
    for path in unique:
        relative = path.relative_to(root).as_posix()
        content_digest = _regular_file_digest(root, path)
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(content_digest.encode("ascii"))
        aggregate.update(b"\n")
        relative_paths.append(relative)
    return _Manifest(aggregate.hexdigest(), tuple(relative_paths))


def _build_manifest(root: Path) -> _Manifest:
    fixed = [
        root / ".dockerignore",
        root / "apps/api/Dockerfile",
        root / "apps/api/pyproject.toml",
        root / "apps/api/uv.lock",
        root / "apps/api/LICENSE",
        root / "apps/api/alembic.ini",
    ]
    recursive = _walk_inputs(root, root / "apps/api/src", python_only=False)
    recursive.extend(_walk_inputs(root, root / "migrations", python_only=False))
    return _manifest(root, [*fixed, *recursive])


def _proof_manifest(root: Path) -> _Manifest:
    fixed = [
        root / "scripts/run-audit-external-acceptance.py",
        root / "infra/images.lock",
        root / "infra/compose/minio/minio-init.sh",
        root / "apps/api/tests/fixtures/audit_bootstrap_bridge_vectors.json",
        root / "apps/api/tests/fixtures/audit_history_collection_vectors.json",
        root / "apps/api/tests/fixtures/audit_history_reconciliation_vectors.json",
    ]
    tests = _walk_inputs(root, root / "apps/api/tests", python_only=True)
    return _manifest(root, [*fixed, *tests])


def _parse_json_list(text_value: str, *, context: str) -> list[object]:
    try:
        value = json.loads(text_value)
    except (json.JSONDecodeError, TypeError):
        raise AcceptanceError(f"{context} returned invalid metadata") from None
    if not isinstance(value, list):
        raise AcceptanceError(f"{context} returned invalid metadata")
    return value


def _inspect_image(
    result: _CommandResult,
    *,
    run_id: str,
    source_digest: str,
    tag: str,
) -> str:
    if result.returncode != 0:
        raise AcceptanceError("built image is unavailable")
    values = _parse_json_list(result.stdout, context="image inspect")
    if len(values) != 1 or not isinstance(values[0], dict):
        raise AcceptanceError("image inspect returned invalid metadata")
    metadata = values[0]
    image_id = metadata.get("Id")
    config = metadata.get("Config")
    repository_tags = metadata.get("RepoTags")
    if (
        not isinstance(image_id, str)
        or _IMAGE_ID.fullmatch(image_id) is None
        or not isinstance(config, dict)
        or not isinstance(repository_tags, list)
        or tag not in repository_tags
    ):
        raise AcceptanceError("image identity validation failed")
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        raise AcceptanceError("image labels are unavailable")
    if labels.get(_RUN_LABEL) != run_id or labels.get(_SOURCE_LABEL) != source_digest:
        raise AcceptanceError("image labels do not match this run")
    return image_id


def _validate_junit(path: Path) -> int:
    _require_real_directory(path.parent)
    _assert_path_components(path.parent, path)
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - parse only runner-owned pytest JUnit
    except (OSError, ET.ParseError):
        raise AcceptanceError("runtime JUnit is unavailable") from None
    names: set[str] = set()
    cases = list(root.iter("testcase"))
    if not cases:
        raise AcceptanceError("runtime JUnit contains no tests")
    for suite in root.iter("testsuite"):
        for field in ("failures", "errors", "skipped"):
            value = suite.attrib.get(field, "0")
            if not value.isdecimal() or int(value) != 0:
                raise AcceptanceError("runtime JUnit reports a nonpassing test")
    for case in cases:
        name = case.attrib.get("name", "")
        names.add(name.split("[", 1)[0])
        if any(case.find(kind) is not None for kind in ("failure", "error", "skipped")):
            raise AcceptanceError("runtime JUnit contains a nonpassing test")
    if not _MANDATORY_TESTS.issubset(names):
        raise AcceptanceError("runtime JUnit is missing a mandatory test")
    return len(cases)


def _read_diagnostic_junit(path: Path) -> ET.Element:
    """Read a bounded regular report without following links or blocking on a FIFO."""
    _require_real_directory(path.parent)
    _assert_path_components(path.parent, path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as report:
        metadata = os.fstat(report.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise AcceptanceError("diagnostic report is not a regular file")
        if metadata.st_size > _DIAGNOSTIC_JUNIT_MAX_BYTES:
            raise AcceptanceError("diagnostic report is too large")
        payload = report.read(_DIAGNOSTIC_JUNIT_MAX_BYTES + 1)
    if len(payload) > _DIAGNOSTIC_JUNIT_MAX_BYTES:
        raise AcceptanceError("diagnostic report is too large")
    content = payload.decode("utf-8")
    if "<!DOCTYPE" in content or "<!ENTITY" in content:
        raise AcceptanceError("diagnostic report contains declarations")
    root = ET.fromstring(content)  # noqa: S314 - bounded UTF-8; DTD/entities rejected
    if root.tag not in {"testsuites", "testsuite"}:
        raise AcceptanceError("diagnostic report has an unexpected root")
    return root


def _history_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate history diagnostic key")
        value[key] = item
    return value


def _is_history_duration(value: object, total: int | None = None) -> bool:
    return (
        type(value) is int
        and 0 <= value <= _HISTORY_DIAGNOSTIC_MAX_DURATION_MS
        and (total is None or value <= total)
    )


def _history_phase_diagnostic(stdout: str) -> tuple[str, ...]:
    unavailable = ("runtime_history_diagnostic=unavailable",)
    try:
        if stdout.count(_HISTORY_FAILURE_PREFIX) != 1:
            return unavailable
        record = next(line for line in stdout.splitlines() if _HISTORY_FAILURE_PREFIX in line)
        match = _HISTORY_PROGRESS_RECORD.fullmatch(record)
        if match is None or len(record.encode("utf-8")) > _HISTORY_DIAGNOSTIC_MAX_RECORD_BYTES:
            return unavailable
        payload = match.group("payload")
        value = json.loads(payload, object_pairs_hook=_history_json_object)
        if (
            not isinstance(value, dict)
            or json.dumps(value, sort_keys=True, separators=(",", ":")) != payload
        ):
            return unavailable
        if set(value) != {
            "completed_phases_ms",
            "phase",
            "phase_elapsed_ms",
            "resource_subcases_ms",
            "total_elapsed_ms",
        }:
            return unavailable
        phase = value["phase"]
        total = value["total_elapsed_ms"]
        phase_elapsed = value["phase_elapsed_ms"]
        completed = value["completed_phases_ms"]
        resource_subcases = value["resource_subcases_ms"]
        if (
            phase not in _HISTORY_PHASES
            or not _is_history_duration(total)
            or not _is_history_duration(phase_elapsed, total)
            or not isinstance(completed, dict)
            or not isinstance(resource_subcases, dict)
        ):
            return unavailable
        expected_completed = _HISTORY_COMPLETED_PREFIXES[phase]
        if set(completed) != set(expected_completed):
            return unavailable
        if set(resource_subcases) not in (set(), set(_HISTORY_RESOURCE_SUBCASES)):
            return unavailable
        if not all(_is_history_duration(item, total) for item in completed.values()):
            return unavailable
        if not all(_is_history_duration(item, total) for item in resource_subcases.values()):
            return unavailable
        lines = [
            "runtime_history_diagnostic=available",
            f"runtime_history_phase={phase}",
            f"runtime_history_phase_elapsed_ms={phase_elapsed}",
            f"runtime_history_total_elapsed_ms={total}",
        ]
        lines.extend(
            f"runtime_history_completed_phase={name} elapsed_ms={completed[name]}"
            for name in expected_completed
        )
        lines.extend(
            f"runtime_history_resource_subcase={name} elapsed_ms={resource_subcases[name]}"
            for name in _HISTORY_RESOURCE_SUBCASES
            if resource_subcases
        )
        return tuple(lines)
    except Exception:  # noqa: BLE001 - malformed child stdout is always unavailable
        return unavailable


def _history_failure_source(case: ET.Element) -> str:
    child = case.find("error")
    if child is None:
        child = case.find("failure")
    if child is None:
        return "runtime_history_failure_source=unavailable"
    source: str | None = None
    for line in "".join(child.itertext()).splitlines():
        match = _HISTORY_SOURCE_FRAME.fullmatch(line)
        if match is None:
            continue
        line_number = int(match.group("line"))
        if line_number <= _HISTORY_SOURCE_MAX_LINE:
            source = f"{_HISTORY_SOURCE_NAME}:{line_number}"
    return (
        "runtime_history_failure_source=unavailable"
        if source is None
        else f"runtime_history_failure_source={source}"
    )


def _history_provider_diagnostic(case: ET.Element) -> tuple[str, ...]:
    unavailable = ("runtime_history_provider_diagnostic=unavailable",)
    try:
        child = case.find("error")
        if child is None:
            child = case.find("failure")
        if child is None:
            return unavailable
        records = []
        for line in "".join(child.itertext()).splitlines():
            if _HISTORY_PROVIDER_RECEIPT_PREFIX not in line:
                continue
            if len(line.encode("utf-8")) > _HISTORY_PROVIDER_RECEIPT_MAX_BYTES:
                return unavailable
            match = _HISTORY_PROVIDER_ASSERTION.fullmatch(line)
            if match is None:
                return unavailable
            records.append(match.group("receipt"))
        if len(records) != 1:
            return unavailable
        receipt = records[0]
        if len(receipt.encode("utf-8")) > _HISTORY_PROVIDER_RECEIPT_MAX_BYTES:
            return unavailable
        if not receipt.startswith(_HISTORY_PROVIDER_RECEIPT_PREFIX):
            return unavailable
        parts = receipt[len(_HISTORY_PROVIDER_RECEIPT_PREFIX) :].split(" ")
        if len(parts) != len(_HISTORY_PROVIDER_FIELDS):
            return unavailable
        values: dict[str, str] = {}
        for field, part in zip(_HISTORY_PROVIDER_FIELDS, parts, strict=True):
            key, separator, value = part.partition("=")
            if key != field or separator != "=" or not value:
                return unavailable
            values[field] = value

        def decimal(field: str, maximum: int) -> int:
            value = values[field]
            if (
                len(value) > 7
                or not value.isascii()
                or not value.isdecimal()
                or (len(value) > 1 and value.startswith("0"))
            ):
                raise ValueError
            number = int(value)
            if number > maximum:
                raise ValueError
            return number

        outcome = values["outcome"]
        status = values["status"]
        if outcome not in _HISTORY_PROVIDER_OUTCOMES:
            return unavailable
        decimal("elapsed_ms", _HISTORY_DIAGNOSTIC_MAX_DURATION_MS)
        attempted_reads = decimal("attempted_reads", 6_000)
        returned_reads = decimal("returned_reads", 6_000)
        attempted_pages = decimal("attempted_pages", 16)
        returned_pages = decimal("returned_pages", 16)
        expected_reads = decimal("expected_reads", 6_000)
        locator_matches = decimal("locator_matches", 6_000)
        exact_matches = decimal("exact_matches", 6_000)
        if (
            returned_reads > attempted_reads
            or returned_pages > attempted_pages
            or locator_matches > returned_reads
            or locator_matches > expected_reads
            or exact_matches > locator_matches
        ):
            return unavailable
        if status == "none":
            if (
                outcome == "report"
                or values["terminal_witnesses"] != "none"
                or values["unavailable_reads"] != "none"
            ):
                return unavailable
        else:
            if outcome != "report" or status not in _HISTORY_PROVIDER_STATUSES:
                return unavailable
            decimal("terminal_witnesses", 2)
            decimal("unavailable_reads", 6_000)
        lines = ["runtime_history_provider_diagnostic=available"]
        lines.extend(
            f"runtime_history_provider_{field}={values[field]}"
            for field in _HISTORY_PROVIDER_FIELDS
        )
        return tuple(lines)
    except Exception:  # noqa: BLE001 - malformed JUnit detail is never a diagnostic channel
        return unavailable


def _history_diagnostics(root: ET.Element, stdout: str) -> tuple[str, ...]:
    failed_cases = [
        case
        for case in root.iter("testcase")
        if case.attrib.get("name") == _HISTORY_TEST_NAME
        and (case.find("error") is not None or case.find("failure") is not None)
    ]
    if not failed_cases:
        return ()
    if len(failed_cases) != 1:
        return (
            "runtime_history_diagnostic=unavailable",
            "runtime_history_provider_diagnostic=unavailable",
            "runtime_history_failure_source=unavailable",
        )
    return (
        *_history_phase_diagnostic(stdout),
        *_history_provider_diagnostic(failed_cases[0]),
        _history_failure_source(failed_cases[0]),
    )


def _runtime_failure_summary(
    path: Path, returncode: int | None, harness_stdout: str = ""
) -> tuple[str, ...]:
    """Expose only fixed outcomes and known case names, never child/report details."""
    # uv can fail before pytest starts; an exit code alone does not identify its cause.
    if returncode is None:
        exit_status = "unavailable"
    elif -255 <= returncode <= 255:
        exit_status = str(returncode)
    else:
        exit_status = "abnormal_exit"
    header = (f"runtime_harness_exit={exit_status}",)
    try:
        root = _read_diagnostic_junit(path)
        statuses: dict[str, set[str]] = {name: set() for name in _MANDATORY_TESTS}
        other_cases = False
        for case in root.iter("testcase"):
            name = case.attrib.get("name", "").split("[", 1)[0]
            if name not in statuses:
                other_cases = True
                continue
            status = next(
                (
                    label
                    for tag, label in (
                        ("error", "error"),
                        ("failure", "failed"),
                        ("skipped", "skipped"),
                    )
                    if case.find(tag) is not None
                ),
                "passed",
            )
            statuses[name].add(status)
        lines = [
            "runtime_junit=available",
            f"runtime_other_cases={'present' if other_cases else 'absent'}",
        ]
        for name in sorted(_MANDATORY_TESTS):
            # Multiple parameterizations or teardown records cannot hide a failure.
            status = next(
                (
                    label
                    for label in ("error", "failed", "skipped", "passed")
                    if label in statuses[name]
                ),
                "missing",
            )
            lines.append(f"runtime_case={name} status={status}")
        return (*header, *lines, *_history_diagnostics(root, harness_stdout))
    except Exception:  # noqa: BLE001 - diagnostics must not leak or prevent owned cleanup
        return (*header, "runtime_junit=unavailable")


def _read_resource_record(path: Path, run_id: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise AcceptanceError("owned resource record unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceError("owned resource record invalid") from None
    if not isinstance(value, dict) or set(value) != {"run_id", "session_id"}:
        raise AcceptanceError("owned resource record invalid")
    session_id = value.get("session_id")
    if (
        value.get("run_id") != run_id
        or not isinstance(session_id, str)
        or _SESSION_ID.fullmatch(session_id) is None
    ):
        raise AcceptanceError("owned resource record identity mismatch")
    return session_id


def _cleanup_containers(
    docker: str,
    root: Path,
    resource_record: Path,
    run_id: str,
    *,
    harness_started: bool,
) -> None:
    _require_real_directory(resource_record.parent)
    if not resource_record.exists():
        if harness_started:
            raise AcceptanceError("owned resource record was not created")
        return
    session_id = _read_resource_record(resource_record, run_id)
    listed = _run_command(
        [docker, "ps", "-aq", "--filter", f"label={_SESSION_LABEL}={session_id}"],
        cwd=root,
        timeout=60,
    )
    if listed.returncode != 0:
        raise AcceptanceError("owned container listing failed")
    container_ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    for container_id in container_ids:
        if _HEX_ID.fullmatch(container_id) is None:
            raise AcceptanceError("owned container listing returned invalid identity")
        inspected = _run_command(
            [docker, "container", "inspect", container_id], cwd=root, timeout=60
        )
        if inspected.returncode != 0:
            raise AcceptanceError("owned container inspection failed")
        values = _parse_json_list(inspected.stdout, context="container inspect")
        if len(values) != 1 or not isinstance(values[0], dict):
            raise AcceptanceError("container inspect returned invalid metadata")
        config = values[0].get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if not isinstance(labels, dict) or labels.get(_SESSION_LABEL) != session_id:
            raise AcceptanceError("container ownership changed before cleanup")
        removed = _run_command([docker, "rm", "-f", container_id], cwd=root, timeout=120)
        if removed.returncode != 0:
            raise AcceptanceError("owned container cleanup failed")


def _cleanup_image(
    docker: str,
    root: Path,
    *,
    tag: str,
    run_id: str,
    source_digest: str,
    expected_image_id: str | None,
    build_started: bool,
) -> None:
    if not build_started:
        return
    inspected = _run_command([docker, "image", "inspect", tag], cwd=root, timeout=60)
    if inspected.returncode != 0:
        output = f"{inspected.stdout}\n{inspected.stderr}".lower()
        if "no such image" in output:
            return
        raise AcceptanceError("owned image inspection failed")
    image_id = _inspect_image(inspected, run_id=run_id, source_digest=source_digest, tag=tag)
    if expected_image_id is not None and image_id != expected_image_id:
        raise AcceptanceError("image identity changed before cleanup")
    removed = _run_command([docker, "image", "rm", "-f", tag], cwd=root, timeout=120)
    if removed.returncode != 0:
        raise AcceptanceError("owned image cleanup failed")


def _cleanup_owned_directory(owned: Path, cache: Path, run_id: str) -> None:
    try:
        metadata = owned.lstat()
    except FileNotFoundError:
        return
    if owned.parent != cache or not owned.name.startswith("audit-external-"):
        raise AcceptanceError("owned directory identity mismatch")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise AcceptanceError("owned path is not a real directory")
    record = owned / "runner.json"
    if record.is_symlink() or not record.is_file():
        raise AcceptanceError("runner ownership record unavailable")
    try:
        value = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceError("runner ownership record invalid") from None
    if not isinstance(value, dict) or value.get("run_id") != run_id:
        raise AcceptanceError("runner ownership record identity mismatch")
    try:
        shutil.rmtree(owned)
    except OSError:
        raise AcceptanceError("owned directory cleanup failed") from None


def _commit_identity(result: _CommandResult) -> str:
    commit = result.stdout.strip()
    if result.returncode != 0 or len(commit) not in {40, 64} or _HEX_ID.fullmatch(commit) is None:
        raise AcceptanceError("Git source identity unavailable")
    return commit


def run_acceptance(root: Path | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[1] if root is None else root
    run_id = str(uuid.uuid4())
    cache = repository_root / ".pytest_cache"
    owned: Path | None = None
    resource_record: Path | None = None
    build_started = False
    harness_started = False
    harness_returncode: int | None = None
    harness_stdout = ""
    source_digest = ""
    tag = f"easysynq-audit-external:{run_id}"
    image_id: str | None = None
    failure = False
    cleanup_failure = False
    failure_stage = "setup"
    runtime_test_count = 0
    failure_summary: tuple[str, ...] = ()
    tools: _Tools | None = None
    try:
        tools = _required_tools()
        if os.environ.get("UV_PYTHON_DOWNLOADS") != "never":
            raise AcceptanceError("managed Python downloads must be disabled")
        owned = _allocate_owned_directory(repository_root, run_id)
        resource_record = owned / "resources.json"
        failure_stage = "source_identity"
        commit = _commit_identity(
            _run_command([tools.git, "rev-parse", "HEAD"], cwd=repository_root, timeout=60)
        )
        failure_stage = "input_manifest"
        build_manifest = _build_manifest(repository_root)
        proof_manifest = _proof_manifest(repository_root)
        source_digest = build_manifest.digest
        _write_json_atomic(
            owned / "runner.json",
            {
                "run_id": run_id,
                "commit": commit,
                "build_input_sha256": build_manifest.digest,
                "proof_input_sha256": proof_manifest.digest,
            },
        )
        print(f"audit_external_run={run_id}")
        print(f"commit={commit}")
        print(f"build_input_sha256={build_manifest.digest}")
        print(f"proof_input_sha256={proof_manifest.digest}")

        failure_stage = "image_build"
        build_argv = [
            tools.docker,
            "build",
            "--label",
            f"{_RUN_LABEL}={run_id}",
            "--label",
            f"{_SOURCE_LABEL}={build_manifest.digest}",
            "-f",
            "apps/api/Dockerfile",
            "-t",
            tag,
            ".",
        ]
        build_started = True
        build = _run_command(build_argv, cwd=repository_root, timeout=_BUILD_TIMEOUT_SECONDS)
        if build.returncode != 0:
            raise AcceptanceError("image build failed")
        failure_stage = "image_identity"
        image_id = _inspect_image(
            _run_command(
                [tools.docker, "image", "inspect", tag],
                cwd=repository_root,
                timeout=60,
            ),
            run_id=run_id,
            source_digest=build_manifest.digest,
            tag=tag,
        )
        _write_json_atomic(
            owned / "runner.json",
            {
                "run_id": run_id,
                "commit": commit,
                "build_input_sha256": build_manifest.digest,
                "proof_input_sha256": proof_manifest.digest,
                "image_id": image_id,
                "tag": tag,
            },
        )
        print(f"image_id={image_id}")

        failure_stage = "runtime_harness"
        child_environment = dict(os.environ)
        child_environment.update(
            {
                "EASYSYNQ_TEST_API_IMAGE": image_id,
                "EASYSYNQ_ACCEPTANCE_RUN_ID": run_id,
                "EASYSYNQ_ACCEPTANCE_RESOURCE_RECORD": str(resource_record),
            }
        )
        harness_argv = [
            tools.uv,
            "run",
            "--project",
            str(repository_root / "apps/api"),
            "pytest",
            "tests/integration/audit_external_runtime_acceptance.py",
            "tests/integration/audit_raw_runtime_acceptance.py",
            "tests/integration/audit_isolated_raw_runtime_acceptance.py",
            "tests/integration/audit_version_page_runtime_acceptance.py",
            "tests/integration/audit_version_page_transport_runtime_acceptance.py",
            "tests/integration/audit_history_collection_runtime_acceptance.py"
            "::test_history_collection_runtime_preserves_required_witnesses_and_resource_boundaries",
            "tests/integration/audit_history_reconciliation_runtime_acceptance.py"
            "::test_history_reconciliation_runtime_preserves_global_closure_and_owned_limits",
            "-q",
            "--junitxml",
            str(owned / "runtime.xml"),
        ]
        harness_started = True
        harness = _run_command(
            harness_argv,
            cwd=repository_root / "apps/api",
            timeout=_HARNESS_TIMEOUT_SECONDS,
            environ=child_environment,
        )
        harness_returncode = harness.returncode
        harness_stdout = harness.stdout
        failure_stage = "input_recheck"
        build_after = _build_manifest(repository_root)
        proof_after = _proof_manifest(repository_root)
        if build_after != build_manifest or proof_after != proof_manifest:
            raise AcceptanceError("acceptance inputs changed during execution")
        if harness.returncode != 0:
            failure_stage = "runtime_harness"
            raise AcceptanceError("runtime acceptance failed")
        failure_stage = "junit_validation"
        runtime_test_count = _validate_junit(owned / "runtime.xml")
    except (AcceptanceError, _SignalReceived, KeyboardInterrupt):
        failure = True
    except Exception:  # noqa: BLE001 - redact unexpected runner/provider detail
        failure = True
    finally:
        if (
            failure
            and owned is not None
            and failure_stage in {"runtime_harness", "junit_validation"}
        ):
            failure_summary = _runtime_failure_summary(
                owned / "runtime.xml", harness_returncode, harness_stdout
            )
        if tools is not None:
            if resource_record is not None:
                try:
                    _cleanup_containers(
                        tools.docker,
                        repository_root,
                        resource_record,
                        run_id,
                        harness_started=harness_started,
                    )
                except AcceptanceError:
                    cleanup_failure = True
            if source_digest:
                try:
                    _cleanup_image(
                        tools.docker,
                        repository_root,
                        tag=tag,
                        run_id=run_id,
                        source_digest=source_digest,
                        expected_image_id=image_id,
                        build_started=build_started,
                    )
                except AcceptanceError:
                    cleanup_failure = True
        if owned is not None:
            try:
                _cleanup_owned_directory(owned, cache, run_id)
            except AcceptanceError:
                cleanup_failure = True

    if failure or cleanup_failure:
        for line in failure_summary:
            print(line)
        if failure:
            print(f"failure_stage={failure_stage}")
        if cleanup_failure:
            print("cleanup_status=failed")
        print("runtime_acceptance=failed")
        return 1
    print(f"runtime_tests={runtime_test_count}")
    print(f"mandatory_tests={len(_MANDATORY_TESTS)}")
    print("runtime_acceptance=passed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    supplied = list(sys.argv[1:] if argv is None else argv)
    if supplied:
        print("runtime_acceptance=failed")
        return 2
    previous = {
        signum: signal.signal(signum, _signal_handler) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        return run_acceptance()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
