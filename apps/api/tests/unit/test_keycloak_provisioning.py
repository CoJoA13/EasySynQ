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
    """Keycloak echoing a DIFFERENT user (the contains-match hazard) must raise — it must never be
    accepted as a match, and it must never be collapsed into "definitively absent" either, since
    that would let the caller fall through to CREATE and conflict on the real existing account."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path == "/admin/realms/easysynq/users":
            seen.update(dict(request.url.params))
            return httpx.Response(200, json=[{"id": "sub-joann", "username": "joann"}])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.find_user_by_username("ann")

    assert seen["exact"] == "true"
    assert seen["username"] == "ann"


async def test_lookup_mismatch_from_username_normalization_raises() -> None:
    """The reachable real-world case: Keycloak lowercases usernames, so provisioning `JDoe` while
    `jdoe` already exists returns exactly this shape. It must raise rather than report absent —
    reporting absent would let the caller fall through to CREATE and 409 against that very
    account instead of surfacing the "link the existing account" affordance."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(200, json=[{"id": "sub-jdoe", "username": "jdoe"}])

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.find_user_by_username("JDoe")


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


async def test_create_user_classifies_a_username_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(409, json={"errorMessage": "User exists with same username"})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakConflict) as excinfo:
            await kc.create_user(username="jdoe", email=None, first_name=None, last_name=None)

    assert excinfo.value.field == "username"


async def test_set_password_failure_never_leaks_the_password() -> None:
    secret = "Xk4m-Pq7r-Ts2v-Wy8n-Bd3h"

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(500, json={"error": "boom"})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable) as excinfo:
            await kc.set_temporary_password(subject="sub-new", password=secret)

    assert secret not in str(excinfo.value)
    assert secret not in repr(excinfo.value)


async def test_lookup_finds_the_exact_match_among_contains_match_siblings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        # Keycloak's username query is a CONTAINS match: asking for `ann` returns all of these.
        return httpx.Response(
            200,
            json=[
                {"id": "sub-joann", "username": "joann"},
                {"id": "sub-ann", "username": "ann"},
                {"id": "sub-annette", "username": "annette"},
            ],
        )

    async with _client(handler) as kc:
        result = await kc.find_user_by_username("ann")

    assert result.found is True
    assert result.subject == "sub-ann"


async def test_token_response_that_is_not_an_object_raises_unavailable() -> None:
    """A 200 with valid-but-non-object JSON (Keycloak or an intermediary returning `null` or a
    bare array) must not reach `.get("access_token")` on a non-dict — `response.json()` succeeds
    either way, so the shape must be checked BEFORE reading the field, mirroring
    `find_user_by_username`'s `isinstance(body, list)` guard. Pre-fix, `[].get(...)` raised an
    uncaught `AttributeError` the surrounding `except (httpx.HTTPError, ValueError)` does not
    catch, surfacing as an internal 500 instead of the documented `keycloak_unavailable` 502."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.find_user_by_username("jdoe")


async def test_create_user_falls_back_to_lookup_when_location_is_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "POST" and request.url.path == "/admin/realms/easysynq/users":
            return httpx.Response(201)  # no Location header
        if request.method == "GET" and request.url.path == "/admin/realms/easysynq/users":
            return httpx.Response(200, json=[{"id": "sub-recovered", "username": "jdoe"}])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        subject = await kc.create_user(username="jdoe", email=None, first_name=None, last_name=None)

    assert subject == "sub-recovered"
