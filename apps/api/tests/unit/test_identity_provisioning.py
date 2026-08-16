"""Shared credential-less Keycloak identity provisioning boundaries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

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
    lookup_after_conflict: UserLookup | None = None
    create_calls: list[dict[str, object]] = field(default_factory=list)
    reset_calls: list[tuple[str, str]] = field(default_factory=list)

    async def find_user_by_username(self, username: str) -> UserLookup:
        if isinstance(self.lookup, Exception):
            raise self.lookup
        if self.lookup_after_conflict is not None and self.create_calls:
            return self.lookup_after_conflict
        return self.lookup

    async def create_user(self, **kwargs: object) -> str:
        self.create_calls.append(kwargs)
        if isinstance(self.create_result, Exception):
            raise self.create_result
        return self.create_result

    async def set_temporary_password(self, *, subject: str, password: str) -> None:
        self.reset_calls.append((subject, password))


def _profile() -> IdentityProfile:
    return IdentityProfile("jdoe", "jdoe@example.local", "J", "Doe")


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
