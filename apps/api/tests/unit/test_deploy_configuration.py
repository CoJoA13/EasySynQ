"""Batch 13 deployment invariants.

These source-level checks complement ``docker compose config --quiet`` in CI. They pin the security
boundaries that are easy to accidentally erase while editing an overlay: production never publishes
plaintext MinIO, every browser URL is supplied together, and Keycloak's live store is PostgreSQL.
"""

from __future__ import annotations

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


def test_keycloak_runs_optimized_on_durable_postgres_schema() -> None:
    compose = _read("infra/compose/compose.yml")
    image = _read("infra/compose/keycloak/Dockerfile")
    init = _read("infra/compose/keycloak/keycloak-init.sh")
    migration = _read("scripts/migrate-keycloak-h2.sh")

    assert "start-dev" not in compose
    assert 'command: ["start", "--optimized", "--import-realm"]' in compose
    assert "KC_DB: postgres" in compose
    assert "KC_DB_SCHEMA: keycloak" in compose
    assert "KC_DB_USERNAME: easysynq_keycloak" in compose
    assert "KEYCLOAK_DB_PASSWORD:-${POSTGRES_PASSWORD" in compose
    assert "keycloakimport:/opt/keycloak/data/import:ro" in compose
    assert "condition: service_completed_successfully" in compose

    assert "RUN /opt/keycloak/bin/kc.sh build" in image
    assert "CREATE ROLE easysynq_keycloak LOGIN" in init
    assert "CREATE SCHEMA keycloak AUTHORIZATION easysynq_keycloak" in init

    # A transition must export users/credential hashes before the legacy container is replaced.
    assert "docker stop --time 60" in migration
    assert "--users realm_file" in migration
    assert ".legacy-h2-export-complete" in migration
    assert "com.docker.compose.volume=keycloakimport" in migration
    assert "restarting the untouched legacy container" in migration
