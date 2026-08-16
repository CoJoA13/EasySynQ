"""Async Keycloak provisioning client — mock transport only (D1: no live identity service in CI)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from easysynq_api.services.keycloak_provisioning import (
    BOOTSTRAP_CLAIM_ATTRIBUTE,
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
_WRONG_BOOTSTRAP_CLAIM = str(uuid.uuid4())


def _client(handler: object) -> KeycloakProvisioningClient:
    return KeycloakProvisioningClient(
        **_KWARGS,  # type: ignore[arg-type]
        _transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def _token_ok(request: httpx.Request) -> httpx.Response | None:
    if request.method == "POST" and request.url.path.endswith("/protocol/openid-connect/token"):
        return httpx.Response(200, json={"access_token": "admin-token"})
    return None


def _claimed_user_representation(claim: uuid.UUID) -> dict[str, object]:
    return {
        "id": "sub-1",
        "username": "first.admin",
        "enabled": True,
        "email": "original@example.local",
        "emailVerified": True,
        "firstName": "Original",
        "lastName": "Administrator",
        "attributes": {
            BOOTSTRAP_CLAIM_ATTRIBUTE: [str(claim)],
            "employeeId": ["E-100"],
        },
        "requiredActions": ["UPDATE_PASSWORD"],
        "federationLink": "directory-provider",
        "createdTimestamp": 1_700_000_000_000,
        "access": {"manage": True},
    }


async def test_claimed_user_profile_preserves_every_unapproved_field() -> None:
    claim = uuid.uuid4()
    original = _claimed_user_representation(claim)
    requests: list[str] = []
    put_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        requests.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=original)
        if request.method == "PUT":
            put_bodies.append(json.loads(request.content))
            return httpx.Response(204)
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        await kc.reconcile_claimed_user_profile(
            subject="sub-1",
            username="first.admin",
            bootstrap_claim_id=claim,
            email="corrected@example.local",
            first_name="Corrected",
            last_name=None,
        )

    assert requests == ["GET", "PUT"]
    assert original["email"] == "original@example.local"
    put_body = put_bodies[0]
    assert put_body["id"] == "sub-1"
    assert put_body["username"] == "first.admin"
    assert put_body["attributes"] == original["attributes"]
    assert put_body["requiredActions"] == original["requiredActions"]
    assert put_body["federationLink"] == original["federationLink"]
    assert put_body["createdTimestamp"] == original["createdTimestamp"]
    assert put_body["access"] == original["access"]
    assert put_body["email"] == "corrected@example.local"
    assert put_body["emailVerified"] is True
    assert put_body["firstName"] == "Corrected"
    assert put_body["lastName"] is None


async def test_claimed_user_profile_skips_put_when_approved_fields_are_equal() -> None:
    claim = uuid.uuid4()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        requests.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=_claimed_user_representation(claim))
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        await kc.reconcile_claimed_user_profile(
            subject="sub-1",
            username="first.admin",
            bootstrap_claim_id=claim,
            email="original@example.local",
            first_name="Original",
            last_name="Administrator",
        )

    assert requests == ["GET"]


async def test_claimed_user_profile_uses_explicit_clearing_semantics() -> None:
    claim = uuid.uuid4()
    put_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET":
            return httpx.Response(200, json=_claimed_user_representation(claim))
        put_bodies.append(json.loads(request.content))
        return httpx.Response(204)

    async with _client(handler) as kc:
        await kc.reconcile_claimed_user_profile(
            subject="sub-1",
            username="first.admin",
            bootstrap_claim_id=claim,
            email=None,
            first_name=None,
            last_name=None,
        )

    assert put_bodies[0]["email"] is None
    assert put_bodies[0]["emailVerified"] is False
    assert put_bodies[0]["firstName"] is None
    assert put_bodies[0]["lastName"] is None


@pytest.mark.parametrize(
    ("mutation", "sensitive"),
    [
        ({"id": "sub-other"}, "sub-other"),
        ({"username": "other.admin"}, "other.admin"),
        ({"attributes": {"employeeId": ["E-100"]}}, "E-100"),
        (
            {"attributes": {BOOTSTRAP_CLAIM_ATTRIBUTE: ["not-a-uuid"]}},
            "not-a-uuid",
        ),
        (
            {"attributes": {BOOTSTRAP_CLAIM_ATTRIBUTE: [_WRONG_BOOTSTRAP_CLAIM]}},
            _WRONG_BOOTSTRAP_CLAIM,
        ),
    ],
)
async def test_claimed_user_profile_fails_closed_on_wrong_or_malformed_ownership(
    mutation: dict[str, object], sensitive: str
) -> None:
    claim = uuid.uuid4()
    representation = _claimed_user_representation(claim)
    representation.update(mutation)
    put_attempted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_attempted
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET":
            return httpx.Response(200, json=representation)
        put_attempted = True
        return httpx.Response(204)

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable) as excinfo:
            await kc.reconcile_claimed_user_profile(
                subject="sub-1",
                username="first.admin",
                bootstrap_claim_id=claim,
                email="corrected@example.local",
                first_name="Corrected",
                last_name="Administrator",
            )

    assert put_attempted is False
    assert "sub-1" not in str(excinfo.value)
    assert str(claim) not in str(excinfo.value)
    assert sensitive not in str(excinfo.value)


@pytest.mark.parametrize("status_code", [400, 409])
async def test_claimed_user_profile_maps_validation_rejection_without_provider_detail(
    status_code: int,
) -> None:
    claim = uuid.uuid4()
    provider_detail = f"invalid subject=sub-1 marker={claim}"

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET":
            return httpx.Response(200, json=_claimed_user_representation(claim))
        return httpx.Response(status_code, json={"errorMessage": provider_detail})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakRejected) as excinfo:
            await kc.reconcile_claimed_user_profile(
                subject="sub-1",
                username="first.admin",
                bootstrap_claim_id=claim,
                email="corrected@example.local",
                first_name="Corrected",
                last_name="Administrator",
            )

    assert "sub-1" not in str(excinfo.value)
    assert str(claim) not in str(excinfo.value)
    assert provider_detail not in str(excinfo.value)


@pytest.mark.parametrize("status_code", [403, 404, 500, 503])
@pytest.mark.parametrize("operation", ["GET", "PUT"])
async def test_claimed_user_profile_maps_unavailable_status_without_provider_detail(
    status_code: int, operation: str
) -> None:
    claim = uuid.uuid4()
    provider_detail = f"provider subject=sub-1 marker={claim}"

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and operation == "PUT":
            return httpx.Response(200, json=_claimed_user_representation(claim))
        return httpx.Response(status_code, json={"error": provider_detail})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable) as excinfo:
            await kc.reconcile_claimed_user_profile(
                subject="sub-1",
                username="first.admin",
                bootstrap_claim_id=claim,
                email="corrected@example.local",
                first_name="Corrected",
                last_name="Administrator",
            )

    assert "sub-1" not in str(excinfo.value)
    assert str(claim) not in str(excinfo.value)
    assert provider_detail not in str(excinfo.value)


@pytest.mark.parametrize("body", [[], None, "not-an-object"])
async def test_claimed_user_profile_rejects_malformed_json_shape(body: object) -> None:
    claim = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(200, json=body)

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.reconcile_claimed_user_profile(
                subject="sub-1",
                username="first.admin",
                bootstrap_claim_id=claim,
                email=None,
                first_name=None,
                last_name=None,
            )


async def test_claimed_user_profile_rejects_malformed_json_body() -> None:
    claim = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(200, content=b"{not-json")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.reconcile_claimed_user_profile(
                subject="sub-1",
                username="first.admin",
                bootstrap_claim_id=claim,
                email=None,
                first_name=None,
                last_name=None,
            )


async def test_claimed_user_profile_maps_transport_failure_without_identity_detail() -> None:
    claim = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        raise httpx.ConnectError(f"failed for {request.url}", request=request)

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable) as excinfo:
            await kc.reconcile_claimed_user_profile(
                subject="sub-1",
                username="first.admin",
                bootstrap_claim_id=claim,
                email=None,
                first_name=None,
                last_name=None,
            )

    assert "sub-1" not in str(excinfo.value)
    assert str(claim) not in str(excinfo.value)


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


async def test_lookup_returns_one_well_formed_bootstrap_claim() -> None:
    claim = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json=[
                {
                    "id": "sub-jdoe",
                    "username": "jdoe",
                    "attributes": {BOOTSTRAP_CLAIM_ATTRIBUTE: [claim]},
                }
            ],
        )

    async with _client(handler) as kc:
        result = await kc.find_user_by_username("jdoe")

    assert result.bootstrap_claim_id == claim


async def test_lookup_without_bootstrap_claim_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(200, json=[{"id": "sub-jdoe", "username": "jdoe"}])

    async with _client(handler) as kc:
        result = await kc.find_user_by_username("jdoe")

    assert result.bootstrap_claim_id is None


@pytest.mark.parametrize(
    "marker",
    [["first", "second"], "not-a-list", [""], [1], [], None, {}, True],
)
async def test_lookup_with_malformed_bootstrap_claim_fails_closed(marker: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json=[
                {
                    "id": "sub-jdoe",
                    "username": "jdoe",
                    "attributes": {BOOTSTRAP_CLAIM_ATTRIBUTE: marker},
                }
            ],
        )

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.find_user_by_username("jdoe")


async def test_lookup_with_empty_attributes_returns_no_bootstrap_claim() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(
            200,
            json=[{"id": "sub-jdoe", "username": "jdoe", "attributes": {}}],
        )

    async with _client(handler) as kc:
        result = await kc.find_user_by_username("jdoe")

    assert result.bootstrap_claim_id is None


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
    assert "attributes" not in body


async def test_user_profile_reconcile_makes_only_product_optional_fields_optional() -> None:
    current_profile = {
        "attributes": [
            {
                "name": "username",
                "required": {"roles": ["admin", "user"]},
                "permissions": {"view": ["admin", "user"]},
            },
            {
                "name": "email",
                "required": {"roles": ["user"]},
                "validations": {"email": {}, "length": {"max": 255}},
            },
            {
                "name": "firstName",
                "required": {"roles": ["user"]},
                "validations": {"length": {"max": 255}},
            },
            {
                "name": "lastName",
                "required": {"roles": ["user"]},
                "validations": {"length": {"max": 255}},
            },
            {
                "name": "employeeId",
                "required": {"roles": ["user"]},
                "annotations": {"inputType": "text"},
            },
        ],
        "groups": [{"name": "employment"}],
        "unmanagedAttributePolicy": "ENABLED",
    }
    updated: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(200, json=current_profile)
        if request.method == "PUT" and request.url.path.endswith("/users/profile"):
            updated.append(json.loads(request.content))
            return httpx.Response(200, json=updated[-1])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        await kc.ensure_optional_user_profile_fields()

    assert updated == [
        {
            "attributes": [
                {
                    "name": "username",
                    "required": {"roles": ["admin", "user"]},
                    "permissions": {"view": ["admin", "user"]},
                },
                {
                    "name": "email",
                    "validations": {"email": {}, "length": {"max": 255}},
                },
                {"name": "firstName", "validations": {"length": {"max": 255}}},
                {"name": "lastName", "validations": {"length": {"max": 255}}},
                {
                    "name": "employeeId",
                    "required": {"roles": ["user"]},
                    "annotations": {"inputType": "text"},
                },
                {
                    "name": BOOTSTRAP_CLAIM_ATTRIBUTE,
                    "permissions": {"view": ["admin"], "edit": ["admin"]},
                    "multivalued": False,
                },
            ],
            "groups": [{"name": "employment"}],
            "unmanagedAttributePolicy": "ENABLED",
        }
    ]


async def test_user_profile_reconcile_defines_admin_only_marker_for_fresh_realm() -> None:
    updated: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(
                200,
                json={
                    "attributes": [
                        {
                            "name": "username",
                            "required": {"roles": ["admin", "user"]},
                            "permissions": {
                                "view": ["admin", "user"],
                                "edit": ["admin", "user"],
                            },
                        },
                        {
                            "name": "email",
                            "required": {"roles": ["user"]},
                            "validations": {"email": {}, "length": {"max": 255}},
                        },
                        {
                            "name": "firstName",
                            "required": {"roles": ["user"]},
                            "validations": {"length": {"max": 255}},
                        },
                        {
                            "name": "lastName",
                            "required": {"roles": ["user"]},
                            "validations": {"length": {"max": 255}},
                        },
                    ],
                    "groups": [{"name": "user-metadata"}],
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/users/profile"):
            updated.append(json.loads(request.content))
            return httpx.Response(200, json=updated[-1])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        await kc.ensure_optional_user_profile_fields()

    assert updated == [
        {
            "attributes": [
                {
                    "name": "username",
                    "required": {"roles": ["admin", "user"]},
                    "permissions": {
                        "view": ["admin", "user"],
                        "edit": ["admin", "user"],
                    },
                },
                {
                    "name": "email",
                    "validations": {"email": {}, "length": {"max": 255}},
                },
                {"name": "firstName", "validations": {"length": {"max": 255}}},
                {"name": "lastName", "validations": {"length": {"max": 255}}},
                {
                    "name": BOOTSTRAP_CLAIM_ATTRIBUTE,
                    "permissions": {"view": ["admin"], "edit": ["admin"]},
                    "multivalued": False,
                },
            ],
            "groups": [{"name": "user-metadata"}],
        }
    ]
    assert "unmanagedAttributePolicy" not in updated[0]


async def test_user_profile_reconcile_replaces_unsafe_marker_without_broadening_policy() -> None:
    updated: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(
                200,
                json={
                    "attributes": [
                        {"name": "username", "required": {"roles": ["admin", "user"]}},
                        {"name": "email"},
                        {"name": "firstName"},
                        {"name": "lastName"},
                        {
                            "name": BOOTSTRAP_CLAIM_ATTRIBUTE,
                            "permissions": {
                                "view": ["admin", "user"],
                                "edit": ["admin", "user"],
                            },
                            "multivalued": True,
                            "annotations": {"inputType": "text"},
                        },
                        {"name": "employeeId", "permissions": {"view": ["admin"]}},
                    ],
                    "groups": [{"name": "employment"}],
                    "unmanagedAttributePolicy": "DISABLED",
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/users/profile"):
            updated.append(json.loads(request.content))
            return httpx.Response(200, json=updated[-1])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        await kc.ensure_optional_user_profile_fields()

    assert updated == [
        {
            "attributes": [
                {"name": "username", "required": {"roles": ["admin", "user"]}},
                {"name": "email"},
                {"name": "firstName"},
                {"name": "lastName"},
                {
                    "name": BOOTSTRAP_CLAIM_ATTRIBUTE,
                    "permissions": {"view": ["admin"], "edit": ["admin"]},
                    "multivalued": False,
                },
                {"name": "employeeId", "permissions": {"view": ["admin"]}},
            ],
            "groups": [{"name": "employment"}],
            "unmanagedAttributePolicy": "DISABLED",
        }
    ]


async def test_user_profile_reconcile_fails_closed_on_duplicate_marker_definition() -> None:
    put_attempted = False
    marker = {
        "name": BOOTSTRAP_CLAIM_ATTRIBUTE,
        "permissions": {"view": ["admin"], "edit": ["admin"]},
        "multivalued": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_attempted
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(
                200,
                json={
                    "attributes": [
                        {"name": "username"},
                        {"name": "email"},
                        {"name": "firstName"},
                        {"name": "lastName"},
                        marker,
                        marker,
                    ]
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/users/profile"):
            put_attempted = True
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable, match="duplicate bootstrap marker"):
            await kc.ensure_optional_user_profile_fields()

    assert put_attempted is False


async def test_user_profile_reconcile_fails_closed_when_a_builtin_field_is_missing() -> None:
    put_attempted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_attempted
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(
                200,
                json={
                    "attributes": [
                        {"name": "username"},
                        {"name": "email"},
                        {"name": "firstName"},
                    ]
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/users/profile"):
            put_attempted = True
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.ensure_optional_user_profile_fields()

    assert put_attempted is False


async def test_user_profile_reconcile_skips_put_when_already_optional() -> None:
    put_attempted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_attempted
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(
                200,
                json={
                    "attributes": [
                        {"name": "username", "required": {"roles": ["user"]}},
                        {"name": "email", "validations": {"email": {}}},
                        {"name": "firstName"},
                        {"name": "lastName"},
                        {"name": "employeeId", "required": {"roles": ["user"]}},
                        {
                            "name": BOOTSTRAP_CLAIM_ATTRIBUTE,
                            "permissions": {"view": ["admin"], "edit": ["admin"]},
                            "multivalued": False,
                        },
                    ],
                    "groups": [{"name": "employment"}],
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/users/profile"):
            put_attempted = True
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        await kc.ensure_optional_user_profile_fields()

    assert put_attempted is False


async def test_user_profile_reconcile_fails_closed_when_read_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(503, json={"error": "unavailable"})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable, match="user-profile read failed"):
            await kc.ensure_optional_user_profile_fields()


async def test_user_profile_reconcile_fails_closed_when_write_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(
                200,
                json={
                    "attributes": [
                        {"name": "email", "required": {"roles": ["user"]}},
                        {"name": "firstName", "required": {"roles": ["user"]}},
                        {"name": "lastName", "required": {"roles": ["user"]}},
                    ]
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/users/profile"):
            return httpx.Response(409, json={"error": "conflict"})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable, match="user-profile update failed"):
            await kc.ensure_optional_user_profile_fields()


async def test_create_user_sends_only_bootstrap_marker_for_bootstrap_calls() -> None:
    claim = uuid.uuid4()
    posted: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        posted.append(json.loads(request.content))
        return httpx.Response(
            201,
            headers={"Location": "http://keycloak:8080/admin/realms/easysynq/users/sub-new"},
        )

    async with _client(handler) as kc:
        await kc.create_user(
            username="jdoe",
            email=None,
            first_name=None,
            last_name=None,
            bootstrap_claim_id=claim,
        )

    assert posted[0]["attributes"] == {BOOTSTRAP_CLAIM_ATTRIBUTE: [str(claim)]}
    assert "credentials" not in posted[0]


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
