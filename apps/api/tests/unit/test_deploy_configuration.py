"""Batch 13 deployment invariants.

These source-level checks complement ``docker compose config --quiet`` in CI. They pin the security
boundaries that are easy to accidentally erase while editing an overlay: production never publishes
plaintext MinIO, every browser URL is supplied together, and Keycloak's live store is PostgreSQL.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


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


def test_minio_init_receives_sink_credentials_and_retention() -> None:
    compose = _read("infra/compose/compose.yml")
    template = _read(".env.example")

    for key in (
        "AUDIT_SINK_ACCESS_KEY",
        "AUDIT_SINK_SECRET_KEY",
        "AUDIT_SINK_READ_ACCESS_KEY",
        "AUDIT_SINK_READ_SECRET_KEY",
        "WORM_RETENTION",
    ):
        assert f"{key}: ${{{key}:-" in compose
    assert "WORM_RETENTION=30d" in template


def test_minio_init_versions_staging_without_unsupported_bucket_cors() -> None:
    init = _read("infra/compose/minio/minio-init.sh")
    compose = _read("infra/compose/compose.yml")

    assert "mc version enable local/staging" in init
    assert "mc version enable local/import-staging" in init
    assert "mc cors set" not in init
    assert "<CORSConfiguration>" not in init
    assert "MINIO_API_CORS_ALLOW_ORIGIN: ${PUBLIC_BASE_URL:-http://localhost}" in compose
    assert "--purge-on-delete" not in init
    assert "lifecycle" not in init.lower()


def _run_minio_init(tmp_path: Path, origin: str) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mc = fake_bin / "mc"
    fake_mc.write_text("#!/bin/sh\nexit 0\n")
    fake_mc.chmod(0o755)
    return subprocess.run(  # noqa: S603 - fixed script with an isolated fake mc
        ["/bin/sh", str(ROOT / "infra/compose/minio/minio-init.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PUBLIC_BASE_URL": origin,
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://one.test,http://two.test",
        "http://*",
        "http://bad test",
        "http://bad\ntest",
        "http://bad&test",
        "http://bad<test",
        'http://bad"test',
        "ftp://test",
        "http:///path",
        "http://test/path",
        "http://:",
        "http://host:",
        "https://host:not-a-port",
        "http://host:0",
        "http://host:65536",
        "http://host:999999999999999999999",
        "http://bad_host",
        "http://-host.test",
        "http://host-.test",
        "http://host..test",
        "http://.host",
        "http://host.",
        f"http://{'a' * 64}.test",
        "http://" + ".".join(["aa"] * 85),
        "http://::1",
        "http://[::1",
        "http://::1]",
        "http://[]",
        "http://[:::1]",
        "http://[2001:db8::1::1]",
        "http://[2001:db8:0:0:0:0:0:0:1]",
        "http://[1:2:3]",
        "http://[12345::1]",
        "http://[gggg::1]",
        "http://[::ffff:192.0.2.999]",
        "http://[::1]:",
        "http://[::1]:not-a-port",
        "http://[::1]:0",
        "http://[::1]:65536",
        "http://[::1]extra",
    ],
)
def test_minio_init_rejects_non_exact_browser_origins(tmp_path: Path, origin: str) -> None:
    result = _run_minio_init(tmp_path, origin)

    assert result.returncode != 0
    assert "one exact HTTP(S) origin" in result.stderr


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "http://localhost:9000",
        "https://qms.example.com",
        "https://QMS.example.com:9443",
        "http://[::1]",
        "http://[::1]:9000",
        "https://[2001:db8::1]",
        "https://[2001:DB8:0:1::abcd]:443",
        "http://[::ffff:192.0.2.1]",
    ],
)
def test_minio_init_accepts_valid_browser_origins(tmp_path: Path, origin: str) -> None:
    result = _run_minio_init(tmp_path, origin)

    assert result.returncode == 0, result.stderr


def test_staging_initializer_gates_every_promotion_capable_service() -> None:
    compose = yaml.safe_load(_read("infra/compose/compose.yml"))

    for service_name in ("api", "worker", "beat"):
        depends_on = compose["services"][service_name]["depends_on"]
        assert depends_on["minio-init"] == {"condition": "service_completed_successfully"}


def test_compose_passes_safe_cors_origin_and_default_off_rollback_guard() -> None:
    compose = _read("infra/compose/compose.yml")
    template = _read(".env.example")

    assert "PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:-http://localhost}" in compose
    assert "EASYSYNQ_COMPATIBILITY_READ_ONLY: ${EASYSYNQ_COMPATIBILITY_READ_ONLY:-0}" in compose
    assert "EASYSYNQ_COMPATIBILITY_READ_ONLY=0" in template


def test_compatibility_rollback_guard_precedes_every_api_proxy_handle() -> None:
    caddy = _read("infra/compose/caddy/Caddyfile")
    matcher = """@compatibility_rollback_write {
		path /api/*
		method POST PUT PATCH DELETE
		vars {env.EASYSYNQ_COMPATIBILITY_READ_ONLY} 1
	}"""

    assert matcher in caddy
    assert 'respond "Write operations are disabled during compatibility rollback." 503' in caddy
    guard_index = caddy.index("handle @compatibility_rollback_write")
    assert guard_index < caddy.index("handle @sse")
    assert guard_index < caddy.index("handle @public_html")
    assert guard_index < caddy.index("handle @api")


def test_import_source_default_resolves_to_repository_root() -> None:
    compose = _read("infra/compose/compose.yml")
    template = _read(".env.example")

    assert "IMPORT_SOURCE_PATH=../../.import-source" in template
    assert "${IMPORT_SOURCE_PATH:-../../.import-source}:/srv/import/source:ro" in compose
    assert "${IMPORT_SOURCE_PATH:-./.import-source}" not in compose


def test_web_image_uses_lockfile_and_excludes_host_artifacts() -> None:
    dockerfile = _read("apps/web/Dockerfile")
    dockerignore = _read("apps/web/.dockerignore").splitlines()

    assert "COPY package.json package-lock.json ./" in dockerfile
    assert "RUN npm ci" in dockerfile
    assert "npm ci ||" not in dockerfile
    assert {"node_modules", "dist", "coverage", ".vite"}.issubset(dockerignore)


def test_web_image_excludes_browser_harness_and_removes_playwright_from_runtime() -> None:
    dockerfile = _read("apps/web/Dockerfile")
    dockerignore = _read("apps/web/.dockerignore").splitlines()
    browser_only_context_roots = {
        "e2e",
        "playwright.config.ts",
        "tsconfig.browser.json",
        ".playwright-dist",
        "playwright-report",
        "test-results",
    }

    # These exact app-root patterns are interpreted by Docker before the broad `COPY . .`.
    assert browser_only_context_roots.issubset(dockerignore)
    assert not any(
        pattern.startswith("!") and pattern.removeprefix("!") in browser_only_context_roots
        for pattern in dockerignore
    )
    assert "COPY . ." in dockerfile

    install_build_cleanup = """RUN npm ci \\
    && npm run build \\
    && npm uninstall --no-save @playwright/test \\
    && npm cache clean --force"""
    assert install_build_cleanup in dockerfile
    assert dockerfile.index("COPY . .") < dockerfile.index(install_build_cleanup)
    assert dockerfile.count("RUN npm ci") == 1
    assert (
        'CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]' in dockerfile
    )


def test_production_requires_one_consistent_browser_edge() -> None:
    production = _read("infra/compose/compose.production.yml")
    caddy = _read("infra/compose/caddy/Caddyfile.production")
    installer = _read("scripts/install.sh")
    provisioner = _read("infra/appliance/provision/easysynq-provision.sh")
    compose_helper = _read("infra/appliance/provision/bin/easysynq-compose")
    reconfigure = _read("infra/appliance/provision/bin/easysynq-reconfigure")
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
    assert "easysynq_api.cli.keycloak_redirect" in installer
    assert "easysynq_api.cli.keycloak_redirect" in reconfigure
    assert "redirectUris=[" not in installer
    assert "redirectUris=[" not in reconfigure
    assert "compose.production.yml" in installer
    assert "validate-browser-origins.sh" in installer
    assert "validate-browser-origins.sh" in provisioner
    assert "validate-browser-origins.sh" in compose_helper
    assert "validate-browser-origins.sh" in reconfigure
    assert "validate-dns-name.sh" in installer
    assert "validate-dns-name.sh" in reconfigure
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
    justfile = _read("justfile")
    restore_runbook = _read("docs/runbooks/backup-restore.md")
    template = _read(".env.example")

    assert "start-dev" not in compose
    assert 'command: ["start", "--optimized", "--import-realm"]' in compose
    assert "KC_DB: postgres" in compose
    assert "KEYCLOAK_DB_NAME:-${POSTGRES_DB" in compose
    assert "KC_DB_SCHEMA: keycloak" in compose
    assert "KC_DB_USERNAME: easysynq_keycloak" in compose
    assert compose.count("KEYCLOAK_DB_PASSWORD:?set a distinct KEYCLOAK_DB_PASSWORD") == 2
    assert "KEYCLOAK_DB_PASSWORD:-${POSTGRES_PASSWORD" not in compose
    assert "keycloakimport:/opt/keycloak/data/import:ro" in compose
    assert "condition: service_completed_successfully" in compose

    assert "RUN /opt/keycloak/bin/kc.sh build" in image
    assert "CREATE ROLE easysynq_keycloak LOGIN" in init
    assert "CREATE SCHEMA keycloak AUTHORIZATION easysynq_keycloak" in init
    assert "ALTER %s %I.%I OWNER TO easysynq_keycloak" in init
    assert "ALL TABLES IN SCHEMA keycloak" in init
    assert "KEYCLOAK_DB_NAME=" in template
    assert "`KEYCLOAK_DB_NAME`" in restore_runbook
    assert "restored `keycloak` schema objects" in restore_runbook

    # A transition must export users/credential hashes before the legacy container is replaced.
    assert "docker stop --time 60" in migration
    assert "--users realm_file" in migration
    assert ".legacy-h2-export-complete" in migration
    assert "com.docker.compose.volume=keycloakimport" in migration
    assert 'IMPORT_VOLUME="${IMPORT_VOLUME:-${PROJECT}_keycloakimport}"' in migration
    assert (
        'grep -q "\\"users\\"[[:space:]]*:" /migration-export/easysynq-realm.json &&' in migration
    )
    assert "restarting the untouched legacy container" in migration
    assert "name: easysynq-keycloak-import" not in compose
    assert 'legs.realm_export = "present"' in restore_runbook
    assert "<compose-project>_keycloakimport" in restore_runbook
    assert "before the first Keycloak start" in restore_runbook
    assert justfile.count("ensure-keycloak-db-password.sh --env-file .env") == 3
    assert justfile.index("ensure-keycloak-db-password.sh") < justfile.index(
        "migrate-keycloak-h2.sh"
    )


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


def test_installer_rejects_invalid_dns_labels_before_deployment() -> None:
    for hostname in (
        "qms.-corp.example",
        "qms-.corp.example",
        ".qms.corp.example",
        "qms.corp.example.",
        f"{'a' * 64}.example",
    ):
        result = subprocess.run(  # noqa: S603 - fixed repository installer
            [
                "/bin/bash",
                str(ROOT / "scripts/install.sh"),
                "s",
                "--host",
                hostname,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert "must be a valid DNS name" in result.stderr


def test_production_browser_origin_validator_rejects_both_tuple_mismatches(
    tmp_path: Path,
) -> None:
    checker = ROOT / "scripts/validate-browser-origins.sh"
    env_file = tmp_path / ".env"
    values = {
        "SITE_ADDRESS": "https://qms.example.com",
        "PUBLIC_BASE_URL": "https://qms.example.com",
        "APP_BASE_URL": "https://qms.example.com",
        "KEYCLOAK_HOSTNAME": "https://qms.example.com",
        "MINIO_SITE_ADDRESS": "https://qms.example.com:9443",
        "S3_PUBLIC_ENDPOINT": "https://qms.example.com:9443",
    }
    browser_keys = set(values)

    def check(overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        rendered = {**values, **overrides}
        env_file.write_text("".join(f"{key}={value}\n" for key, value in rendered.items()))
        clean_env = {key: value for key, value in os.environ.items() if key not in browser_keys}
        return subprocess.run(  # noqa: S603 - fixed checker against an isolated env file
            ["/bin/bash", str(checker), "--env-file", str(env_file)],
            cwd=ROOT,
            env=clean_env,
            capture_output=True,
            text=True,
            check=False,
        )

    consistent = check({})
    assert consistent.returncode == 0, consistent.stderr

    app_mismatch = check({"APP_BASE_URL": "https://stale.example.com"})
    assert app_mismatch.returncode != 0
    assert "APP_BASE_URL must exactly equal SITE_ADDRESS" in app_mismatch.stderr

    minio_mismatch = check({"S3_PUBLIC_ENDPOINT": "https://stale.example.com:9443"})
    assert minio_mismatch.returncode != 0
    assert "S3_PUBLIC_ENDPOINT must exactly equal MINIO_SITE_ADDRESS" in minio_mismatch.stderr


def test_keycloak_db_password_backfill_is_distinct_persistent_and_idempotent(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=owner-secret\nKEYCLOAK_DB_PASSWORD=owner-secret\n")
    helper = ROOT / "scripts/ensure-keycloak-db-password.sh"

    env_file.chmod(0o440)
    read_only = subprocess.run(  # noqa: S603 - fixed helper against an isolated env file
        ["/bin/bash", str(helper), "--env-file", str(env_file)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert read_only.returncode != 0
    assert "rerun this first Batch 13 command with sudo" in read_only.stderr
    assert "KEYCLOAK_DB_PASSWORD=owner-secret" in env_file.read_text()

    env_file.chmod(0o640)
    first = subprocess.run(  # noqa: S603 - fixed helper against an isolated env file
        ["/bin/bash", str(helper), "--env-file", str(env_file)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    first_value = next(
        line.split("=", 1)[1]
        for line in env_file.read_text().splitlines()
        if line.startswith("KEYCLOAK_DB_PASSWORD=")
    )
    assert first_value
    assert first_value != "owner-secret"
    assert env_file.stat().st_mode & 0o777 == 0o640

    second = subprocess.run(  # noqa: S603 - fixed helper against an isolated env file
        ["/bin/bash", str(helper), "--env-file", str(env_file)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert f"KEYCLOAK_DB_PASSWORD={first_value}" in env_file.read_text()


def test_dev_keycloak_hostname_tracks_nondefault_http_port(tmp_path: Path) -> None:
    browser_keys = {
        "HTTP_PORT",
        "KEYCLOAK_HOSTNAME",
        "KEYCLOAK_DB_PASSWORD",
    }

    compose_dir = tmp_path / "infra" / "compose"
    compose_dir.mkdir(parents=True)
    for name in ("compose.yml", "compose.s.yml", "compose.dev.yml"):
        shutil.copyfile(ROOT / "infra" / "compose" / name, compose_dir / name)
    shutil.copyfile(ROOT / ".env.example", tmp_path / ".env")
    shutil.copyfile(ROOT / ".env.example", tmp_path / ".env.example")

    def render(http_port: str | None) -> str:
        docker = shutil.which("docker")
        assert docker is not None
        env = {key: value for key, value in os.environ.items() if key not in browser_keys}
        env["KEYCLOAK_DB_PASSWORD"] = "keycloak-secret"
        if http_port is not None:
            env["HTTP_PORT"] = http_port
        result = subprocess.run(  # noqa: S603 - resolved Docker binary; no daemon/network
            [
                docker,
                "compose",
                "--env-file",
                str(tmp_path / ".env.example"),
                "-f",
                str(compose_dir / "compose.yml"),
                "-f",
                str(compose_dir / "compose.s.yml"),
                "-f",
                str(compose_dir / "compose.dev.yml"),
                "config",
                "--format",
                "json",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        rendered = json.loads(result.stdout)
        return rendered["services"]["keycloak"]["environment"]["KC_HOSTNAME"]

    assert render(None) == "http://localhost"
    assert render("8088") == "http://localhost:8088"


def test_dev_overlay_relabels_only_repository_bind_mounts_for_selinux() -> None:
    docker = shutil.which("docker")
    assert docker is not None

    expected_dev_binds = {
        "minio-init": {"/init": "./minio:/init:ro,z"},
        "keycloak-init": {
            "/init/keycloak-init.sh": "./keycloak/keycloak-init.sh:/init/keycloak-init.sh:ro,z",
            "/seed/easysynq-realm.json": (
                "./keycloak/realm-export.json:/seed/easysynq-realm.json:ro,z"
            ),
        },
        "worker": {
            "/srv/import/source": (
                "${IMPORT_SOURCE_PATH:-../../.import-source}:/srv/import/source:ro,z"
            )
        },
        "proxy": {"/etc/caddy/Caddyfile": "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro,z"},
    }
    dev = yaml.safe_load(_read("infra/compose/compose.dev.yml"))
    assert {
        service: {entry.rsplit(":", 2)[1]: entry for entry in config["volumes"]}
        for service, config in dev["services"].items()
        if "volumes" in config
    } == expected_dev_binds

    clean_env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "KEYCLOAK_DB_PASSWORD",
            "SITE_ADDRESS",
            "MINIO_SITE_ADDRESS",
            "S3_PUBLIC_ENDPOINT",
            "PUBLIC_BASE_URL",
            "APP_BASE_URL",
            "KEYCLOAK_HOSTNAME",
        }
    }
    clean_env["KEYCLOAK_DB_PASSWORD"] = "proof-only-keycloak-secret"

    def render(*overlays: str, production: bool = False) -> dict[str, object]:
        env = dict(clean_env)
        if production:
            env.update(
                {
                    "SITE_ADDRESS": "https://qms.example.test",
                    "MINIO_SITE_ADDRESS": "https://qms.example.test:9443",
                    "S3_PUBLIC_ENDPOINT": "https://qms.example.test:9443",
                    "PUBLIC_BASE_URL": "https://qms.example.test",
                    "APP_BASE_URL": "https://qms.example.test",
                    "KEYCLOAK_HOSTNAME": "https://qms.example.test",
                }
            )
        command = [
            docker,
            "compose",
            "--env-file",
            str(ROOT / ".env.example"),
            "-f",
            str(ROOT / "infra/compose/compose.yml"),
        ]
        for overlay in overlays:
            command.extend(["-f", str(ROOT / f"infra/compose/{overlay}")])
        command.extend(["config", "--no-env-resolution", "--format", "json"])
        result = subprocess.run(  # noqa: S603 - resolved Docker binary; config rendering only
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    named_targets = {
        "keycloak-init": {"/import": "keycloakimport"},
        "worker": {
            "/var/lib/easysynq/qms-mirror": "mirror",
            "/run/secrets": "secrets",
            "/var/lib/easysynq/backups": "backup",
        },
        "proxy": {"/data": "caddydata", "/config": "caddyconfig"},
    }
    for sizing in ("compose.s.yml", "compose.m.yml"):
        rendered = render(sizing, "compose.dev.yml")
        services = rendered["services"]
        for service, targets in expected_dev_binds.items():
            volumes = {volume["target"]: volume for volume in services[service]["volumes"]}
            for target in targets:
                assert volumes[target]["type"] == "bind"
                assert volumes[target]["read_only"] is True
                assert volumes[target]["bind"]["selinux"] == "z"
        for service, targets in named_targets.items():
            volumes = {volume["target"]: volume for volume in services[service]["volumes"]}
            assert {
                target: volumes[target]["source"]
                for target in targets
                if volumes[target]["type"] == "volume"
            } == targets

    production_source = _read("infra/compose/compose.production.yml")
    assert ":z" not in production_source
    assert ",z" not in production_source
    for sizing in ("compose.s.yml", "compose.m.yml"):
        production = render(sizing, "compose.production.yml", production=True)
        for service in production["services"].values():
            for volume in service.get("volumes", []):
                if volume["type"] == "bind":
                    assert "selinux" not in volume["bind"]


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
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=owner-secret\nSITE_ADDRESS=https://easysynq.local\n"
    )
    browser_keys = {
        "SITE_ADDRESS",
        "MINIO_SITE_ADDRESS",
        "S3_PUBLIC_ENDPOINT",
        "PUBLIC_BASE_URL",
        "APP_BASE_URL",
        "KEYCLOAK_HOSTNAME",
    }
    env = {
        **{key: value for key, value in os.environ.items() if key not in browser_keys},
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "EASYSYNQ_APP_DIR": str(tmp_path),
        "EASYSYNQ_MIGRATION_SCRIPT": str(fake_migration),
        "EASYSYNQ_COMPOSE_VERSION_SCRIPT": str(ROOT / "scripts/require-compose-version.sh"),
        "EASYSYNQ_BROWSER_ORIGIN_SCRIPT": str(ROOT / "scripts/validate-browser-origins.sh"),
        "EASYSYNQ_KEYCLOAK_DB_PASSWORD_SCRIPT": str(
            ROOT / "scripts/ensure-keycloak-db-password.sh"
        ),
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
    env_after_backfill = (tmp_path / ".env").read_text()
    assert "KEYCLOAK_DB_PASSWORD=" in env_after_backfill
    assert "KEYCLOAK_DB_PASSWORD=owner-secret" not in env_after_backfill

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
