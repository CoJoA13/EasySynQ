"""S0 unit tests for the health surface. No external services required:
/readyz exercises the probes, which fail gracefully (no PG/Redis) and report shape.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from easysynq_api.api import health as health_api


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
