"""Executable/static guards for the exact-version rollback operator procedure."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import textwrap
from collections.abc import Mapping

ROOT = pathlib.Path(__file__).resolve().parents[4]
RUNBOOK = ROOT / "docs" / "runbooks" / "upload-identity-rollback.md"


def _text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _bash_block(marker: str) -> str:
    match = re.search(
        rf"```bash\n# {re.escape(marker)}\n(?P<body>.*?)\n```",
        _text(),
        flags=re.DOTALL,
    )
    assert match is not None, f"missing executable runbook block {marker!r}"
    return match.group("body")


def _write_executable(path: pathlib.Path, source: str) -> None:
    lines = source.strip("\n").splitlines()
    indent = len(lines[0]) - len(lines[0].lstrip())
    prefix = " " * indent
    normalized = "\n".join(line[indent:] if line.startswith(prefix) else line for line in lines)
    path.write_text(normalized + "\n", encoding="utf-8")
    path.chmod(0o700)


def _run_library(
    tmp_path: pathlib.Path,
    body: str,
    *,
    library: str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    script = f"set -euo pipefail\n{library or _bash_block('rollback-helper-library')}\n{body}"
    merged_env = dict(env or {})
    merged_env.setdefault("PATH", "/usr/bin:/bin")
    return subprocess.run(  # noqa: S603 - executes the reviewed runbook helper
        [bash, "-c", script],
        cwd=tmp_path,
        env=merged_env,
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
    )


def _container_id(char: str) -> str:
    return char * 12


def _image_id(char: str) -> str:
    return f"sha256:{char * 64}"


def _inject_post_fstat_quarantine_replacement(library: str, label: str) -> str:
    marker = "    retained_stat = os.fstat(retained_fd)\n"
    assert marker in library
    injection = (
        marker
        + "    with open('recorded-retained-identity', 'w', encoding='ascii') as record:\n"
        + "        record.write(\n"
        + "            f'{retained_stat.st_dev} {retained_stat.st_ino} '\n"
        + "            f'{retained_stat.st_uid} {retained_stat.st_gid} '\n"
        + "            f'{stat.S_IMODE(retained_stat.st_mode):04o}\\n'\n"
        + "        )\n"
        + f'    if label == "{label}":\n'
        + f'        saved_name = "helper-owned-{label}"\n'
        + "        os.rename(\n"
        + "            retained_name, saved_name,\n"
        + "            src_dir_fd=quarantine_fd, dst_dir_fd=quarantine_fd,\n"
        + "        )\n"
        + "        os.rename(\n"
        + "            'cleanup-racer', retained_name,\n"
        + "            src_dir_fd=dir_fd, dst_dir_fd=quarantine_fd,\n"
        + "        )\n"
    )
    return library.replace(marker, injection, 1)


def _json_records(output: str, prefix: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix(prefix))
        for line in output.splitlines()
        if line.startswith(prefix)
    ]


def _recorded_retained_identity(tmp_path: pathlib.Path) -> dict[str, int | str]:
    device, inode, uid, gid, mode = (
        (tmp_path / "recorded-retained-identity").read_text(encoding="ascii").split()
    )
    return {
        "device": int(device),
        "inode": int(inode),
        "uid": int(uid),
        "gid": int(gid),
        "mode": mode,
    }


def _assert_inode_report_and_inventory_find_retained_entry(
    tmp_path: pathlib.Path,
    env_file: pathlib.Path,
    library: str,
    result: subprocess.CompletedProcess[str],
) -> None:
    recorded = _recorded_retained_identity(tmp_path)
    reports = _json_records(result.stderr, "rollback-retained-entry: ")
    assert len(reports) == 1
    report = reports[0]
    assert report["identity"] == {
        "device": recorded["device"],
        "inode": recorded["inode"],
    }
    assert report["metadata"] == {
        "uid": recorded["uid"],
        "gid": recorded["gid"],
        "mode": recorded["mode"],
    }
    assert report["identity_authoritative"] is True
    assert report["path_authority"] == "last-known-only"

    quarantine = tmp_path / ".env.rollback-quarantine"
    quarantine_stat = quarantine.stat()
    assert report["validated_quarantine"] == {
        "last_known_path": str(quarantine),
        "device": quarantine_stat.st_dev,
        "inode": quarantine_stat.st_ino,
        "uid": quarantine_stat.st_uid,
        "gid": quarantine_stat.st_gid,
        "mode": "0700",
    }
    last_known_path = pathlib.Path(str(report["last_known_path"]))
    last_known_stat = last_known_path.stat()
    assert (last_known_stat.st_dev, last_known_stat.st_ino) != (
        recorded["device"],
        recorded["inode"],
    )
    current_matching_paths = report["current_matching_paths"]
    assert isinstance(current_matching_paths, list)
    matching_paths = [pathlib.Path(str(path)) for path in current_matching_paths]
    assert any(
        (path.stat().st_dev, path.stat().st_ino) == (recorded["device"], recorded["inode"])
        for path in matching_paths
    )

    inventory = _run_library(
        tmp_path,
        f"ESQ_MODE=repository\nESQ_ENV_FILE='{env_file}'\nesq_inventory_quarantine",
        library=library,
    )
    assert inventory.returncode == 0, inventory.stderr
    inventory_output = inventory.stdout + inventory.stderr
    assert "SECRET=" not in inventory_output
    assert "RACER=" not in inventory_output
    assert "CONCURRENT=" not in inventory_output
    directory_records = _json_records(inventory.stdout, "rollback-quarantine-directory: ")
    assert directory_records == [report["validated_quarantine"]]
    entries = _json_records(inventory.stdout, "rollback-quarantine-entry: ")
    actual_entries = list(quarantine.iterdir())
    assert len(entries) == len(actual_entries) == 2
    assert {(entry.stat().st_dev, entry.stat().st_ino) for entry in actual_entries} == {
        (int(record["identity"]["device"]), int(record["identity"]["inode"])) for record in entries
    }
    authoritative = [
        entry
        for entry in entries
        if entry["identity"] == report["identity"] and entry["metadata"] == report["metadata"]
    ]
    assert len(authoritative) == 1
    observed_path = pathlib.Path(str(authoritative[0]["observed_path"]))
    observed_stat = observed_path.stat()
    assert (observed_stat.st_dev, observed_stat.st_ino) == (
        recorded["device"],
        recorded["inode"],
    )
    assert authoritative[0]["path_authority"] == "observation-only"


def _install_compose_docker_fakes(
    tmp_path: pathlib.Path,
    *,
    running: list[str],
    all_containers: list[str],
    refs: Mapping[str, str],
    ids: Mapping[str, str],
) -> tuple[pathlib.Path, pathlib.Path]:
    state = tmp_path / "state"
    state.mkdir()
    (state / "running").write_text("\n".join(running) + "\n", encoding="utf-8")
    (state / "all").write_text("\n".join(all_containers) + "\n", encoding="utf-8")
    for container in all_containers:
        (state / f"ref-{container}").write_text(refs[container] + "\n", encoding="utf-8")
        (state / f"id-{container}").write_text(ids[container] + "\n", encoding="utf-8")
    compose = tmp_path / "fake-compose"
    docker = tmp_path / "fake-docker"
    _write_executable(
        compose,
        """
        #!/bin/bash
        set -euo pipefail
        case "$*" in
          'ps -q --status running api') cat "$ESQ_FAKE_STATE/running" ;;
          'ps -aq api') cat "$ESQ_FAKE_STATE/all" ;;
          *) exit 90 ;;
        esac
        """,
    )
    _write_executable(
        docker,
        """
        #!/bin/bash
        set -euo pipefail
        test "$1" = container
        test "$2" = inspect
        test "$3" = --format
        format="$4"
        container="$5"
        case "$format" in
          '{{.Config.Image}}') cat "$ESQ_FAKE_STATE/ref-$container" ;;
          '{{.Image}}') cat "$ESQ_FAKE_STATE/id-$container" ;;
          *) exit 91 ;;
        esac
        """,
    )
    return compose, docker


def test_env_setter_is_mode_specific_privileged_and_atomic() -> None:
    text = _text()
    library = _bash_block("rollback-helper-library")

    assert "ESQ_MODE=appliance" in text
    assert "ESQ_EXPECTED_API_REPLICAS=1" in text
    assert "m) ESQ_PROFILE_FILE='infra/compose/compose.m.yml'; ESQ_EXPECTED_API_REPLICAS=2" in text
    assert "esq_set_env_appliance" in library
    assert "esq_set_env_repository" in library
    assert "sudo bash -c" in library
    assert "/opt/easysynq/.env" in library
    assert "root:easysynq" in library
    assert "esq_atomic_set_env_file /opt/easysynq/.env 0 '$easysynq_gid' 640" in library
    assert 'esq_atomic_set_env_file "$ESQ_ENV_FILE" "$owner" "$group" "$mode"' in library
    assert "EASYSYNQ_COMPATIBILITY_READ_ONLY" in library
    assert 'value not in {"0", "1"}' in library
    assert "O_NOFOLLOW" in library
    assert "fcntl.flock" in library
    assert "renameat2" in library
    assert "os.fsync" in library


def test_atomic_setter_shell_harness_preserves_owner_mode_and_content(
    tmp_path: pathlib.Path,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    library = _bash_block("rollback-helper-library")
    function_match = re.search(
        r"(?ms)^esq_atomic_set_env_file\(\) \{.*?^\}",
        library,
    )
    assert function_match is not None
    env_file = tmp_path / ".env"
    script = textwrap.dedent(
        f"""
        set -euo pipefail
        {function_match.group(0)}
        env_file="$1"
        appliance_uid="$(id -u)"
        appliance_gid="$(id -g)"
        printf '%s\n' 'SECRET_SENTINEL=not-printed' 'EASYSYNQ_PROFILE=s' >"$env_file"
        chmod 0640 "$env_file"
        esq_atomic_set_env_file "$env_file" "$appliance_uid" "$appliance_gid" 640 \
          EASYSYNQ_COMPATIBILITY_READ_ONLY 1
        test "$(stat -c '%u:%g:%a' "$env_file")" = \
          "$appliance_uid:$appliance_gid:640"
        test "$(command stat -c %a "$env_file")" = 640
        test "$(grep -c '^EASYSYNQ_COMPATIBILITY_READ_ONLY=1$' "$env_file")" = 1
        test "$(grep -c '^SECRET_SENTINEL=not-printed$' "$env_file")" = 1
        """
    )
    result = subprocess.run(  # noqa: S603 - executes the reviewed runbook helper
        [bash, "-c", script, "bash", str(env_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "SECRET_SENTINEL" not in result.stdout
    assert "SECRET_SENTINEL" not in result.stderr
    quarantine = tmp_path / ".env.rollback-quarantine"
    retained = list(quarantine.iterdir())
    assert len(retained) == 1
    assert retained[0].read_text(encoding="utf-8") == (
        "SECRET_SENTINEL=not-printed\nEASYSYNQ_PROFILE=s\n"
    )
    assert retained[0].stat().st_mode & 0o777 == 0o640
    assert str(retained[0]) in result.stderr


def test_each_api_cutover_selects_retags_recreates_and_verifies_exact_image() -> None:
    text = _text()
    rollback = text.index("# rollback-artifact-selection")
    recovery = text.index("# recovery-artifact-selection")
    rollback_slice = text[rollback:recovery]
    recovery_slice = text[recovery:]

    for section in (rollback_slice, recovery_slice):
        assert "esq_select_api_artifact" in section
        assert "SELECTED_API_IMAGE_ID" in section
        assert "up -d --no-deps --force-recreate --no-build api" in section
        assert "esq_require_running_api_image" in section
        assert section.index("esq_select_api_artifact") < section.index("force-recreate")
        assert section.index("force-recreate") < section.index("esq_require_running_api_image")

    library = _bash_block("rollback-helper-library")
    assert "hashlib.sha256" in library
    assert '"load", "--input"' in library
    assert "git rev-parse --verify" in library
    assert 'worktree add --detach "$build_source" "$resolved_commit"' in library
    assert "docker build" in library
    assert "container inspect" in library
    assert "{{.Image}}" in library
    assert 'image tag "$SELECTED_API_IMAGE_ID" "$ESQ_API_SERVICE_IMAGE"' in library
    assert '[ "$ESQ_RECOVERY_API_IMAGE_ID" != "$ESQ_ROLLBACK_API_IMAGE_ID" ]' in text


def test_every_http_probe_uses_mode_specific_curl_array() -> None:
    text = _text()
    library = _bash_block("rollback-helper-library")

    assert "ESQ_CURL=(curl --disable --silent --show-error --noproxy '*')" in library
    assert "easysynq-status --ca" in library
    assert "openssl x509" in library
    assert '--cacert "$ESQ_CADDY_CA"' in library
    assert '--resolve "${ESQ_HTTPS_HOST}:443:127.0.0.1"' in library
    assert "curl -k" not in text
    assert "curl -fsk" not in text

    probe_lines = [line for line in text.splitlines() if "curl" in line.lower()]
    executable_probes = [line for line in probe_lines if "ESQ_BASE_URL" in line]
    assert executable_probes
    assert all('"${ESQ_CURL[@]}"' in line for line in executable_probes)


def test_s_and_m_replica_sets_are_verified_as_immutable_sets(tmp_path: pathlib.Path) -> None:
    cases = [
        ("s-ok", 1, [_container_id("a")], [_container_id("a")], True),
        (
            "m-ok",
            2,
            [_container_id("a"), _container_id("b")],
            [_container_id("a"), _container_id("b")],
            True,
        ),
        ("missing", 2, [_container_id("a")], [_container_id("a")], False),
        (
            "stopped-extra",
            2,
            [_container_id("a"), _container_id("b")],
            [_container_id("a"), _container_id("b"), _container_id("c")],
            False,
        ),
        (
            "running-extra",
            2,
            [_container_id("a"), _container_id("b"), _container_id("c")],
            [_container_id("a"), _container_id("b"), _container_id("c")],
            False,
        ),
    ]
    for name, replicas, running, all_containers, should_pass in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        refs = {container: "easysynq-api:latest" for container in all_containers}
        ids = {container: _image_id("1") for container in all_containers}
        compose, docker = _install_compose_docker_fakes(
            case_dir,
            running=running,
            all_containers=all_containers,
            refs=refs,
            ids=ids,
        )
        body = textwrap.dedent(
            f"""
            ESQ_COMPOSE=({compose})
            ESQ_DOCKER=({docker})
            ESQ_EXPECTED_API_REPLICAS={replicas}
            esq_resolve_api_service_image
            esq_require_running_api_image '{_image_id("1")}'
            """
        )
        result = _run_library(
            case_dir,
            body,
            env={"ESQ_FAKE_STATE": str(case_dir / "state")},
        )
        assert (result.returncode == 0) is should_pass, (
            name,
            result.stdout,
            result.stderr,
        )


def test_replica_verifier_rejects_mixed_refs_and_ids(tmp_path: pathlib.Path) -> None:
    containers = [_container_id("a"), _container_id("b")]
    scenarios = {
        "mixed-ref": (
            {containers[0]: "easysynq-api:latest", containers[1]: "foreign-api:latest"},
            {container: _image_id("1") for container in containers},
            "esq_resolve_api_service_image",
        ),
        "mixed-allowed-ref": (
            {containers[0]: "easysynq-api", containers[1]: "easysynq-api:latest"},
            {container: _image_id("1") for container in containers},
            "esq_resolve_api_service_image",
        ),
        "mixed-current-id": (
            {container: "easysynq-api:latest" for container in containers},
            {containers[0]: _image_id("1"), containers[1]: _image_id("2")},
            "esq_resolve_api_service_image",
        ),
        "selected-id-mismatch": (
            {container: "easysynq-api:latest" for container in containers},
            {containers[0]: _image_id("1"), containers[1]: _image_id("2")},
            f"esq_require_running_api_image '{_image_id('1')}'",
        ),
    }
    for name, (refs, ids, invocation) in scenarios.items():
        case_dir = tmp_path / name
        case_dir.mkdir()
        compose, docker = _install_compose_docker_fakes(
            case_dir,
            running=containers,
            all_containers=containers,
            refs=refs,
            ids=ids,
        )
        result = _run_library(
            case_dir,
            f"ESQ_COMPOSE=({compose})\nESQ_DOCKER=({docker})\n"
            f"ESQ_EXPECTED_API_REPLICAS=2\n{invocation}\n",
            env={"ESQ_FAKE_STATE": str(case_dir / "state")},
        )
        assert result.returncode != 0, name


def test_env_exchange_restores_a_final_boundary_replacement(tmp_path: pathlib.Path) -> None:
    library = _bash_block("rollback-helper-library")
    marker = "# final-exchange-boundary"
    assert marker in library
    injected = library.replace(
        marker,
        "os.rename('racer', name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)\n    " + marker,
        1,
    )
    env_file = tmp_path / ".env"
    racer = tmp_path / "racer"
    env_file.write_text("SECRET_SENTINEL=old\n", encoding="utf-8")
    racer.write_text("CONCURRENT_SENTINEL=preserve\n", encoding="utf-8")
    env_file.chmod(0o640)
    racer.chmod(0o640)
    result = _run_library(
        tmp_path,
        f"esq_atomic_set_env_file '{env_file}' {env_file.stat().st_uid} "
        f"{env_file.stat().st_gid} 640 EASYSYNQ_COMPATIBILITY_READ_ONLY 1",
        library=injected,
    )
    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "CONCURRENT_SENTINEL=preserve\n"
    retained = list((tmp_path / ".env.rollback-quarantine").iterdir())
    assert len(retained) == 1
    assert "EASYSYNQ_COMPATIBILITY_READ_ONLY=1\n" in retained[0].read_text(encoding="utf-8")
    assert retained[0].stat().st_mode & 0o777 == 0o640
    assert str(retained[0]) in result.stderr


def test_curl_array_ignores_hostile_config_and_proxy(tmp_path: pathlib.Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_log = tmp_path / "curl-args"
    _write_executable(
        fake_bin / "curl",
        """
        #!/bin/bash
        set -euo pipefail
        printf '%s\n' "$@" >"$ESQ_CURL_LOG"
        test "$1" = --disable
        shift
        found_noproxy=0
        while [ "$#" -gt 0 ]; do
          if [ "$1" = --noproxy ] && [ "${2:-}" = '*' ]; then found_noproxy=1; fi
          shift
        done
        test "$found_noproxy" = 1
        """,
    )
    hostile_home = tmp_path / "home"
    hostile_home.mkdir()
    (hostile_home / ".curlrc").write_text(
        "insecure\nlocation-trusted\nproxy http://attacker.invalid:8080\n",
        encoding="utf-8",
    )
    result = _run_library(
        tmp_path,
        'ESQ_MODE=repository\nESQ_CURL_LOG="$PWD/curl-args"\nexport ESQ_CURL_LOG\n'
        "ESQ_BASE_URL=https://app.example\nesq_configure_curl\n"
        '"${ESQ_CURL[@]}" "$ESQ_BASE_URL/healthz"',
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(hostile_home),
            "HTTPS_PROXY": "http://attacker.invalid:8080",
            "ALL_PROXY": "http://attacker.invalid:8080",
        },
    )
    assert result.returncode == 0, result.stderr
    args = curl_log.read_text(encoding="utf-8").splitlines()
    assert args[0] == "--disable"
    assert args[args.index("--noproxy") + 1] == "*"


def test_appliance_curl_executes_with_exported_ca_and_loopback_pin(
    tmp_path: pathlib.Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_log = tmp_path / "curl-args"
    openssl_log = tmp_path / "openssl-args"
    _write_executable(
        fake_bin / "sudo",
        """
        #!/bin/bash
        set -euo pipefail
        test "$1" = easysynq-status
        test "$2" = --ca
        printf '%s\n' '-----BEGIN CERTIFICATE-----' 'FAKE' '-----END CERTIFICATE-----'
        """,
    )
    _write_executable(
        fake_bin / "openssl",
        """
        #!/bin/bash
        set -euo pipefail
        printf '%s\n' "$@" >"$ESQ_OPENSSL_LOG"
        test "$1" = x509
        test "$2" = -in
        grep -q 'BEGIN CERTIFICATE' "$3"
        test "$4" = -noout
        test "$5" = -checkend
        test "$6" = 0
        """,
    )
    _write_executable(
        fake_bin / "curl",
        """
        #!/bin/bash
        set -euo pipefail
        printf '%s\n' "$@" >"$ESQ_CURL_LOG"
        """,
    )
    result = _run_library(
        tmp_path,
        "ESQ_MODE=appliance\nESQ_BASE_URL=https://appliance.example\n"
        'ESQ_CURL_LOG="$PWD/curl-args"\nESQ_OPENSSL_LOG="$PWD/openssl-args"\n'
        "export ESQ_CURL_LOG ESQ_OPENSSL_LOG\n"
        'esq_configure_curl\n"${ESQ_CURL[@]}" "$ESQ_BASE_URL/healthz"',
        env={"PATH": f"{fake_bin}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    args = curl_log.read_text(encoding="utf-8").splitlines()
    assert args[0] == "--disable"
    assert args[args.index("--noproxy") + 1] == "*"
    assert args[args.index("--resolve") + 1] == "appliance.example:443:127.0.0.1"
    assert "--cacert" in args
    assert openssl_log.read_text(encoding="utf-8").splitlines()[-2:] == [
        "-checkend",
        "0",
    ]


def test_appliance_artifact_digest_load_ref_and_tag_oracles(tmp_path: pathlib.Path) -> None:
    fake_docker = tmp_path / "fake-docker"
    log = tmp_path / "docker-log"
    tag_state = tmp_path / "tag-state"
    _write_executable(
        fake_docker,
        """
        #!/bin/bash
        set -euo pipefail
        printf '%s\n' "$*" >>"$ESQ_DOCKER_LOG"
        case "$1 $2" in
          'load --input')
            case "$ESQ_LOAD_MODE" in
              good) printf '%s\n' 'Loaded image: approved-api:round2' ;;
              fail) exit 44 ;;
              bad-ref) printf '%s\n' 'Loaded image: invalid ref' ;;
              *) exit 45 ;;
            esac
            ;;
          'image inspect')
            reference="$5"
            if [ "$reference" = approved-api:round2 ]; then
              printf '%s\n' "$ESQ_SELECTED_ID"
            else
              if [ "$ESQ_LOAD_MODE" = tag-mismatch ]; then
                printf '%s\n' "$ESQ_MISMATCH_ID"
              else
                cat "$ESQ_TAG_STATE"
              fi
            fi
            ;;
          'image tag')
            [ "$ESQ_LOAD_MODE" != tag-fail ] || exit 47
            printf '%s\n' "$3" >"$ESQ_TAG_STATE"
            ;;
          *) exit 46 ;;
        esac
        """,
    )
    selected_id = _image_id("3")
    for name, digest_ok, load_mode, should_pass in [
        ("success", True, "good", True),
        ("digest", False, "good", False),
        ("load", True, "fail", False),
        ("ref", True, "bad-ref", False),
        ("tag-fail", True, "tag-fail", False),
        ("tag-mismatch", True, "tag-mismatch", False),
    ]:
        archive = tmp_path / f"{name}.tar"
        sidecar = tmp_path / f"{name}.sha256"
        archive.write_bytes(f"archive-{name}".encode())
        archive.chmod(0o644)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sidecar.write_text(
            f"{digest if digest_ok else '0' * 64}  {archive.name}\n",
            encoding="utf-8",
        )
        sidecar.chmod(0o644)
        log.write_text("", encoding="utf-8")
        tag_state.write_text("", encoding="utf-8")
        result = _run_library(
            tmp_path,
            f"ESQ_MODE=appliance\nESQ_DOCKER=({fake_docker})\n"
            "ESQ_API_SERVICE_IMAGE=easysynq-api:latest\n"
            "esq_select_api_artifact rollback\n"
            f'test "$SELECTED_API_IMAGE_ID" = "{selected_id}"',
            env={
                "ESQ_DOCKER_LOG": str(log),
                "ESQ_TAG_STATE": str(tag_state),
                "ESQ_LOAD_MODE": load_mode,
                "ESQ_SELECTED_ID": selected_id,
                "ESQ_MISMATCH_ID": _image_id("6"),
            },
            stdin=f"{archive}\n{sidecar}\n",
        )
        assert (result.returncode == 0) is should_pass, name
        calls = log.read_text(encoding="utf-8")
        if not digest_ok:
            assert "load --input" not in calls
        if should_pass:
            assert tag_state.read_text(encoding="utf-8").strip() == selected_id


def test_appliance_archive_load_uses_the_hashed_inode_after_path_swap(
    tmp_path: pathlib.Path,
) -> None:
    archive = tmp_path / "approved.tar"
    replacement = tmp_path / "replacement.tar"
    sidecar = tmp_path / "approved.sha256"
    archive.write_bytes(b"APPROVED-ARCHIVE")
    replacement.write_bytes(b"UNAPPROVED-REPLACEMENT")
    archive.chmod(0o644)
    replacement.chmod(0o644)
    approved_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar.write_text(f"{approved_digest}  {archive.name}\n", encoding="utf-8")
    sidecar.chmod(0o644)
    fake_docker = tmp_path / "fake-docker"
    loaded_digest = tmp_path / "loaded-digest"
    tag_state = tmp_path / "tag-state"
    selected_id = _image_id("7")
    _write_executable(
        fake_docker,
        """
        #!/bin/bash
        set -euo pipefail
        case "$1 $2" in
          'load --input')
            mv "$ESQ_REPLACEMENT_ARCHIVE" "$ESQ_ORIGINAL_ARCHIVE"
            sha256sum "$3" | awk '{print $1}' >"$ESQ_LOADED_DIGEST"
            printf '%s\n' 'Loaded image: approved-api:stable'
            ;;
          'image inspect')
            if [ "$5" = approved-api:stable ]; then
              printf '%s\n' "$ESQ_SELECTED_ID"
            else
              cat "$ESQ_TAG_STATE"
            fi
            ;;
          'image tag') printf '%s\n' "$3" >"$ESQ_TAG_STATE" ;;
          *) exit 70 ;;
        esac
        """,
    )
    result = _run_library(
        tmp_path,
        f"ESQ_MODE=appliance\nESQ_DOCKER=({fake_docker})\n"
        "ESQ_API_SERVICE_IMAGE=easysynq-api:latest\n"
        "esq_select_api_artifact rollback",
        env={
            "ESQ_REPLACEMENT_ARCHIVE": str(replacement),
            "ESQ_ORIGINAL_ARCHIVE": str(archive),
            "ESQ_LOADED_DIGEST": str(loaded_digest),
            "ESQ_SELECTED_ID": selected_id,
            "ESQ_TAG_STATE": str(tag_state),
        },
        stdin=f"{archive}\n{sidecar}\n",
    )
    assert result.returncode == 0, result.stderr
    assert loaded_digest.read_text(encoding="utf-8").strip() == approved_digest


def test_appliance_loads_a_sealed_snapshot_after_same_inode_source_mutation(
    tmp_path: pathlib.Path,
) -> None:
    archive = tmp_path / "approved.tar"
    sidecar = tmp_path / "approved.sha256"
    archive.write_bytes(b"APPROVED-SEALED-ARCHIVE")
    archive.chmod(0o644)
    approved_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar.write_text(f"{approved_digest}  {archive.name}\n", encoding="utf-8")
    sidecar.chmod(0o644)
    fake_docker = tmp_path / "fake-docker"
    loaded_digest = tmp_path / "loaded-digest"
    mutation_rejected = tmp_path / "mutation-rejected"
    tag_state = tmp_path / "tag-state"
    selected_id = _image_id("9")
    _write_executable(
        fake_docker,
        """
        #!/bin/bash
        set -euo pipefail
        case "$1 $2" in
          'load --input')
            /usr/bin/python3 - "$3" "$ESQ_MUTATION_REJECTED" <<'PY'
        import errno
        import os
        import sys

        descriptor = os.open(sys.argv[1], os.O_RDWR)
        try:
            try:
                os.write(descriptor, b"UNAPPROVED-MEMFD-MUTATION")
            except OSError as error:
                if error.errno != errno.EPERM:
                    raise
                with open(sys.argv[2], "w", encoding="ascii") as marker:
                    marker.write("sealed\\n")
            else:
                raise SystemExit("Docker input remained writable")
        finally:
            os.close(descriptor)
        PY
            sha256sum "$3" | awk '{print $1}' >"$ESQ_LOADED_DIGEST"
            printf '%s\n' 'Loaded image: approved-api:sealed'
            ;;
          'image inspect')
            if [ "$5" = approved-api:sealed ]; then
              printf '%s\n' "$ESQ_SELECTED_ID"
            else
              cat "$ESQ_TAG_STATE"
            fi
            ;;
          'image tag') printf '%s\n' "$3" >"$ESQ_TAG_STATE" ;;
          *) exit 73 ;;
        esac
        """,
    )
    library = _bash_block("rollback-helper-library")
    marker = "# artifact-digest-complete"
    assert marker in library
    injected = library.replace(
        marker,
        "racer_fd = os.open(archive_path, os.O_WRONLY | os.O_TRUNC)\n"
        "    os.write(racer_fd, b'IN-PLACE-SOURCE-MUTATION')\n"
        "    os.close(racer_fd)\n    " + marker,
        1,
    )
    result = _run_library(
        tmp_path,
        f"ESQ_MODE=appliance\nESQ_DOCKER=({fake_docker})\n"
        "ESQ_API_SERVICE_IMAGE=easysynq-api:latest\n"
        "esq_select_api_artifact rollback",
        library=injected,
        env={
            "ESQ_LOADED_DIGEST": str(loaded_digest),
            "ESQ_MUTATION_REJECTED": str(mutation_rejected),
            "ESQ_SELECTED_ID": selected_id,
            "ESQ_TAG_STATE": str(tag_state),
        },
        stdin=f"{archive}\n{sidecar}\n",
    )
    assert result.returncode == 0, result.stderr
    assert mutation_rejected.read_text(encoding="utf-8") == "sealed\n"
    assert loaded_digest.read_text(encoding="utf-8").strip() == approved_digest
    assert archive.read_bytes() == b"IN-PLACE-SOURCE-MUTATION"


def test_appliance_snapshot_honors_archive_and_process_size_limits(
    tmp_path: pathlib.Path,
) -> None:
    fake_docker = tmp_path / "fake-docker"
    docker_marker = tmp_path / "docker-called"
    _write_executable(
        fake_docker,
        f"""
        #!/bin/bash
        touch '{docker_marker}'
        exit 74
        """,
    )
    library = _bash_block("rollback-helper-library")
    size_limit = "MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024"
    assert size_limit in library

    for name, body_prefix, case_library, content in [
        (
            "archive-limit",
            "",
            library.replace(size_limit, "MAX_ARCHIVE_BYTES = 32", 1),
            b"X" * 33,
        ),
        ("process-limit", "ulimit -f 1\n", library, b"Y" * 4096),
    ]:
        case_dir = tmp_path / name
        case_dir.mkdir()
        archive = case_dir / "limited.tar"
        sidecar = case_dir / "limited.sha256"
        archive.write_bytes(content)
        archive.chmod(0o644)
        digest = hashlib.sha256(content).hexdigest()
        sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        sidecar.chmod(0o644)
        result = _run_library(
            case_dir,
            body_prefix
            + f"ESQ_MODE=appliance\nESQ_DOCKER=({fake_docker})\n"
            + "ESQ_API_SERVICE_IMAGE=easysnq-api:latest\n"
            + "esq_select_api_artifact rollback",
            library=case_library,
            stdin=f"{archive}\n{sidecar}\n",
        )
        assert result.returncode != 0, name
        assert not docker_marker.exists(), name


def test_appliance_snapshot_read_error_fails_before_docker(tmp_path: pathlib.Path) -> None:
    archive = tmp_path / "read-error.tar"
    sidecar = tmp_path / "read-error.sha256"
    archive.write_bytes(b"READ-ERROR-ARCHIVE")
    archive.chmod(0o644)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    sidecar.chmod(0o644)
    docker_marker = tmp_path / "docker-called"
    fake_docker = tmp_path / "fake-docker"
    _write_executable(
        fake_docker,
        f"""
        #!/bin/bash
        touch '{docker_marker}'
        exit 75
        """,
    )
    library = _bash_block("rollback-helper-library")
    marker = "# artifact-snapshot-read-boundary"
    assert marker in library
    injected = library.replace(
        marker,
        "os.close(archive_fd)\n        " + marker,
        1,
    )
    result = _run_library(
        tmp_path,
        f"ESQ_MODE=appliance\nESQ_DOCKER=({fake_docker})\n"
        "ESQ_API_SERVICE_IMAGE=easysynq-api:latest\n"
        "esq_select_api_artifact rollback",
        library=injected,
        stdin=f"{archive}\n{sidecar}\n",
    )
    assert result.returncode != 0
    assert not docker_marker.exists()


def test_appliance_archive_and_sidecar_are_read_from_retained_fds(
    tmp_path: pathlib.Path,
) -> None:
    archive = tmp_path / "stable.tar"
    sidecar = tmp_path / "stable.sha256"
    archive_racer = tmp_path / "archive-racer"
    sidecar_racer = tmp_path / "sidecar-racer"
    archive.write_bytes(b"STABLE-ARCHIVE")
    approved_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar.write_text(f"{approved_digest}  {archive.name}\n", encoding="utf-8")
    archive_racer.write_bytes(b"RACED-ARCHIVE")
    sidecar_racer.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")
    for path in (archive, sidecar, archive_racer, sidecar_racer):
        path.chmod(0o644)
    fake_docker = tmp_path / "fake-docker"
    loaded_digest = tmp_path / "loaded-digest"
    tag_state = tmp_path / "tag-state"
    selected_id = _image_id("8")
    _write_executable(
        fake_docker,
        """
        #!/bin/bash
        set -euo pipefail
        case "$1 $2" in
          'load --input')
            sha256sum "$3" | awk '{print $1}' >"$ESQ_LOADED_DIGEST"
            printf '%s\n' 'Loaded image: approved-api:both-fds'
            ;;
          'image inspect')
            if [ "$5" = approved-api:both-fds ]; then
              printf '%s\n' "$ESQ_SELECTED_ID"
            else
              cat "$ESQ_TAG_STATE"
            fi
            ;;
          'image tag') printf '%s\n' "$3" >"$ESQ_TAG_STATE" ;;
          *) exit 71 ;;
        esac
        """,
    )
    library = _bash_block("rollback-helper-library")
    marker = "# artifact-stable-fds-opened"
    injected = library.replace(
        marker,
        "os.replace(os.environ['ESQ_ARCHIVE_RACER'], archive_path)\n"
        "    os.replace(os.environ['ESQ_SIDECAR_RACER'], sidecar_path)\n    " + marker,
        1,
    )
    result = _run_library(
        tmp_path,
        f"ESQ_MODE=appliance\nESQ_DOCKER=({fake_docker})\n"
        "ESQ_API_SERVICE_IMAGE=easysynq-api:latest\n"
        "esq_select_api_artifact rollback",
        library=injected,
        env={
            "ESQ_ARCHIVE_RACER": str(archive_racer),
            "ESQ_SIDECAR_RACER": str(sidecar_racer),
            "ESQ_LOADED_DIGEST": str(loaded_digest),
            "ESQ_SELECTED_ID": selected_id,
            "ESQ_TAG_STATE": str(tag_state),
        },
        stdin=f"{archive}\n{sidecar}\n",
    )
    assert result.returncode == 0, result.stderr
    assert loaded_digest.read_text(encoding="utf-8").strip() == approved_digest


def test_appliance_artifact_rejects_writable_or_linked_inputs(
    tmp_path: pathlib.Path,
) -> None:
    fake_docker = tmp_path / "fake-docker"
    docker_marker = tmp_path / "docker-called"
    _write_executable(
        fake_docker,
        f"""
        #!/bin/bash
        touch '{docker_marker}'
        exit 72
        """,
    )
    for name, archive_mode, link_sidecar in [
        ("writable", 0o664, False),
        ("linked", 0o644, True),
    ]:
        archive = tmp_path / f"{name}.tar"
        sidecar = tmp_path / f"{name}.sha256"
        archive.write_bytes(name.encode())
        archive.chmod(archive_mode)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        sidecar.chmod(0o644)
        if link_sidecar:
            (tmp_path / f"{name}.hardlink").hardlink_to(sidecar)
        result = _run_library(
            tmp_path,
            f"ESQ_MODE=appliance\nESQ_DOCKER=({fake_docker})\n"
            "ESQ_API_SERVICE_IMAGE=easysynq-api:latest\n"
            "esq_select_api_artifact rollback",
            stdin=f"{archive}\n{sidecar}\n",
        )
        assert result.returncode != 0, name
        assert not docker_marker.exists()


def test_cleanup_quarantines_success_boundary_replacement(tmp_path: pathlib.Path) -> None:
    library = _bash_block("rollback-helper-library")
    marker = "# success-cleanup-boundary"
    assert marker in library
    injected = library.replace(
        marker,
        "os.rename('cleanup-racer', temp_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)\n    "
        + marker,
        1,
    )
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=old\n", encoding="utf-8")
    env_file.chmod(0o640)
    (tmp_path / "cleanup-racer").write_text("REPLACEMENT-MUST-SURVIVE\n", encoding="utf-8")
    result = _run_library(
        tmp_path,
        f"esq_atomic_set_env_file '{env_file}' {env_file.stat().st_uid} "
        f"{env_file.stat().st_gid} 640 EASYSYNQ_COMPATIBILITY_READ_ONLY 1",
        library=injected,
    )
    assert result.returncode != 0
    retained = list((tmp_path / ".env.rollback-quarantine").iterdir())
    assert [path.read_text(encoding="utf-8") for path in retained] == ["REPLACEMENT-MUST-SURVIVE\n"]


def test_cleanup_quarantines_restoration_boundary_replacement(
    tmp_path: pathlib.Path,
) -> None:
    library = _bash_block("rollback-helper-library")
    final_marker = "# final-exchange-boundary"
    cleanup_marker = "# restoration-cleanup-boundary"
    assert cleanup_marker in library
    injected = library.replace(
        final_marker,
        "os.rename('boundary-racer', name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)\n    "
        + final_marker,
        1,
    ).replace(
        cleanup_marker,
        "os.rename('cleanup-racer', temp_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)\n        "
        + cleanup_marker,
        1,
    )
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=old\n", encoding="utf-8")
    env_file.chmod(0o640)
    (tmp_path / "boundary-racer").write_text("CONCURRENT-ENV\n", encoding="utf-8")
    (tmp_path / "cleanup-racer").write_text("CLEANUP-RACER-SURVIVES\n", encoding="utf-8")
    result = _run_library(
        tmp_path,
        f"esq_atomic_set_env_file '{env_file}' {env_file.stat().st_uid} "
        f"{env_file.stat().st_gid} 640 EASYSYNQ_COMPATIBILITY_READ_ONLY 1",
        library=injected,
    )
    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "CONCURRENT-ENV\n"
    retained = list((tmp_path / ".env.rollback-quarantine").iterdir())
    assert [path.read_text(encoding="utf-8") for path in retained] == ["CLEANUP-RACER-SURVIVES\n"]


def test_success_cleanup_retains_post_fstat_replacement_and_old_env(
    tmp_path: pathlib.Path,
) -> None:
    library = _inject_post_fstat_quarantine_replacement(
        _bash_block("rollback-helper-library"), "replaced-source"
    )
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=old-success\n", encoding="utf-8")
    env_file.chmod(0o640)
    cleanup_racer = tmp_path / "cleanup-racer"
    cleanup_racer.write_text("RACER=success\n", encoding="utf-8")
    cleanup_racer.chmod(0o640)
    result = _run_library(
        tmp_path,
        f"esq_atomic_set_env_file '{env_file}' {env_file.stat().st_uid} "
        f"{env_file.stat().st_gid} 640 EASYSYNQ_COMPATIBILITY_READ_ONLY 1",
        library=library,
    )
    assert result.returncode == 0, result.stderr
    retained = list((tmp_path / ".env.rollback-quarantine").iterdir())
    assert {path.read_text(encoding="utf-8") for path in retained} == {
        "SECRET=old-success\n",
        "RACER=success\n",
    }
    assert all(path.stat().st_mode & 0o777 == 0o640 for path in retained)
    _assert_inode_report_and_inventory_find_retained_entry(tmp_path, env_file, library, result)
    assert str(tmp_path / ".env.rollback-quarantine") in result.stderr
    assert "SECRET=old-success" not in result.stderr
    assert "RACER=success" not in result.stderr


def test_restoration_cleanup_retains_post_fstat_replacement_and_helper_temp(
    tmp_path: pathlib.Path,
) -> None:
    library = _inject_post_fstat_quarantine_replacement(
        _bash_block("rollback-helper-library"), "restored-update"
    )
    marker = "# final-exchange-boundary"
    library = library.replace(
        marker,
        "os.rename('boundary-racer', name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)\n    " + marker,
        1,
    )
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=old-restoration\n", encoding="utf-8")
    env_file.chmod(0o640)
    boundary_racer = tmp_path / "boundary-racer"
    boundary_racer.write_text("CONCURRENT=restored\n", encoding="utf-8")
    boundary_racer.chmod(0o640)
    cleanup_racer = tmp_path / "cleanup-racer"
    cleanup_racer.write_text("RACER=restoration\n", encoding="utf-8")
    cleanup_racer.chmod(0o640)
    result = _run_library(
        tmp_path,
        f"esq_atomic_set_env_file '{env_file}' {env_file.stat().st_uid} "
        f"{env_file.stat().st_gid} 640 EASYSYNQ_COMPATIBILITY_READ_ONLY 1",
        library=library,
    )
    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "CONCURRENT=restored\n"
    retained = list((tmp_path / ".env.rollback-quarantine").iterdir())
    contents = {path.read_text(encoding="utf-8") for path in retained}
    assert "RACER=restoration\n" in contents
    assert any(
        content == "SECRET=old-restoration\nEASYSYNQ_COMPATIBILITY_READ_ONLY=1\n"
        for content in contents
    )
    assert len(retained) == 2
    assert all(path.stat().st_mode & 0o777 == 0o640 for path in retained)
    _assert_inode_report_and_inventory_find_retained_entry(tmp_path, env_file, library, result)
    assert str(tmp_path / ".env.rollback-quarantine") in result.stderr
    assert "SECRET=old-restoration" not in result.stderr


def test_final_failure_cleanup_retains_post_fstat_replacement_and_helper_temp(
    tmp_path: pathlib.Path,
) -> None:
    library = _inject_post_fstat_quarantine_replacement(
        _bash_block("rollback-helper-library"), "failed-update"
    )
    marker = "# final-exchange-boundary"
    library = library.replace(
        marker,
        "raise RuntimeError('forced final-failure cleanup')\n    " + marker,
        1,
    )
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=old-final\n", encoding="utf-8")
    env_file.chmod(0o640)
    cleanup_racer = tmp_path / "cleanup-racer"
    cleanup_racer.write_text("RACER=final-failure\n", encoding="utf-8")
    cleanup_racer.chmod(0o640)
    result = _run_library(
        tmp_path,
        f"esq_atomic_set_env_file '{env_file}' {env_file.stat().st_uid} "
        f"{env_file.stat().st_gid} 640 EASYSYNQ_COMPATIBILITY_READ_ONLY 1",
        library=library,
    )
    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "SECRET=old-final\n"
    retained = list((tmp_path / ".env.rollback-quarantine").iterdir())
    contents = {path.read_text(encoding="utf-8") for path in retained}
    assert "RACER=final-failure\n" in contents
    assert any(
        content == "SECRET=old-final\nEASYSYNQ_COMPATIBILITY_READ_ONLY=1\n" for content in contents
    )
    assert len(retained) == 2
    assert all(path.stat().st_mode & 0o777 == 0o640 for path in retained)
    _assert_inode_report_and_inventory_find_retained_entry(tmp_path, env_file, library, result)
    assert str(tmp_path / ".env.rollback-quarantine") in result.stderr
    assert "SECRET=old-final" not in result.stderr


def test_appliance_rejects_invalid_hostname_and_real_openssl_rejects_ca(
    tmp_path: pathlib.Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sudo_log = tmp_path / "sudo-log"
    _write_executable(
        fake_bin / "sudo",
        """
        #!/bin/bash
        set -euo pipefail
        printf '%s\n' "$*" >>"$ESQ_SUDO_LOG"
        printf '%s\n' 'not-a-certificate'
        """,
    )
    invalid_host = _run_library(
        tmp_path,
        "ESQ_MODE=appliance\nESQ_BASE_URL=https://bad..host\nesq_configure_curl",
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "ESQ_SUDO_LOG": str(sudo_log)},
    )
    assert invalid_host.returncode != 0
    assert not sudo_log.exists()
    invalid_ca = _run_library(
        tmp_path,
        "ESQ_MODE=appliance\nESQ_BASE_URL=https://valid.example\nesq_configure_curl",
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "ESQ_SUDO_LOG": str(sudo_log)},
    )
    assert invalid_ca.returncode != 0
    assert sudo_log.read_text(encoding="utf-8") == "easysynq-status --ca\n"


def test_repository_build_and_cleanup_failures_are_safe(tmp_path: pathlib.Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_log = tmp_path / "git-log"
    docker_log = tmp_path / "docker-log"
    commit = "a" * 40
    _write_executable(
        fake_bin / "git",
        """
        #!/bin/bash
        set -euo pipefail
        printf '%s\n' "$*" >>"$ESQ_GIT_LOG"
        if [ "$1" = rev-parse ] && [ "$2" = --verify ]; then
          printf '%s\n' "$ESQ_COMMIT"
        elif [ "$1" = rev-parse ] && [ "$2" = --show-toplevel ]; then
          printf '%s\n' "$ESQ_REPOSITORY_ROOT"
        elif [ "$1" = -C ] && [ "$3" = worktree ] && [ "$4" = add ]; then
          mkdir "$6"
          printf '%s\n' "$6" >"$ESQ_WORKTREE_PATH"
        elif [ "$1" = -C ] && [ "$3" = worktree ] && [ "$4" = remove ]; then
          rmdir "$6"
          [ "${ESQ_REMOVE_FAIL:-0}" = 0 ] || exit 63
        else
          exit 61
        fi
        """,
    )
    _write_executable(
        fake_bin / "docker",
        """
        #!/bin/bash
        set -euo pipefail
        printf '%s\n' "$*" >>"$ESQ_DOCKER_LOG"
        if [ "$1" = build ]; then
          [ "${ESQ_BUILD_FAIL:-0}" = 0 ] || exit 62
        elif [ "$1" = image ] && [ "$2" = inspect ]; then
          printf '%s\n' "$ESQ_SELECTED_ID"
        else
          exit 64
        fi
        """,
    )
    worktree_path = tmp_path / "worktree-path"
    result = _run_library(
        tmp_path,
        "ESQ_MODE=repository\nESQ_DOCKER=(docker)\n"
        "ESQ_API_SERVICE_IMAGE=easysynq-api:latest\n"
        "esq_select_api_artifact rollback",
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "ESQ_GIT_LOG": str(git_log),
            "ESQ_DOCKER_LOG": str(docker_log),
            "ESQ_COMMIT": commit,
            "ESQ_REPOSITORY_ROOT": str(tmp_path),
            "ESQ_WORKTREE_PATH": str(worktree_path),
            "ESQ_BUILD_FAIL": "1",
            "ESQ_REMOVE_FAIL": "0",
            "ESQ_SELECTED_ID": _image_id("5"),
        },
        stdin=f"{commit}\n",
    )
    assert result.returncode != 0
    built_source = pathlib.Path(worktree_path.read_text(encoding="utf-8").strip())
    assert not built_source.exists()
    assert not built_source.parent.exists()
    assert "worktree remove --force" in git_log.read_text(encoding="utf-8")

    git_log.write_text("", encoding="utf-8")
    result = _run_library(
        tmp_path,
        "ESQ_MODE=repository\nESQ_DOCKER=(docker)\n"
        "ESQ_API_SERVICE_IMAGE=easysynq-api:latest\n"
        "esq_select_api_artifact rollback",
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "ESQ_GIT_LOG": str(git_log),
            "ESQ_DOCKER_LOG": str(docker_log),
            "ESQ_COMMIT": commit,
            "ESQ_REPOSITORY_ROOT": str(tmp_path),
            "ESQ_WORKTREE_PATH": str(worktree_path),
            "ESQ_BUILD_FAIL": "0",
            "ESQ_REMOVE_FAIL": "1",
            "ESQ_SELECTED_ID": _image_id("5"),
        },
        stdin=f"{commit}\n",
    )
    assert result.returncode != 0
    failed_cleanup_source = pathlib.Path(worktree_path.read_text(encoding="utf-8").strip())
    assert not failed_cleanup_source.exists()
    assert failed_cleanup_source.parent.exists()
    assert git_log.read_text(encoding="utf-8").count("worktree remove --force") >= 1
    failed_cleanup_source.parent.rmdir()


def test_recovery_block_rejects_the_rollback_image_before_compose(
    tmp_path: pathlib.Path,
) -> None:
    block = _bash_block("recovery-artifact-selection")
    compose_marker = tmp_path / "compose-called"
    body = textwrap.dedent(
        f"""
        ESQ_ROLLBACK_API_IMAGE_ID='{_image_id("4")}'
        SELECTED_API_IMAGE_ID=''
        esq_select_api_artifact() {{ SELECTED_API_IMAGE_ID='{_image_id("4")}'; }}
        fake_compose() {{ touch '{compose_marker}'; }}
        ESQ_COMPOSE=(fake_compose)
        esq_require_running_api_image() {{ return 0; }}
        {block}
        """
    )
    result = _run_library(tmp_path, body)
    assert result.returncode != 0
    assert not compose_marker.exists()


def test_setter_rejects_invalid_duplicate_symlink_and_metadata_drift(
    tmp_path: pathlib.Path,
) -> None:
    uid = tmp_path.stat().st_uid
    gid = tmp_path.stat().st_gid
    cases = [
        ("bad-key", "OTHER_KEY", "1", "SECRET=old\n", 0o640),
        ("bad-value", "EASYSYNQ_COMPATIBILITY_READ_ONLY", "yes", "SECRET=old\n", 0o640),
        (
            "duplicate",
            "EASYSYNQ_COMPATIBILITY_READ_ONLY",
            "1",
            "EASYSYNQ_COMPATIBILITY_READ_ONLY=0\nEASYSYNQ_COMPATIBILITY_READ_ONLY=1\n",
            0o640,
        ),
        ("metadata", "EASYSYNQ_COMPATIBILITY_READ_ONLY", "1", "SECRET=old\n", 0o600),
    ]
    for name, key, value, content, mode in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        env_file = case_dir / ".env"
        env_file.write_text(content, encoding="utf-8")
        env_file.chmod(mode)
        result = _run_library(
            case_dir,
            f"esq_atomic_set_env_file '{env_file}' {uid} {gid} 640 {key} {value}",
        )
        assert result.returncode != 0, name
        assert env_file.read_text(encoding="utf-8") == content
        assert not list(case_dir.glob(".env.upload-identity.*"))

    target = tmp_path / "symlink-target"
    target.write_text("SECRET=old\n", encoding="utf-8")
    target.chmod(0o640)
    link = tmp_path / "symlink-env"
    link.symlink_to(target)
    result = _run_library(
        tmp_path,
        f"esq_atomic_set_env_file '{link}' {uid} {gid} 640 EASYSYNQ_COMPATIBILITY_READ_ONLY 1",
    )
    assert result.returncode != 0
    assert target.read_text(encoding="utf-8") == "SECRET=old\n"
