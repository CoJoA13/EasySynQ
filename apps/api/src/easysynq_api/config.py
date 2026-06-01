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

    # database (async runtime + sync for Alembic)
    database_url: str = "postgresql+psycopg://easysynq:easysynq@localhost:5432/easysynq"
    database_url_sync: str | None = None

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
    s3_object_lock_mode: str = "GOVERNANCE"
    s3_presign_expiry_seconds: int = 900  # presigned PUT/GET validity (doc 18 §5.2)

    # auth (Keycloak)
    oidc_issuer: str = ""
    oidc_audience: str = "easysynq-api"
    oidc_jwks_url: str = ""
    oidc_client_id: str = "easysynq-web"

    # renderer + mirror
    gotenberg_url: str = "http://localhost:3000"
    mirror_path: str = "/var/lib/easysynq/qms-mirror"

    @property
    def sync_dsn(self) -> str:
        """DSN Alembic uses (sync driver). Falls back to the async URL's driver name."""
        return self.database_url_sync or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
