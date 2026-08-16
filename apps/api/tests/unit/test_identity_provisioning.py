"""Shared credential-less Keycloak identity provisioning boundaries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from easysynq_api.services.identity import provisioning as identity_provisioning
from easysynq_api.services.identity.provisioning import (
    CredentiallessIdentity,
    IdentityProfile,
    IdentityUsernameExists,
    ensure_credentialless_identity,
    issue_temporary_credential,
    keycloak_client,
)
from easysynq_api.services.keycloak_provisioning import (
    KeycloakConflict,
    KeycloakUnavailable,
    UserLookup,
)


@dataclass
class FakeClient:
    lookup: UserLookup | Exception
    create_result: str | Exception = "sub-created"
    lookup_after_conflict: UserLookup | Exception | None = None
    create_calls: list[dict[str, object]] = field(default_factory=list)
    reset_calls: list[tuple[str, str]] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    lookup_usernames: list[str] = field(default_factory=list)
    lookup_calls: int = 0
    reconcile_calls: list[dict[str, object]] = field(default_factory=list)

    async def ensure_optional_user_profile_fields(self) -> None:
        self.operations.append("profile")

    async def find_user_by_username(self, username: str) -> UserLookup:
        self.operations.append("lookup")
        self.lookup_usernames.append(username)
        self.lookup_calls += 1
        if isinstance(self.lookup, Exception):
            raise self.lookup
        if self.lookup_after_conflict is not None and self.create_calls:
            if isinstance(self.lookup_after_conflict, Exception):
                raise self.lookup_after_conflict
            return self.lookup_after_conflict
        return self.lookup

    async def create_user(self, **kwargs: object) -> str:
        self.operations.append("create")
        self.create_calls.append(kwargs)
        if isinstance(self.create_result, Exception):
            raise self.create_result
        return self.create_result

    async def set_temporary_password(self, *, subject: str, password: str) -> None:
        self.reset_calls.append((subject, password))

    async def reconcile_claimed_user_profile(self, **kwargs: object) -> None:
        self.reconcile_calls.append(kwargs)


def _profile() -> IdentityProfile:
    return IdentityProfile("jdoe", "jdoe@example.local", "J", "Doe")


async def test_identity_boundary_canonicalizes_username_before_lookup_and_create() -> None:
    client = FakeClient(UserLookup(found=False))

    await ensure_credentialless_identity(
        client,
        IdentityProfile("  JDoe  ", "jdoe@example.local", "J", "Doe"),
    )

    assert client.lookup_usernames == ["jdoe"]
    assert client.create_calls[0]["username"] == "jdoe"


async def test_ordinary_identity_creation_sends_no_bootstrap_marker() -> None:
    client = FakeClient(UserLookup(found=False))

    result = await ensure_credentialless_identity(client, _profile())

    assert result == CredentiallessIdentity(subject="sub-created", created=True)
    assert client.create_calls == [
        {
            "username": "jdoe",
            "email": "jdoe@example.local",
            "first_name": "J",
            "last_name": "Doe",
            "bootstrap_claim_id": None,
        }
    ]
    assert client.reset_calls == []


async def test_ordinary_identity_reconciles_profile_before_lookup_and_create() -> None:
    client = FakeClient(UserLookup(found=False))

    await ensure_credentialless_identity(client, _profile())

    assert client.operations == ["profile", "lookup", "create"]


async def test_bootstrap_retry_reconciles_profile_before_adopting_claimed_identity() -> None:
    claim = uuid.uuid4()
    client = FakeClient(
        UserLookup(found=True, subject="sub-existing", bootstrap_claim_id=str(claim))
    )

    result = await ensure_credentialless_identity(
        client, _profile(), bootstrap_claim_id=claim, allow_matching_claim=True
    )

    assert client.operations == ["profile", "lookup"]
    assert result == CredentiallessIdentity(subject="sub-existing", created=False)


async def test_claimed_profile_reconciliation_canonicalizes_and_forwards_exact_identity() -> None:
    claim = uuid.uuid4()
    client = FakeClient(UserLookup(found=False))

    await identity_provisioning.reconcile_claimed_identity_profile(
        client,
        IdentityProfile("  First.Admin  ", "admin@example.local", "First", None),
        subject="sub-1",
        bootstrap_claim_id=claim,
    )

    assert client.reconcile_calls == [
        {
            "subject": "sub-1",
            "username": "first.admin",
            "bootstrap_claim_id": claim,
            "email": "admin@example.local",
            "first_name": "First",
            "last_name": None,
        }
    ]


async def test_ordinary_identity_provisioning_never_reconciles_a_claimed_profile() -> None:
    client = FakeClient(UserLookup(found=False))

    await ensure_credentialless_identity(client, _profile())

    assert client.reconcile_calls == []


async def test_ordinary_existing_username_raises_with_subject() -> None:
    client = FakeClient(UserLookup(found=True, subject="sub-existing"))

    with pytest.raises(IdentityUsernameExists) as excinfo:
        await ensure_credentialless_identity(client, _profile())

    assert excinfo.value.subject == "sub-existing"
    assert "sub-existing" not in str(excinfo.value)


async def test_bootstrap_matching_marker_adopts_existing_identity() -> None:
    claim = uuid.uuid4()
    client = FakeClient(UserLookup(True, "sub-existing", str(claim)))

    result = await ensure_credentialless_identity(
        client, _profile(), bootstrap_claim_id=claim, allow_matching_claim=True
    )

    assert result == CredentiallessIdentity(subject="sub-existing", created=False)
    assert client.create_calls == []


@pytest.mark.parametrize("marker", [None, str(uuid.uuid4())])
async def test_bootstrap_missing_or_different_marker_rejects_existing_identity(
    marker: str | None,
) -> None:
    client = FakeClient(UserLookup(True, "sub-existing", marker))

    with pytest.raises(IdentityUsernameExists):
        await ensure_credentialless_identity(
            client, _profile(), bootstrap_claim_id=uuid.uuid4(), allow_matching_claim=True
        )

    assert client.create_calls == []
    assert client.reset_calls == []


async def test_malformed_marker_lookup_propagates_without_creating_or_resetting() -> None:
    client = FakeClient(KeycloakUnavailable("Keycloak bootstrap marker was malformed"))

    with pytest.raises(KeycloakUnavailable):
        await ensure_credentialless_identity(
            client, _profile(), bootstrap_claim_id=uuid.uuid4(), allow_matching_claim=True
        )

    assert client.create_calls == []
    assert client.reset_calls == []


async def test_create_conflict_adopts_only_reread_matching_marker() -> None:
    claim = uuid.uuid4()
    client = FakeClient(
        UserLookup(False),
        KeycloakConflict("username", "duplicate"),
        UserLookup(True, "sub-race", str(claim)),
    )

    result = await ensure_credentialless_identity(
        client, _profile(), bootstrap_claim_id=claim, allow_matching_claim=True
    )

    assert result == CredentiallessIdentity(subject="sub-race", created=False)


async def test_ordinary_classifier_known_conflict_raises_without_a_reread() -> None:
    """A post-classifier transient lookup must not erase the ordinary 409 collision."""
    client = FakeClient(
        UserLookup(False),
        KeycloakConflict("username", "duplicate", keycloak_subject="sub-race"),
        KeycloakUnavailable("unexpected third lookup"),
    )

    with pytest.raises(IdentityUsernameExists) as excinfo:
        await ensure_credentialless_identity(client, _profile())

    assert excinfo.value.subject == "sub-race"
    assert client.lookup_calls == 1


async def test_bootstrap_classifier_known_conflict_rereads_and_validates_the_marker() -> None:
    claim = uuid.uuid4()
    client = FakeClient(
        UserLookup(False),
        KeycloakConflict("username", "duplicate", keycloak_subject="sub-race"),
        UserLookup(True, "sub-race", str(claim)),
    )

    result = await ensure_credentialless_identity(
        client, _profile(), bootstrap_claim_id=claim, allow_matching_claim=True
    )

    assert result == CredentiallessIdentity(subject="sub-race", created=False)
    assert client.lookup_calls == 2


async def test_unresolved_bootstrap_conflict_fails_closed() -> None:
    client = FakeClient(
        UserLookup(False), KeycloakConflict("username", "duplicate"), UserLookup(found=False)
    )

    with pytest.raises(KeycloakUnavailable):
        await ensure_credentialless_identity(
            client, _profile(), bootstrap_claim_id=uuid.uuid4(), allow_matching_claim=True
        )


async def test_issue_temporary_credential_returns_generated_realm_conforming_password() -> None:
    client = FakeClient(UserLookup(found=False))

    password = await issue_temporary_credential(client, subject="sub-created", username="jdoe")

    assert len(password) >= 12
    assert password.lower() != "jdoe"
    assert client.reset_calls == [("sub-created", password)]


def test_client_factory_uses_existing_keycloak_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from easysynq_api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("KEYCLOAK_ADMIN_URL", "http://keycloak.test")
    monkeypatch.setenv("KEYCLOAK_ADMIN_USER", "admin")
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("OIDC_ISSUER", "http://keycloak.test/realms/easysynq")

    client = keycloak_client()

    assert client._base_url == "http://keycloak.test"
    assert client._realm == "easysynq"
    get_settings.cache_clear()
