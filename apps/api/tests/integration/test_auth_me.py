"""S1: /me resolves a validated Keycloak token to an app_user (JIT-provisioned)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_me_jit_provisions_and_reuses_app_user(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    sub = f"kc-{uuid.uuid4()}"
    headers = {"Authorization": f"Bearer {token_factory(sub)}"}

    first = await app_client.get("/api/v1/me", headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["keycloak_subject"] == sub
    assert body["status"] == "ACTIVE"
    assert body["display_name"] == "Test User"
    assert body["is_guest"] is False

    # a second call with a fresh token for the same subject reuses the same row
    second = await app_client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {token_factory(sub)}"}
    )
    assert second.status_code == 200
    assert second.json()["id"] == body["id"]


async def test_me_without_token_is_401(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/v1/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthenticated"


async def test_auth_config_is_public(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    assert resp.json()["client_id"] == "easysynq-web"
