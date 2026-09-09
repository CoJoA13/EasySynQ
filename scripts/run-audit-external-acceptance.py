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
_RUN_LABEL = "com.easysynq.audit-external.run"
_SOURCE_LABEL = "com.easysynq.audit-external.source"
_SESSION_LABEL = "org.testcontainers.session-id"
_MANDATORY_TESTS = frozenset(
    {
        "test_external_cli_runtime_is_public_only_and_read_only",
        "test_external_cli_runtime_preserves_enrolled_obligation_after_db_selection_attack",
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
    source_digest = ""
    tag = f"easysynq-audit-external:{run_id}"
    image_id: str | None = None
    failure = False
    cleanup_failure = False
    failure_stage = "setup"
    runtime_test_count = 0
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
