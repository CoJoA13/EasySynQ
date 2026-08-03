"""Async Keycloak provisioning client — mock transport only (D1: no live identity service in CI)."""

from __future__ import annotations

import json

import httpx
import pytest

from easysynq_api.services.keycloak_provisioning import (
    KeycloakConflict,
    KeycloakNotConfigured,
    KeycloakProvisioningClient,
    KeycloakUnavailable,
)

_KWARGS = {
    "base_url": "http://keycloak:8080",
    "realm": "easysynq",
    "admin_user": "admin",
    "admin_password": "secret",
}


def _client(handler: object) -> KeycloakProvisioningClient:
    return KeycloakProvisioningClient(
        **_KWARGS,  # type: ignore[arg-type]
        _transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def _token_ok(request: httpx.Request) -> httpx.Response | None:
    if request.method == "POST" and request.url.path.endswith("/protocol/openid-connect/token"):
        return httpx.Response(200, json={"access_token": "admin-token"})
    return None


async def test_lookup_requires_exact_and_reverifies_the_returned_username() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path == "/admin/realms/easysynq/users":
            seen.update(dict(request.url.params))
            # Keycloak echoing a DIFFERENT user (the contains-match hazard) must not be accepted.
            return httpx.Response(200, json=[{"id": "sub-joann", "username": "joann"}])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        result = await kc.find_user_by_username("ann")

    assert seen["exact"] == "true"
    assert seen["username"] == "ann"
    assert result.found is False
    assert result.subject is None


async def test_lookup_returns_the_subject_on_an_exact_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(200, json=[{"id": "sub-jdoe", "username": "jdoe"}])

    async with _client(handler) as kc:
        result = await kc.find_user_by_username("jdoe")

    assert result.found is True
    assert result.subject == "sub-jdoe"


async def test_lookup_failure_is_not_absence() -> None:
    """A transient 5xx must raise — never fall through to CREATE as if the user were absent."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(503, json={"error": "unavailable"})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.find_user_by_username("jdoe")


async def test_create_user_sends_no_credential_and_returns_the_subject() -> None:
    posted: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "POST" and request.url.path == "/admin/realms/easysynq/users":
            posted.append(json.loads(request.content))
            return httpx.Response(
                201,
                headers={"Location": "http://keycloak:8080/admin/realms/easysynq/users/sub-new"},
            )
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        subject = await kc.create_user(
            username="jdoe", email="jdoe@example.local", first_name="J", last_name="Doe"
        )

    assert subject == "sub-new"
    body = posted[0]
    assert body["username"] == "jdoe"
    assert body["enabled"] is True
    # The account is created WITHOUT a credential; the password is set only after the PG commit.
    assert "credentials" not in body


async def test_create_user_maps_a_conflict_to_the_offending_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(409, json={"errorMessage": "User exists with same email"})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakConflict) as excinfo:
            await kc.create_user(
                username="jdoe", email="taken@example.local", first_name=None, last_name=None
            )

    assert excinfo.value.field == "email"


async def test_set_temporary_password_marks_the_credential_temporary() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "PUT" and request.url.path.endswith("/users/sub-new/reset-password"):
            sent.append(json.loads(request.content))
            return httpx.Response(204)
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        await kc.set_temporary_password(subject="sub-new", password="Xk4m-Pq7r-Ts2v-Wy8n-Bd3h")

    assert sent[0]["temporary"] is True
    assert sent[0]["type"] == "password"


async def test_missing_admin_credentials_fail_closed() -> None:
    client = KeycloakProvisioningClient(
        base_url="http://keycloak:8080", realm="easysynq", admin_user="", admin_password=""
    )
    with pytest.raises(KeycloakNotConfigured):
        async with client as kc:
            await kc.find_user_by_username("jdoe")
