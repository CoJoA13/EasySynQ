"""S0 unit tests for the health surface. No external services required:
/readyz exercises the probes, which fail gracefully (no PG/Redis) and report shape.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from easysynq_api import readiness
from easysynq_api.api import health as health_api
from easysynq_api.config import Settings


@pytest.mark.unit
async def test_healthz_ok(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "api"
    assert "version" in body
    # request-id middleware echoes a correlation id on every response
    assert resp.headers.get("X-Request-Id")


@pytest.mark.unit
async def test_readyz_shape(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert isinstance(body["ready"], bool)
    names = {d["name"] for d in body["dependencies"]}
    assert names == {"postgres", "redis", "minio", "keycloak", "alembic"}
    # OpenSearch is deliberately absent in the MVP (R34)
    assert "opensearch" not in names


@pytest.mark.unit
async def test_readyz_does_not_expose_dependency_diagnostics(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _checks() -> list[dict[str, object]]:
        return [
            {
                "name": "postgres",
                "ready": False,
                "detail": "connection to db.internal as easysynq_app failed",
            }
        ]

    monkeypatch.setattr(health_api, "check_all", _checks)

    resp = await client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json() == {
        "ready": False,
        "dependencies": [{"name": "postgres", "ready": False}],
    }


class _VersioningClient:
    def __init__(self, statuses: dict[str, str]) -> None:
        self._statuses = statuses

    def list_buckets(self) -> dict[str, list[object]]:
        return {"Buckets": []}

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:
        return {"Status": self._statuses[Bucket]}


async def _run_inline(function: object) -> object:
    return function()  # type: ignore[operator]


@pytest.mark.unit
async def test_minio_readiness_fails_when_a_staging_bucket_is_not_versioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(s3_access_key="test", s3_secret_key="test")
    client = _VersioningClient({"staging": "Enabled", "import-staging": "Suspended"})
    assert hasattr(readiness, "_minio_client"), "readiness must expose the MinIO client seam"
    monkeypatch.setattr(readiness, "_minio_client", lambda _settings: client)
    monkeypatch.setattr(readiness.asyncio, "to_thread", _run_inline)

    status = await readiness._check_minio(settings)

    assert status.ready is False


@pytest.mark.unit
async def test_minio_readiness_accepts_both_versioned_staging_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(s3_access_key="test", s3_secret_key="test")
    client = _VersioningClient({"staging": "Enabled", "import-staging": "Enabled"})
    assert hasattr(readiness, "_minio_client"), "readiness must expose the MinIO client seam"
    monkeypatch.setattr(readiness, "_minio_client", lambda _settings: client)
    monkeypatch.setattr(readiness.asyncio, "to_thread", _run_inline)

    status = await readiness._check_minio(settings)

    assert status.ready is True
