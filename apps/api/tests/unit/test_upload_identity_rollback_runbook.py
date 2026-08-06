"""Executable/static guards for the exact-version rollback operator procedure."""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import textwrap

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


def test_env_setter_is_mode_specific_privileged_and_atomic() -> None:
    library = _bash_block("rollback-helper-library")

    assert "esq_set_env_appliance" in library
    assert "esq_set_env_repository" in library
    assert "sudo bash -c" in library
    assert "/opt/easysynq/.env" in library
    assert "root:easysynq" in library
    assert (
        "esq_atomic_set_env_file /opt/easysynq/.env 0 '$easysynq_gid' 640"
        in library
    )
    assert 'esq_atomic_set_env_file "$ESQ_ENV_FILE" "$owner" "$group" "$mode"' in library
    assert "EASYSYNQ_COMPATIBILITY_READ_ONLY" in library
    assert "0|1" in library
    assert "test ! -L" in library
    assert "mktemp" in library
    assert "mv -T" in library
    assert "trap" in library


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
        appliance_uid=0
        appliance_gid=65534
        chown_called=0
        stat() {{
          local format="$2" target="$3"
          case "$format" in
            %u) printf '%s\n' "$appliance_uid" ;;
            %g) printf '%s\n' "$appliance_gid" ;;
            %a|%d:%i) command stat -c "$format" "$target" ;;
            %u:%g:%a)
              printf '%s:%s:%s\n' "$appliance_uid" "$appliance_gid" \
                "$(command stat -c %a "$target")"
              ;;
            *) return 1 ;;
          esac
        }}
        chown() {{
          test "$1" = "$appliance_uid:$appliance_gid"
          chown_called=1
        }}
        printf '%s\n' 'SECRET_SENTINEL=not-printed' 'EASYSYNQ_PROFILE=s' >"$env_file"
        chmod 0640 "$env_file"
        esq_atomic_set_env_file "$env_file" "$appliance_uid" "$appliance_gid" 640 \
          EASYSYNQ_COMPATIBILITY_READ_ONLY 1
        test "$chown_called" = 1
        test "$(stat -c '%u:%g:%a' "$env_file")" = '0:65534:640'
        test "$(command stat -c %a "$env_file")" = 640
        test "$(grep -c '^EASYSYNQ_COMPATIBILITY_READ_ONLY=1$' "$env_file")" = 1
        test "$(grep -c '^SECRET_SENTINEL=not-printed$' "$env_file")" = 1
        test -z "$(find "$(dirname "$env_file")" -maxdepth 1 \
          -name '.env.upload-identity.*' -print -quit)"
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


def test_each_api_cutover_selects_retags_recreates_and_verifies_exact_image() -> None:
    text = _text()
    rollback = text.index("# rollback-artifact-selection")
    recovery = text.index("# recovery-artifact-selection")
    rollback_slice = text[rollback:recovery]
    recovery_slice = text[recovery:]

    for section in (rollback_slice, recovery_slice):
        assert "esq_select_api_artifact" in section
        assert "SELECTED_API_IMAGE_ID" in section
        assert 'up -d --no-deps --force-recreate --no-build api' in section
        assert "esq_require_running_api_image" in section
        assert section.index("esq_select_api_artifact") < section.index("force-recreate")
        assert section.index("force-recreate") < section.index(
            "esq_require_running_api_image"
        )

    library = _bash_block("rollback-helper-library")
    assert "sha256sum" in library
    assert "docker load" in library
    assert "git rev-parse --verify" in library
    assert 'worktree add --detach "$build_source" "$resolved_commit"' in library
    assert "docker build" in library
    assert "docker container inspect" in library
    assert "{{.Image}}" in library
    assert 'image tag "$SELECTED_API_IMAGE_ID" "$ESQ_API_SERVICE_IMAGE"' in library
    assert (
        '[ "$ESQ_RECOVERY_API_IMAGE_ID" != "$ESQ_ROLLBACK_API_IMAGE_ID" ]'
        in text
    )


def test_every_http_probe_uses_mode_specific_curl_array() -> None:
    text = _text()
    library = _bash_block("rollback-helper-library")

    assert "ESQ_CURL=(curl --silent --show-error)" in library
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
