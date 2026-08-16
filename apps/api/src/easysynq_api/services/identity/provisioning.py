"""Fail-closed shared Keycloak identity provisioning primitives.

This module deliberately has no database session, authorization decision, role assignment, or
audit event. Callers own those boundaries and must set a credential only after their durable work
commits.
"""

from __future__ import annotations

import dataclasses
import uuid

from ...config import get_settings
from ...domain.identity.temp_password import generate_temporary_password
from ..backup.realm_export import realm_name_from_issuer
from ..keycloak_provisioning import (
    KeycloakConflict,
    KeycloakProvisioningClient,
    KeycloakUnavailable,
    UserLookup,
)


@dataclasses.dataclass(frozen=True, slots=True)
class IdentityProfile:
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class CredentiallessIdentity:
    subject: str
    created: bool


class IdentityUsernameExists(Exception):
    def __init__(self, subject: str) -> None:
        self.subject = subject
        super().__init__("username already exists")


def keycloak_client() -> KeycloakProvisioningClient:
    """Build the request-scoped Keycloak client from the established application settings."""
    settings = get_settings()
    return KeycloakProvisioningClient(
        base_url=settings.keycloak_admin_url,
        realm=realm_name_from_issuer(settings.oidc_issuer),
        admin_user=settings.keycloak_admin_user,
        admin_password=settings.keycloak_admin_password,
    )


async def ensure_credentialless_identity(
    client: KeycloakProvisioningClient,
    profile: IdentityProfile,
    *,
    bootstrap_claim_id: uuid.UUID | None = None,
    allow_matching_claim: bool = False,
) -> CredentiallessIdentity:
    """Find or create only the identity this operation is allowed to own."""
    lookup = await client.find_user_by_username(profile.username)
    if lookup.found:
        return _existing_identity(
            lookup,
            bootstrap_claim_id=bootstrap_claim_id,
            allow_matching_claim=allow_matching_claim,
        )

    try:
        subject = await client.create_user(
            username=profile.username,
            email=profile.email,
            first_name=profile.first_name,
            last_name=profile.last_name,
            bootstrap_claim_id=bootstrap_claim_id,
        )
    except KeycloakConflict as exc:
        if exc.field != "username":
            raise
        # The transport re-reads to classify a 409; this re-read also proves the marker before
        # a bootstrap retry adopts the identity created by the racing request.
        raced = await client.find_user_by_username(profile.username)
        if not raced.found:
            raise KeycloakUnavailable("Keycloak create conflict could not be resolved") from exc
        return _existing_identity(
            raced,
            bootstrap_claim_id=bootstrap_claim_id,
            allow_matching_claim=allow_matching_claim,
        )
    return CredentiallessIdentity(subject=subject, created=True)


async def issue_temporary_credential(
    client: KeycloakProvisioningClient, *, subject: str, username: str
) -> str:
    """Set a fresh, realm-conforming temporary credential and return it only to the caller."""
    password = generate_temporary_password(username)
    await client.set_temporary_password(subject=subject, password=password)
    return password


def _existing_identity(
    lookup: UserLookup,
    *,
    bootstrap_claim_id: uuid.UUID | None,
    allow_matching_claim: bool,
) -> CredentiallessIdentity:
    if lookup.subject is None:
        raise KeycloakUnavailable("Keycloak found an identity without a subject")
    if (
        allow_matching_claim
        and bootstrap_claim_id is not None
        and lookup.bootstrap_claim_id == str(bootstrap_claim_id)
    ):
        return CredentiallessIdentity(subject=lookup.subject, created=False)
    raise IdentityUsernameExists(lookup.subject)
