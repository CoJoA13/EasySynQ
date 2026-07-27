"""Batch 13 deployment invariants.

These source-level checks complement ``docker compose config --quiet`` in CI. They pin the security
boundaries that are easy to accidentally erase while editing an overlay: production never publishes
plaintext MinIO, every browser URL is supplied together, and Keycloak's live store is PostgreSQL.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infra" / "compose" / "compose.yml").exists():
            return parent
    raise AssertionError("repository root not found above the test directory")


ROOT = _repo_root()


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_minio_host_publish_is_dev_only_and_loopback_bound() -> None:
    sizing = _read("infra/compose/compose.s.yml")
    dev = _read("infra/compose/compose.dev.yml")
    production = _read("infra/compose/compose.production.yml")

    assert "S3_PORT" not in sizing
    assert "127.0.0.1:${S3_PORT:-9000}:9000" in dev
    assert "ports: !reset []" in production
    assert '"9443:9443"' in production
    assert ":9000:9000" not in production


def test_production_requires_one_consistent_browser_edge() -> None:
    production = _read("infra/compose/compose.production.yml")
    caddy = _read("infra/compose/caddy/Caddyfile.production")
    installer = _read("scripts/install.sh")
    template = _read(".env.example")

    for key in (
        "SITE_ADDRESS",
        "MINIO_SITE_ADDRESS",
        "S3_PUBLIC_ENDPOINT",
        "PUBLIC_BASE_URL",
        "APP_BASE_URL",
        "KEYCLOAK_HOSTNAME",
    ):
        assert f"${{{key}:?" in production, f"production overlay does not require {key}"

    assert "{$MINIO_SITE_ADDRESS}" in caddy
    assert "{$CADDY_TLS_DIRECTIVE}" in caddy
    assert "reverse_proxy minio:9000" in caddy

    assert 'MINIO_ORIGIN="https://${HOST_NAME}:9443"' in installer
    assert "${APP_ORIGIN}/*" in installer
    assert "easysynq-web client" in installer
    assert "compose.production.yml" in installer
    assert "S3_PUBLIC_ENDPOINT=http://localhost:9000" not in template
    assert "APP_BASE_URL=" in template


def test_appliance_propagates_qr_share_and_deep_link_origins() -> None:
    provision = _read("infra/appliance/provision/easysynq-provision.sh")
    reconfigure = _read("infra/appliance/provision/bin/easysynq-reconfigure")
    compose_helper = _read("infra/appliance/provision/bin/easysynq-compose")

    for key in ("PUBLIC_BASE_URL", "APP_BASE_URL"):
        assert f'set_kv {key} "https://${{HOSTNAME_DEFAULT}}"' in provision
        assert f'set_kv {key} "https://${{HOST}}"' in reconfigure

    # The day-2 helper defines HOST, not HOSTNAME_DEFAULT; this guards the original set -u trap.
    host_updates = reconfigure[reconfigure.index("set_kv SITE_ADDRESS") :]
    assert "HOSTNAME_DEFAULT" not in host_updates

    # Pre-Batch-13 appliance env files are read-only to the helper user. The helper derives these
    # values in-process for the first upgrade instead of failing production-overlay interpolation.
    assert "for key in PUBLIC_BASE_URL APP_BASE_URL KEYCLOAK_HOSTNAME" in compose_helper
    assert "up_needs_keycloak_migration" in compose_helper
    assert 'targets+=("$arg")' in compose_helper
    assert '[ "$target" = "keycloak" ]' in compose_helper


def test_keycloak_runs_optimized_on_durable_postgres_schema() -> None:
    compose = _read("infra/compose/compose.yml")
    image = _read("infra/compose/keycloak/Dockerfile")
    init = _read("infra/compose/keycloak/keycloak-init.sh")
    migration = _read("scripts/migrate-keycloak-h2.sh")
    restore_runbook = _read("docs/runbooks/backup-restore.md")
    template = _read(".env.example")

    assert "start-dev" not in compose
    assert 'command: ["start", "--optimized", "--import-realm"]' in compose
    assert "KC_DB: postgres" in compose
    assert "KEYCLOAK_DB_NAME:-${POSTGRES_DB" in compose
    assert "KC_DB_SCHEMA: keycloak" in compose
    assert "KC_DB_USERNAME: easysynq_keycloak" in compose
    assert "KEYCLOAK_DB_PASSWORD:-${POSTGRES_PASSWORD" in compose
    assert "keycloakimport:/opt/keycloak/data/import:ro" in compose
    assert "condition: service_completed_successfully" in compose

    assert "RUN /opt/keycloak/bin/kc.sh build" in image
    assert "CREATE ROLE easysynq_keycloak LOGIN" in init
    assert "CREATE SCHEMA keycloak AUTHORIZATION easysynq_keycloak" in init
    assert "ALTER %s %I.%I OWNER TO easysynq_keycloak" in init
    assert "ALL TABLES IN SCHEMA keycloak" in init
    assert "KEYCLOAK_DB_NAME=" in template
    assert "`KEYCLOAK_DB_NAME`" in restore_runbook
    assert "transfers restored `keycloak` schema objects" in restore_runbook

    # A transition must export users/credential hashes before the legacy container is replaced.
    assert "docker stop --time 60" in migration
    assert "--users realm_file" in migration
    assert ".legacy-h2-export-complete" in migration
    assert "com.docker.compose.volume=keycloakimport" in migration
    assert 'IMPORT_VOLUME="${IMPORT_VOLUME:-${PROJECT}_keycloakimport}"' in migration
    assert "restarting the untouched legacy container" in migration
    assert "name: easysynq-keycloak-import" not in compose


def test_h2_migration_reads_custom_compose_project_from_env_file(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        "# `docker ps` intentionally returns no container IDs.\n"
    )
    fake_docker.chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text('COMPOSE_PROJECT_NAME="customer-qms"\n')
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_LOG": str(docker_log),
    }

    result = subprocess.run(  # noqa: S603 - fixed test script and isolated fake PATH
        [
            "/bin/bash",
            str(ROOT / "scripts/migrate-keycloak-h2.sh"),
            "--env-file",
            str(env_file),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "no legacy container found" in result.stdout
    assert "label=com.docker.compose.project=customer-qms" in docker_log.read_text()


def test_h2_migration_fails_closed_when_container_discovery_errors(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        '#!/bin/sh\nif [ "$1" = "ps" ]; then\n  printf "daemon unavailable\\n" >&2\n  exit 1\nfi\n'
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    result = subprocess.run(  # noqa: S603 - fixed test script and isolated fake PATH
        ["/bin/bash", str(ROOT / "scripts/migrate-keycloak-h2.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "could not inspect legacy containers; refusing to continue" in result.stderr
    assert "no legacy container found" not in result.stdout


def test_h2_migration_scopes_import_volume_to_compose_project(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
printf "%s\\n" "$*" >> "$DOCKER_LOG"
if [ "$1" = "ps" ]; then
  printf "legacy-id\\n"
elif [ "$1 $2" = "inspect --format" ] && [ "$3" = "{{.Image}}" ]; then
  printf "sha256:legacy\\n"
elif [ "$1 $2" = "volume inspect" ]; then
  exit 1
fi
"""
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_LOG": str(docker_log),
    }

    result = subprocess.run(  # noqa: S603 - fixed test script and isolated fake PATH
        [
            "/bin/bash",
            str(ROOT / "scripts/migrate-keycloak-h2.sh"),
            "--project",
            "customer-qms",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "verified legacy export already staged" in result.stdout
    create = next(
        line for line in docker_log.read_text().splitlines() if line.startswith("volume create ")
    )
    assert "com.docker.compose.project=customer-qms" in create
    assert "com.docker.compose.volume=keycloakimport" in create
    assert create.endswith(" customer-qms_keycloakimport")


def test_production_entrypoints_require_compose_2_24_4(tmp_path: Path) -> None:
    checker = ROOT / "scripts/require-compose-version.sh"

    def check(version: str) -> subprocess.CompletedProcess[str]:
        fake_bin = tmp_path / version.replace(".", "_")
        fake_bin.mkdir()
        fake_docker = fake_bin / "docker"
        fake_docker.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
        fake_docker.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        }
        return subprocess.run(  # noqa: S603 - fixed checker and isolated fake PATH
            ["/bin/bash", str(checker)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    unsupported = check("2.24.3")
    assert unsupported.returncode != 0
    assert "2.24.4 or newer is required" in unsupported.stderr

    supported = check("2.24.4")
    assert supported.returncode == 0, supported.stderr

    installer = _read("scripts/install.sh")
    appliance = _read("infra/appliance/provision/bin/easysynq-compose")
    provisioner = _read("infra/appliance/provision/easysynq-provision.sh")
    assert 'bash "$ROOT/scripts/require-compose-version.sh"' in installer
    assert 'bash "$COMPOSE_VERSION_SCRIPT"' in appliance
    assert 'bash "$APP_DIR/scripts/require-compose-version.sh"' in provisioner


def test_appliance_targeted_up_only_migrates_when_keycloak_will_start(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        '#!/bin/sh\nif [ "$1 $2 $3" = "compose version --short" ]; then\n  printf "2.24.4\\n"\nfi\n'
    )
    fake_docker.chmod(0o755)
    migration_log = tmp_path / "migration.log"
    migration_args_log = tmp_path / "migration-args.log"
    fake_migration = tmp_path / "migrate.sh"
    fake_migration.write_text(
        '#!/bin/sh\nprintf "migrated\\n" >> "$MIGRATION_LOG"\n'
        'printf "%s\\n" "$*" >> "$MIGRATION_ARGS_LOG"\n'
    )
    fake_migration.chmod(0o755)
    (tmp_path / ".env").write_text("SITE_ADDRESS=https://easysynq.local\n")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "EASYSYNQ_APP_DIR": str(tmp_path),
        "EASYSYNQ_MIGRATION_SCRIPT": str(fake_migration),
        "EASYSYNQ_COMPOSE_VERSION_SCRIPT": str(ROOT / "scripts/require-compose-version.sh"),
        "MIGRATION_LOG": str(migration_log),
        "MIGRATION_ARGS_LOG": str(migration_args_log),
    }
    wrapper = ROOT / "infra/appliance/provision/bin/easysynq-compose"

    def run_wrapper(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed repository wrapper and isolated fake PATH
            ["/bin/bash", str(wrapper), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    result = run_wrapper("up", "-d", "api")
    assert result.returncode == 0, result.stderr
    assert not migration_log.exists()

    result = run_wrapper("up", "-d", "keycloak", "api")
    assert result.returncode == 0, result.stderr
    assert migration_log.read_text().splitlines() == ["migrated"]

    result = run_wrapper("up", "--timeout", "60", "-d")
    assert result.returncode == 0, result.stderr
    assert migration_log.read_text().splitlines() == ["migrated", "migrated"]

    result = run_wrapper("-p", "customer-qms", "up", "keycloak")
    assert result.returncode == 0, result.stderr
    assert migration_log.read_text().splitlines() == ["migrated", "migrated", "migrated"]
    assert migration_args_log.read_text().splitlines() == [
        "--env-file .env",
        "--env-file .env",
        "--env-file .env --project customer-qms",
    ]
