"""Dependency readiness probes for ``/readyz``.

Checks PostgreSQL, Redis, MinIO, Keycloak, and the Alembic migration head.
OpenSearch is intentionally NOT probed in the MVP (omitted per R34). Probes that
are unconfigured in a dev environment report ``ready=True`` with a note rather
than failing the whole skeleton; in the Compose stack every dependency is wired.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from sqlalchemy import text

from .config import Settings, get_settings
from .db.session import get_engine


def _find_migrations_dir() -> Path:
    """Locate the Alembic ``migrations/`` dir robustly across the dev layout
    (repo-root/migrations) and the container layout (/migrations)."""
    env = os.getenv("EASYSYNQ_MIGRATIONS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "migrations"
        if (candidate / "env.py").exists():
            return candidate
    return here.parents[-1] / "migrations"


MIGRATIONS_DIR = _find_migrations_dir()


@dataclass(slots=True)
class DependencyStatus:
    name: str
    ready: bool
    detail: str | None = None


async def _check_postgres() -> DependencyStatus:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return DependencyStatus("postgres", True)
    except Exception as exc:  # noqa: BLE001 — readiness reports, never raises
        return DependencyStatus("postgres", False, str(exc))


async def _check_redis(settings: Settings) -> DependencyStatus:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
        return DependencyStatus("redis", True)
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus("redis", False, str(exc))


async def _check_minio(settings: Settings) -> DependencyStatus:
    if not (settings.s3_access_key and settings.s3_secret_key):
        return DependencyStatus("minio", True, "unconfigured (S0 dev)")

    def _probe() -> None:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        client.list_buckets()

    try:
        await asyncio.to_thread(_probe)
        return DependencyStatus("minio", True)
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus("minio", False, str(exc))


async def _check_keycloak(settings: Settings) -> DependencyStatus:
    url = settings.oidc_jwks_url
    if not url and settings.oidc_issuer:
        url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    if not url:
        return DependencyStatus("keycloak", True, "unconfigured (S0 dev)")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        return DependencyStatus("keycloak", True)
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus("keycloak", False, str(exc))


async def _check_alembic() -> DependencyStatus:
    def _heads() -> str | None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        return ScriptDirectory.from_config(cfg).get_current_head()

    try:
        head = await asyncio.to_thread(_heads)
        async with get_engine().connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current = result.scalar_one_or_none()
        if current is None:
            return DependencyStatus("alembic", False, "no migration applied")
        if current != head:
            return DependencyStatus("alembic", False, f"db@{current} != head@{head}")
        return DependencyStatus("alembic", True, f"head@{head}")
    except Exception as exc:  # noqa: BLE001
        return DependencyStatus("alembic", False, str(exc))


async def check_all() -> list[dict[str, object]]:
    settings = get_settings()
    results = await asyncio.gather(
        _check_postgres(),
        _check_redis(settings),
        _check_minio(settings),
        _check_keycloak(settings),
        _check_alembic(),
    )
    return [asdict(r) for r in results]
