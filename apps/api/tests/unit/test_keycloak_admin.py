"""Deployment-time Keycloak Admin API guards (mock transport; no live identity service)."""

from __future__ import annotations

import json

import httpx

from easysynq_api.services.keycloak_admin import ensure_client_redirect_uri


def test_redirect_append_preserves_complete_client_and_is_idempotent() -> None:
    representation: dict[str, object] = {
        "id": "client-uuid",
        "clientId": "easysynq-web",
        "name": "Operator customized client",
        "redirectUris": [
            "http://localhost/*",
            "https://operator-idp.example/callback",
        ],
        "attributes": {"pkce.code.challenge.method": "S256", "operator-note": "keep"},
    }
    updates: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "admin-token"})
        if request.method == "GET" and request.url.path == "/admin/realms/easysynq/clients":
            assert request.url.params["clientId"] == "easysynq-web"
            return httpx.Response(
                200,
                json=[{"id": "client-uuid", "clientId": "easysynq-web"}],
            )
        if request.method == "GET" and request.url.path.endswith("/clients/client-uuid"):
            return httpx.Response(200, json=representation)
        if request.method == "PUT" and request.url.path.endswith("/clients/client-uuid"):
            update = json.loads(request.content)
            assert isinstance(update, dict)
            updates.append(update)
            representation.clear()
            representation.update(update)
            return httpx.Response(204)
        raise AssertionError(f"unexpected Keycloak request: {request.method} {request.url}")

    kwargs = {
        "base_url": "http://keycloak:8080",
        "realm": "easysynq",
        "client_id": "easysynq-web",
        "redirect_uri": "https://qms.example.com/*",
        "admin_user": "admin",
        "admin_password": "secret",
        "_transport": httpx.MockTransport(handler),
    }

    assert ensure_client_redirect_uri(**kwargs) is True
    assert representation["redirectUris"] == [
        "http://localhost/*",
        "https://operator-idp.example/callback",
        "https://qms.example.com/*",
    ]
    assert representation["attributes"] == {
        "pkce.code.challenge.method": "S256",
        "operator-note": "keep",
    }

    assert ensure_client_redirect_uri(**kwargs) is False
    assert len(updates) == 1
