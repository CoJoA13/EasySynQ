"""Application settings (12-factor, D1). Read from the environment only."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # core
    easysynq_env: str = "production"
    easysynq_profile: str = "s"
    easysynq_org_timezone: str = "UTC"
    log_level: str = "INFO"
    version: str = "0.1.0"

    # database (async runtime + sync for Alembic).
    # In a real deploy the app/worker/beat connect as the NON-OWNER ``easysynq_app`` role
    # (INSERT/SELECT-only on audit_event + signature_event, so the append-only trail is
    # structurally enforced — AC#6a); ``database_url_sync`` (Alembic) runs as the OWNER so the
    # 0010 migration can CREATE ROLE + GRANT/REVOKE (doc 18 §136/§150).
    database_url: str = "postgresql+psycopg://easysynq:easysynq@localhost:5432/easysynq"
    database_url_sync: str | None = None
    # The chain-linker (R12) connects as a dedicated role with column-scoped UPDATE on
    # audit_event(prev_hash,row_hash,chained_at) — the app role has no UPDATE there.
    audit_linker_database_url: str = (
        "postgresql+psycopg://easysynq_linker:easysynq_linker@localhost:5432/easysynq"
    )
    # Passwords the 0010 migration uses to CREATE the app + linker roles (owner runs the
    # migration). Dev defaults; prod operators set APP_DB_PASSWORD / LINKER_DB_PASSWORD before
    # ``migrate``. CI + testcontainers use the defaults so the AC#6a role-grant proof works.
    app_db_password: str = "easysynq_app"  # noqa: S105 — dev default, overridden in prod
    linker_db_password: str = "easysynq_linker"  # noqa: S105 — dev default, overridden in prod

    # redis
    redis_url: str = "redis://localhost:6379/0"

    # object store (S3 API via boto3)
    s3_endpoint: str = "http://localhost:9000"
    # Browser-reachable MinIO origin for presigned URLs (behind Caddy in real deploys); the
    # internal s3_endpoint is not reachable from a client. Empty → use s3_endpoint (dev/CI).
    s3_public_endpoint: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_documents: str = "documents"
    s3_bucket_staging: str = "staging"
    s3_bucket_renditions: str = "renditions"  # derived watermarked PDFs (non-WORM; S7b)
    # S8b2 restore-drill: a plain (NON-WORM) scratch bucket the drill copies blobs INTO and tears
    # down (R37 — object-lock can't be retro-added, so never restore into the locked vault bucket).
    s3_bucket_restore_scratch: str = "restore-scratch"
    s3_object_lock_mode: str = "GOVERNANCE"
    s3_presign_expiry_seconds: int = 900  # presigned PUT/GET validity (doc 18 §5.2)

    # S6 off-host audit-checkpoint sink (worm_bucket kind, R13/D-8): a SEPARATE object-lock
    # bucket reached with DISTINCT, write-only credentials held apart from the vault root, so
    # the same operator cannot control both the live chain and its off-host anchor. Empty creds
    # fall back to the vault s3 creds (dev only — NOT honest custody separation).
    s3_bucket_audit_checkpoints: str = "audit-checkpoints"
    audit_sink_access_key: str = ""
    audit_sink_secret_key: str = ""
    # Ed25519 private key (PEM) that signs checkpoints — a beat-only secret (dev-grade; the
    # Part-11 crypto path stays reserved). Generated + persisted here on first use if absent.
    audit_checkpoint_signing_key_path: str = "/run/secrets/audit_ckpt_key"
    # Configurable-verbosity knob (doc 12 §4.1): also persist routine authz ALLOW decisions.
    # Off in v1 — only denies + state-changes persist (avoids an audit row per read request).
    audit_persist_allows: bool = False

    # auth (Keycloak)
    oidc_issuer: str = ""
    oidc_audience: str = "easysynq-api"
    oidc_jwks_url: str = ""
    oidc_client_id: str = "easysynq-web"

    # renderer + mirror
    gotenberg_url: str = "http://localhost:3000"
    mirror_path: str = "/var/lib/easysynq/qms-mirror"

    # S8b2 backup: the default filesystem destination for durable archives + the restore-test drill
    # (the per-org backup_policy.destination overrides). A mounted volume / NFS path in MVP
    # (S3-destination is S11/v1.x). The drill + pg_dump run as the OWNER role (database_url_sync).
    backup_path: str = "/var/lib/easysynq/backups"
    # S11 archive envelope encryption (doc 12 §6.2): a dedicated AES-256-GCM key, SEPARATE custody
    # from the app KEK — install.sh generates it into the 0600 .env (a stolen archive is useless
    # without it). Unset/placeholder → the durable backup degrades to PLAINTEXT + a loud warning
    # (the drill never encrypts — it is plaintext-internal). Never stored in the archive or VCS.
    backup_encryption_key: str = "CHANGE_ME"
    # S11 Keycloak realm export: the worker reaches Keycloak's Admin REST API on the INTERNAL
    # network (the worker runs the api image — no kcadm.sh). Empty admin creds → the realm leg
    # degrades to "absent" (a Keycloak outage must never fail the nightly backup). The realm name
    # is parsed from oidc_issuer (…/realms/<name>).
    keycloak_admin_url: str = "http://keycloak:8080"
    keycloak_admin_user: str = ""
    keycloak_admin_password: str = ""

    # S7c verify token: a dedicated Ed25519 key (separate custody from the audit-checkpoint key) +
    # the browser-reachable origin the footer QR/verify-link points at.
    verify_token_signing_key_path: str = "/run/secrets/verify_token_key"  # noqa: S105 — a path
    public_base_url: str = "http://localhost"

    @property
    def sync_dsn(self) -> str:
        """DSN Alembic uses (sync driver). Falls back to the async URL's driver name."""
        return self.database_url_sync or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
