"""Async Keycloak provisioning client — mock transport only (D1: no live identity service in CI)."""

from __future__ import annotations

import json

import httpx
import pytest

from easysynq_api.services.keycloak_provisioning import (
    KeycloakConflict,
    KeycloakNotConfigured,
    KeycloakProvisioningClient,
    KeycloakRejected,
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


# --- create-conflict classification is ground-truth, not message-guessed (P2 fix) -------------
#
# Keycloak's duplicate-user 409 commonly reports the COMBINED message "User exists with same
# username or email" — which contains "email" even for a pure username collision, and even when
# the request supplied no email at all. The old `_conflict_field(response)`-only classification
# read that as an email duplicate, so `provision_user` raised `keycloak_email_exists` and the SPA
# highlighted the email field instead of offering "Link the existing account". The fix re-reads
# the username (the same `exact=true`, re-verified lookup `find_user_by_username` already uses)
# and classifies from that ground truth; only if the re-read itself fails does it fall back to the
# message heuristic — which itself now prefers "username" whenever the message is ambiguous.


async def test_create_conflict_classifies_username_when_reread_finds_it() -> None:
    """The exact case this fix corrects: a combined 409 message, but the username genuinely
    resolves on re-read — the true collision is the username, not the (possibly absent) email."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "POST" and request.url.path == "/admin/realms/easysynq/users":
            return httpx.Response(
                409, json={"errorMessage": "User exists with same username or email"}
            )
        if request.method == "GET" and request.url.path == "/admin/realms/easysynq/users":
            return httpx.Response(200, json=[{"id": "sub-jdoe", "username": "jdoe"}])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakConflict) as excinfo:
            await kc.create_user(username="jdoe", email=None, first_name=None, last_name=None)

    assert excinfo.value.field == "username"
    # FIX 2: the re-read RESOLVED the colliding subject — it must be carried out of the conflict,
    # not thrown away, so `provision_user` can classify it linked-vs-unlinked instead of reducing
    # a genuine race to a bare, undifferentiated `user_exists`.
    assert excinfo.value.keycloak_subject == "sub-jdoe"


async def test_create_conflict_classifies_email_when_username_absent_on_reread() -> None:
    """When the re-read comes back definitively empty, the collision must be the email: create
    only 409s on one of the two unique fields, and the username is provably not it. The message
    here names only "username" — a message-only classifier would say "username"; only trusting
    the re-read's ground truth over the message gets this right."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "POST" and request.url.path == "/admin/realms/easysynq/users":
            return httpx.Response(409, json={"errorMessage": "User exists with same username"})
        if request.method == "GET" and request.url.path == "/admin/realms/easysynq/users":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakConflict) as excinfo:
            await kc.create_user(
                username="jdoe", email="taken@example.local", first_name=None, last_name=None
            )

    assert excinfo.value.field == "email"
    # No username collision was found, so there is no subject to carry.
    assert excinfo.value.keycloak_subject is None


async def test_create_conflict_falls_back_when_reread_itself_fails() -> None:
    """If the re-read itself fails (a transient lookup outage), classification must degrade to the
    message heuristic — never let the lookup failure escape as an unrelated outage, and never
    guess. The combined message mentions BOTH words, so the heuristic must not read it as an email
    collision either: it prefers "username" (the field with a recovery path) when ambiguous."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "POST" and request.url.path == "/admin/realms/easysynq/users":
            return httpx.Response(
                409, json={"errorMessage": "User exists with same username or email"}
            )
        if request.method == "GET" and request.url.path == "/admin/realms/easysynq/users":
            return httpx.Response(503, json={"error": "boom"})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakConflict) as excinfo:
            await kc.create_user(
                username="jdoe", email="taken@example.local", first_name=None, last_name=None
            )

    assert excinfo.value.field == "username"
    # The re-read itself failed, so no subject was ever resolved — the fallback heuristic must
    # not fabricate one.
    assert excinfo.value.keycloak_subject is None


# --- a 4xx other than 409 is rejected input, never an outage (P2 fix) --------------------------


async def test_create_user_400_raises_rejected_not_unavailable() -> None:
    """A Keycloak 400 (an invalid email, or a value the realm's user-profile validation refuses)
    is a client error, not an outage: the dependency IS reachable, and retrying the identical form
    cannot succeed. It must raise the distinct rejected-input exception, never KeycloakUnavailable,
    and the detail must carry Keycloak's own explanation (bounded/sanitised, never the raw body)."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(400, json={"errorMessage": "Invalid email address."})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakRejected) as excinfo:
            await kc.create_user(
                username="jdoe", email="not-an-email", first_name=None, last_name=None
            )

    assert "Invalid email address" in excinfo.value.detail


async def test_create_user_500_still_raises_unavailable() -> None:
    """5xx is still an outage — only a non-409 4xx maps to the distinct rejected-input exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(500, json={"error": "boom"})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.create_user(username="jdoe", email=None, first_name=None, last_name=None)
