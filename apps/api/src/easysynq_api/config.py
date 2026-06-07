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
    # S-rec-1: the records WORM bucket (object-locked, GOVERNANCE) — captured record evidence
    # promotes here, kept apart from the documents vault (doc 06; provisioned in minio-init.sh).
    s3_bucket_records: str = "records"
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
    # Optional internal OIDC discovery URL for the G-D setup probe when the public (browser-
    # facing) issuer is not reachable from the API host (a reverse-proxied localhost/hostname
    # issuer). Empty -> derive discovery from oidc_issuer (default; when the issuer is reachable).
    oidc_discovery_url: str = ""
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

    # S-pack-2 evidence-pack external delivery (doc 06 §7.4, UJ-7): the time-box on an Ed25519
    # share-link. A request may set its own expiry; the server clamps to [1, max] days and uses the
    # default when unset. Short windows + revoke are the controls (the link rides a URL).
    pack_share_default_ttl_days: int = 14
    pack_share_max_ttl_days: int = 90

    # S-ing-1 ingestion (doc 09, UJ-2): the read-only mounted source root the worker walks — this is
    # the NG3 confinement boundary (a run's ``source_root`` must resolve within it). The non-WORM
    # ``import-staging`` bucket is where included bytes content-address in one pass (abandoned
    # imports
    # never touch the immutable vault, doc 09 §2/§15). Plus the scan tuning knobs.
    import_source_root: str = "/srv/import/source"
    s3_bucket_import_staging: str = "import-staging"
    import_oversize_bytes: int = 500 * 1024 * 1024  # >500 MB → quarantined, never read/hashed
    import_walk_batch_size: int = 500  # files per walk batch / per-batch commit checkpoint
    import_lock_ttl_seconds: int = 1800  # source-root lock TTL; heartbeated per batch
    import_scan_stall_seconds: int = 3600  # the stalled-scan reaper cutoff on scan_started_at

    # S-ing-2 ingestion extract + classify (doc 09 §5-6). The Apache Tika ``-full`` sidecar bundles
    # the extractors + Tesseract OCR (HTTP, fully local, no telemetry; the Gotenberg precedent). The
    # source-root lock is held continuously scan->extract->classify (freed at Classified), so the
    # reaper's primary stall signal is lock-liveness; ``import_run_stall_seconds`` is the generous
    # absolute backstop on ``scan_started_at`` (the pipeline anchor) for a wedged-but-locked run.
    tika_url: str = "http://localhost:9998"
    import_ocr_language: str = "eng"  # Tesseract lang (org-configurable; en in v1, §5.2)
    import_ocr_char_per_page_threshold: int = 50  # native chars/page below which a PDF is OCR'd
    import_extract_batch_size: int = 50  # extract is OCR-heavy; a conservative per-batch checkpoint
    import_classify_batch_size: int = 200  # classify is pure CPU; cheaper, larger batch
    import_max_extract_text_bytes: int = (
        4 * 1024 * 1024
    )  # full_text inline cap (text_truncated flag)
    import_run_stall_seconds: int = 6 * 3600  # reaper absolute backstop on an in-progress run
    # S-ing-3 dedup: the §7.1 near-dup Jaccard threshold (in-process MinHash). OpenSearch is the
    # documented v1 drop-in for the DedupDetector/Indexer seams (R34) — absent in MVP/v1, no
    # container; this URL is the reserved drop-in target only (nothing connects to it yet).
    import_near_dup_threshold: float = 0.85
    opensearch_url: str = ""  # reserved (R34 drop-in); empty = not configured
    # S-ing-4 review: the §9.3 pre-commit gate "unresolved ambiguous items above a configurable
    # count" — an ambiguous keep-item the reviewer has not decided on (accept/correct/exclude/defer)
    # is unresolved; blocking when the count EXCEEDS this. Default 0 = resolve-or-defer every
    # ambiguous before commit (configurable per install via env).
    import_review_ambiguous_threshold: int = 0
    import_bulk_decision_max: int = 5000  # max files a single bulk-decision call may target

    @property
    def sync_dsn(self) -> str:
        """DSN Alembic uses (sync driver). Falls back to the async URL's driver name."""
        return self.database_url_sync or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
