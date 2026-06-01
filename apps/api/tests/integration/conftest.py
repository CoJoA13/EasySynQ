"""Integration fixtures: a real PostgreSQL (testcontainers), migrated to head, with
the app wired to it and the JWKS replaced by a throwaway test key.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

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


@pytest.fixture
async def app_under_test(_pg: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """The migrated FastAPI app wired to the testcontainer DB, with JWKS stubbed. Exposed so
    a test can install dependency overrides (e.g. a capturing audit sink) before issuing
    requests; most tests use ``app_client`` instead."""
    monkeypatch.setenv("DATABASE_URL", _pg)
    monkeypatch.setenv("DATABASE_URL_SYNC", _pg)
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", AUDIENCE)

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
