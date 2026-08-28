"""Audit U33-U36, U40, U43, U44 — infrastructure and operator-tooling invariants.

These are source-level pins for properties CI cannot otherwise observe: CI builds nothing from the
Dockerfiles and never runs a container without a network, so a regression here ships green.

The load-bearing one is ``UV_NO_SYNC``. ``uv run`` syncs the environment on every invocation and
resolves the DEV dependency group the image deliberately did not install, so every container start
reached PyPI. Verified against an image built from ``main``: ``uv run alembic --version`` under
``--network none`` fails with a DNS error, which means an air-gapped stack died on ``migrate``
before anything else ran.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infra" / "compose" / "compose.yml").exists():
            return parent
    raise AssertionError("repository root not found above the test directory")


ROOT = _repo_root()


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _instructions(dockerfile: str) -> list[str]:
    """The Dockerfile's actual instruction lines.

    A substring check cannot tell ``USER easysynq`` from ``# USER easysynq`` — commenting the
    directive out would leave the container running as root and still satisfy the assertion.
    """
    return [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


# --- U33: the application containers must not run as root ------------------------------------


def test_api_image_runs_unprivileged_and_owns_its_volume_mount_points() -> None:
    dockerfile = _read("apps/api/Dockerfile")
    assert "USER easysynq" in _instructions(dockerfile)
    # Pre-creating the mount points is what makes a FRESH named volume writable without an
    # operator step — Docker seeds a new volume from the image directory behind it.
    for path in (
        "/var/lib/easysynq/qms-mirror",
        "/var/lib/easysynq/backups",
        "/run/secrets",
    ):
        assert path in dockerfile, f"{path} is a written volume mount and must be pre-owned"
    assert "install -d -o easysynq -g easysynq" in dockerfile
    assert "chown -R easysynq:easysynq /app" in dockerfile, "the venv must be writable-by-owner"


def test_web_image_runs_unprivileged() -> None:
    assert "USER node" in _instructions(_read("apps/web/Dockerfile"))


def test_the_api_user_has_a_fixed_uid() -> None:
    """A floating uid would not match the ownership a previously-seeded volume carries."""
    dockerfile = _read("apps/api/Dockerfile")
    assert "ARG APP_UID=10001" in dockerfile
    assert "ARG APP_GID=10001" in dockerfile
    assert "--uid ${APP_UID}" in dockerfile


# --- the offline-start regression this slice found --------------------------------------------


def test_uv_run_never_syncs_at_container_start() -> None:
    """Without this the container reaches PyPI on every start — fatal on an air-gapped host.

    ``uv run`` syncs before executing, and it resolves the dev group the image did not install
    (``--no-dev``), so ``migrate``/``api``/``worker``/``beat`` all needed the network to boot.
    """
    dockerfile = _read("apps/api/Dockerfile")
    assert "UV_NO_SYNC=1" in dockerfile, (
        "uv run syncs on every invocation; without UV_NO_SYNC an offline start fails on migrate"
    )
    assert "UV_FROZEN=1" in dockerfile
    assert "UV_NO_CACHE=1" in dockerfile


# --- U34: the image installs exactly what the lockfile pins ------------------------------------


def test_image_dependencies_come_from_the_lockfile_only() -> None:
    dockerfile = _read("apps/api/Dockerfile")
    assert "uv sync --no-dev --no-install-project" not in dockerfile
    assert dockerfile.count("uv sync --locked") == 2, "both sync layers must be --locked"
    # A globbed `uv.lock*` silently tolerates a MISSING lockfile and resolves fresh. Inspect the
    # COPY directives rather than the raw text — the Dockerfile comment quotes the glob it warns
    # against, and a substring check would match that instead of the instruction.
    copies = [ln for ln in dockerfile.splitlines() if ln.startswith("COPY ")]
    assert not [ln for ln in copies if "uv.lock*" in ln], f"globbed lockfile COPY: {copies}"
    assert "COPY apps/api/pyproject.toml apps/api/uv.lock ./" in copies


# --- U35: SELinux labels on the config bind mounts, but never on the customer's tree ------------


@pytest.mark.parametrize(
    ("compose_file", "mount"),
    [
        ("infra/compose/compose.yml", "./minio:/init:ro,z"),
        ("infra/compose/compose.yml", "./keycloak/keycloak-init.sh:/init/keycloak-init.sh:ro,z"),
        (
            "infra/compose/compose.yml",
            "./keycloak/realm-export.json:/seed/easysynq-realm.json:ro,z",
        ),
        ("infra/compose/compose.yml", "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro,z"),
        (
            "infra/compose/compose.production.yml",
            "./caddy/Caddyfile.production:/etc/caddy/Caddyfile:ro,z",
        ),
        (
            "infra/compose/compose.production.yml",
            "./caddy/Caddyfile:/etc/caddy/Caddyfile.base:ro,z",
        ),
    ],
)
def test_shipped_config_bind_mounts_carry_the_selinux_label(compose_file: str, mount: str) -> None:
    """Unlabelled, these fail with permission-denied on an SELinux-labelling runtime."""
    assert mount in _read(compose_file)


def test_the_customer_document_tree_is_never_relabelled() -> None:
    """`,z` relabels RECURSIVELY. Repo config is ours to relabel; the org's file store is not."""
    compose = _read("infra/compose/compose.yml")
    assert "${IMPORT_SOURCE_PATH:-../../.import-source}:/srv/import/source:ro\n" in compose
    assert "/srv/import/source:ro,z" not in compose
    assert "container_file_t" in _read("docs/runbooks/install-online.md"), (
        "the operator must be told how to label their own tree"
    )


# --- U36: the shipped template must not aim production mail at a dev-only container -------------


def test_env_template_ships_no_smtp_transport() -> None:
    """An empty SMTP_HOST is the app's "no deliverable transport" state.

    ``services/notifications/delivery.py::smtp_transport_configured`` reads it, and the drain then
    suppresses cleanly instead of queueing mail at a host that will never answer.
    """
    env_example = _read(".env.example")
    assert "\nSMTP_HOST=\n" in env_example, "SMTP_HOST must ship empty, not pointed at mailpit"
    assert "\nSMTP_HOST=mailpit" not in env_example


# --- U43/U44: no unbounded wait, no swallowed credential failure --------------------------------


def test_minio_init_gives_up_instead_of_hanging_the_stack(tmp_path: Path) -> None:
    """Behavioural: run the real script against an `mc` that never succeeds.

    api, worker and beat all gate on this one-shot via service_completed_successfully, so an
    unbounded `until` loop turns a wrong S3 credential into a whole-stack hang whose only symptom
    is "waiting for MinIO..." forever. Asserting the variable exists is not enough — the bound has
    to actually fire.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mc = fake_bin / "mc"
    fake_mc.write_text("#!/bin/sh\necho 'mc: Unable to initialize: Access Denied.' >&2\nexit 1\n")
    fake_mc.chmod(0o755)

    started = time.monotonic()
    result = subprocess.run(  # noqa: S603 - fixed repository script with an isolated fake mc
        ["/bin/sh", str(ROOT / "infra/compose/minio/minio-init.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PUBLIC_BASE_URL": "https://qms.example.test",
            "MINIO_WAIT_SECONDS": "4",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    elapsed = time.monotonic() - started

    assert result.returncode != 0, "an unreachable MinIO must fail the one-shot, not hang it"
    assert elapsed < 30, f"the wait was not bounded (took {elapsed:.1f}s)"
    assert "not reachable" in result.stderr
    # The operator needs mc's own words, or the cause is invisible in `docker compose logs`.
    assert "Access Denied" in result.stderr
    assert "S3_ACCESS_KEY" in result.stderr


def test_seed_personas_fails_loudly_when_a_password_cannot_be_set() -> None:
    """A swallowed failure leaves the account enabled but unusable, with no clue at login time."""
    script = _read("scripts/seed-personas.sh")
    assert (
        'kc set-password -r easysynq --username "$u" --new-password "Demo-Password-1" 2>&1'
        in script
    )
    assert '--new-password "Demo-Password-1" >/dev/null 2>&1 || true' not in script


# --- U40: the CLI docstring must describe the CLI, not the host wrapper -------------------------


def test_grant_role_docstring_names_the_flag_its_parser_requires() -> None:
    """The module's parser requires --subject; only the host wrapper takes it positionally."""
    module = _read("apps/api/src/easysynq_api/cli/grant_role.py")
    assert 'parser.add_argument("--subject", required=True' in module
    assert "python -m easysynq_api.cli.grant_role --subject" in module
    assert "./scripts/easysynq grant-role" in module
