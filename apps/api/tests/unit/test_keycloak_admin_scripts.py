"""Real Compose parser; synthetic credentials; discovery/exec never touch a service."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ("new-keycloak-user.sh", "clear-keycloak-lockout.sh")


@pytest.fixture
def deployment(tmp_path):
    docker = shutil.which("docker")
    assert docker is not None, "real Compose required for parser regression"
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    shutil.copytree(ROOT / "infra", tmp_path / "infra")
    (tmp_path / ".env").write_text(
        "KEYCLOAK_DB_PASSWORD=synthetic-db-only\n"
        + "".join(
            f"{key}=https://app.example\n"
            for key in ("APP_BASE_URL", "PUBLIC_BASE_URL", "SITE_ADDRESS", "KEYCLOAK_HOSTNAME")
        )
        + "S3_PUBLIC_ENDPOINT=https://objects.example\nMINIO_SITE_ADDRESS=https://objects.example\n"
    )
    files = [
        str(tmp_path / "infra/compose" / f"compose{suffix}.yml")
        for suffix in ("", ".s", ".production", ".offline")
    ]
    shim = tmp_path / "bin/docker"
    shim.parent.mkdir()
    shim.write_text("""#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as out:
    out.write(json.dumps({"args": args, "pathconv": os.getenv("MSYS_NO_PATHCONV")}) + "\\n")
if args[0] == "ps":
    print(os.environ["ACTIVE_FILES"])
    sys.exit(0)
if args[0] == "compose" and "exec" not in args and "config" in args:
    if os.getenv("FAIL_CONFIG"):
        print("synthetic-secret-parser-diagnostic", file=sys.stderr)
        sys.exit(1)
    # Native Windows Docker consumes C:/ paths; map the synthetic drive for this Linux proof.
    if os.getenv("NATIVE_FIXTURE"):
        args = [arg.replace("C:/fixture", os.environ["NATIVE_FIXTURE"]) for arg in args]
    sys.exit(subprocess.call([os.environ["REAL_DOCKER"], *args]))
if args[0] == "compose" and "exec" in args:
    sys.exit(42)  # stop at authentication; never call a real service
raise SystemExit("unexpected Docker mutation")
""")
    shim.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("KEYCLOAK_", "COMPOSE_", "EASYSYNQ_"))
    }
    env.update(
        PATH=f"{shim.parent}:{env['PATH']}",
        REAL_DOCKER=docker,
        COMMAND_LOG=str(tmp_path / "commands.jsonl"),
        ACTIVE_FILES=",".join(files),
    )
    return tmp_path, env, files


def invoke(deployment, script):
    root, env, _ = deployment
    result = subprocess.run(  # noqa: S603 - fixed scripts and synthetic argv
        ["/bin/bash", str(root / "scripts" / script), "synthetic-user"],
        cwd=root,
        env=env,
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    log = Path(env["COMMAND_LOG"])
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    ("dotenv", "expected"),
    [
        ('"abc\\"def"', 'abc"def'),
        ('"abc\\\\def"', "abc\\def"),
        ('"abc\\\\"', "abc\\"),
        ('"  abc  "', "  abc  "),
        ('"abc\\n"', "abc\n"),
        ("'abc#def'", "abc#def"),
        ("abc # comment", "abc"),
        ('"abc#def" # comment', "abc#def"),
        ('"${SYNTHETIC_PART}tail"', "headtail"),
    ],
)
def test_admin_password_matches_compose(deployment, script, dotenv, expected):
    root, _, files = deployment
    with (root / ".env").open("a", newline="") as out:
        out.write(
            'SYNTHETIC_PART=head\r\nKEYCLOAK_ADMIN_USER="operator name"\r\n'
            f"KEYCLOAK_ADMIN_PASSWORD={dotenv}\r\n"
        )
    result, calls = invoke(deployment, script)
    assert result.returncode == 42, result.stderr
    auth = [call for call in calls if "exec" in call["args"]]
    assert len(auth) == 1
    args = auth[0]["args"]
    assert args[args.index("--password") + 1] == expected
    assert args[args.index("--user") + 1] == "operator name"
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "-f"] == files
    assert args[args.index("exec") : args.index("exec") + 4] == [
        "exec",
        "-T",
        "keycloak",
        "/opt/keycloak/bin/kcadm.sh",
    ]
    assert auth[0]["pathconv"] == "1"
    assert expected not in result.stdout + result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_admin_password_obeys_host_precedence_and_final_overlay(deployment, script):
    root, env, files = deployment
    with (root / ".env").open("a") as out:
        out.write("KEYCLOAK_ADMIN_PASSWORD=dotenv-synthetic\nCOMPOSE_PROJECT_NAME=dotenv-project\n")
    env.update(KEYCLOAK_ADMIN_PASSWORD="host-synthetic\n", COMPOSE_PROJECT_NAME="host-project")
    overlay = root / "credential override.yml"
    overlay.write_text(
        "services:\n  keycloak:\n    environment:\n"
        "      KC_BOOTSTRAP_ADMIN_USERNAME: overlay-operator\n"
        '      KC_BOOTSTRAP_ADMIN_PASSWORD: "${KEYCLOAK_ADMIN_PASSWORD}suffix"\n'
    )
    files.append(str(overlay))
    env["ACTIVE_FILES"] = ",".join(files)
    result, calls = invoke(deployment, script)
    assert result.returncode == 42, result.stderr
    args = next(call["args"] for call in calls if "exec" in call["args"])
    assert args[args.index("--password") + 1] == "host-synthetic\nsuffix"
    assert args[args.index("--user") + 1] == "overlay-operator"
    assert any("label=com.docker.compose.project=host-project" in call["args"] for call in calls)


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(
    "failure",
    [
        "empty",
        "missing",
        "malformed",
        "parser",
        "no-deployment",
        "ambiguous",
        "missing-file",
        "foreign",
    ],
)
def test_admin_resolution_failure_never_authenticates_or_leaks(deployment, script, failure):
    root, env, files = deployment
    password = {"empty": '""', "malformed": '"synthetic-secret-parser-diagnostic'}.get(
        failure, '"synthetic-secret-parser-diagnostic"'
    )
    if failure != "missing":
        with (root / ".env").open("a") as out:
            out.write(f"KEYCLOAK_ADMIN_PASSWORD={password}\n")
    if failure == "parser":
        env["FAIL_CONFIG"] = "1"
    elif failure == "no-deployment":
        env["ACTIVE_FILES"] = ""
    elif failure == "ambiguous":
        env["ACTIVE_FILES"] += "\n" + files[0]
    elif failure == "missing-file":
        env["ACTIVE_FILES"] += ",/nonexistent/synthetic.yml"
    elif failure == "foreign":
        foreign = root / "foreign.yml"
        shutil.copyfile(files[0], foreign)
        env["ACTIVE_FILES"] = str(foreign)
    result, calls = invoke(deployment, script)
    assert result.returncode != 0
    assert not any("exec" in call["args"] for call in calls)
    assert "synthetic-secret-parser-diagnostic" not in result.stdout + result.stderr
    assert result.stderr.strip()


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("value", ['""', "null", '"nul\\0byte"'])
def test_invalid_final_service_password_fails_closed(deployment, script, value):
    root, env, files = deployment
    with (root / ".env").open("a") as out:
        out.write("KEYCLOAK_ADMIN_PASSWORD=synthetic-source\n")
    overlay = root / "invalid-credential.yml"
    overlay.write_text(
        f"services:\n  keycloak:\n    environment:\n      KC_BOOTSTRAP_ADMIN_PASSWORD: {value}\n"
    )
    files.append(str(overlay))
    env["ACTIVE_FILES"] = ",".join(files)
    result, calls = invoke(deployment, script)
    assert result.returncode == 1
    assert not any("exec" in call["args"] for call in calls)
    assert "synthetic-source" not in result.stdout + result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_duplicate_container_labels_and_default_username(deployment, script):
    root, env, _ = deployment
    with (root / ".env").open("a") as out:
        out.write("KEYCLOAK_ADMIN_PASSWORD=synthetic-source\n")
    env["ACTIVE_FILES"] += "\n" + env["ACTIVE_FILES"]
    result, calls = invoke(deployment, script)
    assert result.returncode == 42, result.stderr
    args = next(call["args"] for call in calls if "exec" in call["args"])
    assert args[args.index("--user") + 1] == "admin"


@pytest.mark.parametrize("script", SCRIPTS)
def test_native_windows_paths_are_converted_without_changing_container_paths(deployment, script):
    root, env, files = deployment
    with (root / ".env").open("a") as out:
        out.write("KEYCLOAK_ADMIN_PASSWORD=synthetic-source\n")
    converter = root / "bin/cygpath"
    converter.write_text("""#!/usr/bin/env python3
import os, sys
root = os.environ["NATIVE_FIXTURE"]
mode, value = sys.argv[1:]
print(value.replace(root, "C:/fixture") if mode == "-m" else value.replace("C:/fixture", root))
""")
    converter.chmod(0o755)
    env["NATIVE_FIXTURE"] = str(root)
    native_files = [p.replace(str(root), "C:/fixture") for p in files]
    env["ACTIVE_FILES"] = ",".join(native_files)
    result, calls = invoke(deployment, script)
    assert result.returncode == 42, result.stderr
    args = next(call["args"] for call in calls if "exec" in call["args"])
    assert args[args.index("--env-file") + 1] == "C:/fixture/.env"
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "-f"] == native_files
    assert "/opt/keycloak/bin/kcadm.sh" in args
