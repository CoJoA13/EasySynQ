"""Integration fixtures: a real PostgreSQL (testcontainers), migrated to head, with
the app wired to it and the JWKS replaced by a throwaway test key.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jwt.algorithms import RSAAlgorithm
from testcontainers.postgres import PostgresContainer

ISSUER = "https://kc.test/realms/easysynq"
AUDIENCE = "easysynq-api"
KID = "test-key"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_JWK = json.loads(RSAAlgorithm.to_jwk(_KEY.public_key()))
_JWK["kid"] = KID
_JWK["alg"] = "RS256"
JWKS: dict[str, object] = {"keys": [_JWK]}


def _mint(sub: str, **overrides: object) -> str:
    now = datetime.datetime.now(tz=datetime.UTC)
    claims: dict[str, object] = {
        "sub": sub,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(minutes=5)).timestamp()),
        "preferred_username": "tester",
        "email": "tester@example.com",
        "name": "Test User",
    }
    claims.update(overrides)
    return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def token_factory() -> Callable[..., str]:
    return _mint


@pytest.fixture(scope="session")
def _pg() -> Iterator[str]:
    with PostgresContainer(
        "postgres:16", username="test", password="test", dbname="test", driver="psycopg"
    ) as pg:
        yield pg.get_connection_url()


def _swap_role(dsn: str, user: str, password: str) -> str:
    """Re-point a DSN at a different role (same host/db). Used to connect as the non-owner
    ``easysynq_app`` / ``easysynq_linker`` roles the 0010 migration creates, so AC#6a's DB-grant
    rejection is exercised by the real grant (not bypassed by the superuser)."""
    parts = urlsplit(dsn)
    netloc = f"{user}:{password}@{parts.hostname}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@pytest.fixture
def dsns(_pg: str) -> dict[str, str]:
    """The owner / app / linker DSNs. Depend on ``app_under_test`` first so the roles exist (they
    are created by the migration). Passwords match the 0010 migration's dev defaults."""
    return {
        "owner": _pg,
        "app": _swap_role(_pg, "easysynq_app", "easysynq_app"),
        "linker": _swap_role(_pg, "easysynq_linker", "easysynq_linker"),
    }


@pytest.fixture(scope="session")
def _minio() -> Iterator[dict[str, str]]:
    """A MinIO container with the ``documents`` bucket created object-lock-enabled + a
    GOVERNANCE default retention (mirrors infra/compose/minio-init.sh), so every PUT auto-WORMs."""
    import boto3
    from testcontainers.minio import MinioContainer

    with MinioContainer() as mc:
        cfg = mc.get_config()
        endpoint = f"http://{cfg['endpoint']}"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret_key"],
            region_name="us-east-1",
        )
        client.create_bucket(Bucket="documents", ObjectLockEnabledForBucket=True)
        client.put_object_lock_configuration(
            Bucket="documents",
            ObjectLockConfiguration={
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 30}},
            },
        )
        client.create_bucket(Bucket="staging")  # plain bucket for presigned uploads
        client.create_bucket(Bucket="renditions")  # S7b derived watermarked PDFs (non-WORM)
        # S8b2 restore-test drill: a plain (NON-WORM) scratch bucket the drill copies blobs into +
        # tears the per-drill prefix down (R37 — never restore into the object-locked documents).
        client.create_bucket(Bucket="restore-scratch")
        # S6 off-host audit-checkpoint anchor bucket (object-lock, R13).
        client.create_bucket(Bucket="audit-checkpoints", ObjectLockEnabledForBucket=True)
        yield {
            "endpoint": endpoint,
            "access_key": cfg["access_key"],
            "secret_key": cfg["secret_key"],
        }


@pytest.fixture(scope="session")
def _redis() -> Iterator[str]:
    from testcontainers.redis import RedisContainer

    with RedisContainer() as rc:
        host = rc.get_container_host_ip()
        port = rc.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
async def app_under_test(
    _pg: str,
    _minio: dict[str, str],
    _redis: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Any]:
    """The migrated FastAPI app wired to the testcontainer DB/MinIO/Redis, with JWKS stubbed.

    Migrations run as the OWNER (``DATABASE_URL_SYNC`` = the container superuser), which is what
    creates the ``easysynq_app`` / ``easysynq_linker`` roles. The app itself then connects as the
    NON-OWNER ``easysynq_app`` (``DATABASE_URL``), so the append-only DB grants are real — AC#6a's
    UPDATE/DELETE rejection is exercised, not bypassed by a superuser."""
    app_dsn = _swap_role(_pg, "easysynq_app", "easysynq_app")
    linker_dsn = _swap_role(_pg, "easysynq_linker", "easysynq_linker")
    monkeypatch.setenv("DATABASE_URL", app_dsn)  # app/worker = non-owner role (AC#6a)
    monkeypatch.setenv("DATABASE_URL_SYNC", _pg)  # alembic = owner (creates roles + grants)
    monkeypatch.setenv("AUDIT_LINKER_DATABASE_URL", linker_dsn)
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("S3_ENDPOINT", _minio["endpoint"])
    monkeypatch.setenv("S3_ACCESS_KEY", _minio["access_key"])
    monkeypatch.setenv("S3_SECRET_KEY", _minio["secret_key"])
    monkeypatch.setenv("S3_BUCKET_DOCUMENTS", "documents")
    monkeypatch.setenv("S3_BUCKET_AUDIT_CHECKPOINTS", "audit-checkpoints")
    monkeypatch.setenv("AUDIT_CHECKPOINT_SIGNING_KEY_PATH", str(tmp_path / "audit_ckpt.pem"))
    monkeypatch.setenv("VERIFY_TOKEN_SIGNING_KEY_PATH", str(tmp_path / "verify.pem"))  # S7c
    # S11: a real backup-encryption key so the durable archive exercises the AES-256-GCM path (the
    # restore-into-scratch drill stays plaintext-internal regardless). Keycloak admin stays unset →
    # the realm-export leg degrades to "absent" in CI (no Keycloak), as designed.
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "ci-test-backup-encryption-key-0123456789")
    monkeypatch.setenv("REDIS_URL", _redis)

    from alembic import command
    from alembic.config import Config

    from easysynq_api.auth.jwks import JWKSCache, get_jwks_cache
    from easysynq_api.config import get_settings
    from easysynq_api.db import session as db_session
    from easysynq_api.main import create_app
    from easysynq_api.readiness import MIGRATIONS_DIR

    get_settings.cache_clear()
    await db_session.dispose_engine()

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    command.upgrade(cfg, "head")

    # The S8a latch (423 until OPERATIONAL) would otherwise lock every non-setup test out of the
    # shared, session-scoped DB. Default it OPEN here so existing tests behave as before; the setup
    # tests reset setup_state to UNINITIALIZED themselves (test_setup.py).
    import sqlalchemy as _sa

    _owner = _sa.create_engine(_pg)
    with _owner.begin() as conn:
        conn.execute(
            _sa.text("UPDATE system_config SET setup_state='OPERATIONAL', finalized_at=now()")
        )
    _owner.dispose()

    app = create_app()
    app.dependency_overrides[get_jwks_cache] = lambda: JWKSCache("", static_jwks=JWKS)

    yield app

    await db_session.dispose_engine()
    get_settings.cache_clear()


@pytest.fixture
async def app_client(app_under_test: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_under_test)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
