"""Secret-authorized first-administrator provisioning and recovery (ADR 0005).

Keycloak and PostgreSQL cannot share a transaction.  The singleton ``system_config`` claim is the
durable rendezvous point between their staged commits: every database stage serializes on that row,
and every retry is allowed to converge only on the exact claimed username and marked identity.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import logging
import secrets
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.app_user import AppUser, UserStatus
from ...db.models.audit_event import AuditEvent
from ...db.models.role import Role, RoleAssignment
from ...db.models.system_config import SetupState, SystemConfig
from ...logging import request_id_var
from ...problems import ProblemException
from ..authz.admin_guard import lock_admin_set
from ..identity import provisioning as identity_provisioning
from ..identity.provisioning import CredentiallessIdentity, IdentityProfile, IdentityUsernameExists
from ..keycloak_provisioning import (
    KeycloakConflict,
    KeycloakNotConfigured,
    KeycloakRejected,
    KeycloakUnavailable,
    UserLookup,
)
from .bootstrap import verify_secret
from .service import SYSTEM_ADMIN_ROLE, _check_rate_limit, _record_failure, _reset_failures

logger = logging.getLogger("easysynq.setup")

_DUMMY_BOOTSTRAP_HASH = (
    "00000000000000000000000000000000:"
    "0000000000000000000000000000000000000000000000000000000000000000"
)
_DUMMY_RECEIPT_DIGEST = "0" * 64

ALLOWED_BOOTSTRAP_AFTER_KEYS = {
    EventType.BOOTSTRAP_IDENTITY_CLAIMED: {"username"},
    EventType.USER_CREATED: {"status", "email", "provisioning"},
    EventType.ADMIN_BOOTSTRAPPED: {"role"},
    EventType.USER_CREDENTIAL_ISSUED: {"credential_issued"},
    EventType.BOOTSTRAP_CONSUMED: set(),
}


@dataclasses.dataclass(frozen=True, slots=True)
class FirstAdministratorProfile:
    username: str
    display_name: str
    email: str | None
    first_name: str | None
    last_name: str | None

    def normalized(self) -> FirstAdministratorProfile:
        username = identity_provisioning.canonicalize_username(self.username)
        display_name = self.display_name.strip()
        if not username or not display_name:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="username and display_name must not be empty",
            )

        def optional(value: str | None) -> str | None:
            if value is None:
                return None
            normalized = value.strip()
            return normalized or None

        return FirstAdministratorProfile(
            username=username,
            display_name=display_name,
            email=optional(self.email),
            first_name=optional(self.first_name),
            last_name=optional(self.last_name),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class FirstAdministratorProvisioned:
    admin_user_id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    temporary_password: str
    credential_receipt: str
    created: bool


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _request_id() -> uuid.UUID | None:
    value = request_id_var.get()
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _new_credential_receipt() -> tuple[str, str]:
    receipt = secrets.token_urlsafe(32)
    return receipt, hashlib.sha256(receipt.encode("utf-8")).hexdigest()


def _receipt_matches(receipt: str, stored_digest: str | None) -> bool:
    supplied = hashlib.sha256(receipt.encode("utf-8")).hexdigest()
    return hmac.compare_digest(supplied, stored_digest or _DUMMY_RECEIPT_DIGEST)


def _bootstrap_audit_event(
    *,
    org_id: uuid.UUID,
    event_type: EventType,
    object_type: AuditObjectType,
    object_id: uuid.UUID,
    after: dict[str, Any] | None,
) -> AuditEvent:
    allowed = ALLOWED_BOOTSTRAP_AFTER_KEYS[event_type]
    supplied = set(after or {})
    if not supplied <= allowed:
        raise ValueError(f"bootstrap audit payload keys {supplied - allowed!r} are not allowed")
    return AuditEvent(
        org_id=org_id,
        occurred_at=_now(),
        actor_id=None,
        actor_type=ActorType.system,
        event_type=event_type,
        object_type=object_type,
        object_id=object_id,
        after=after,
        request_id=_request_id(),
    )


def _public_summary(user: AppUser, *, username: str) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "username": username,
        "display_name": user.display_name or username,
        "email": user.email,
        "status": user.status.value,
    }


def _require_bound_username(bound_username: str, requested_username: str) -> None:
    if bound_username != requested_username:
        raise ProblemException(
            status=409,
            code="bootstrap_identity_bound",
            title="A different first-administrator username is already bound",
            members={"bound_username": bound_username},
        )


def _verify_bootstrap_proof(
    cfg: SystemConfig | Any,
    secret: str,
    *,
    now: datetime.datetime,
    allow_expired_consumed_replay: bool = False,
) -> None:
    stored_hash = cfg.bootstrap_secret_hash
    matches = verify_secret(secret, stored_hash or _DUMMY_BOOTSTRAP_HASH)
    if stored_hash is None or not matches:
        raise ProblemException(
            status=403,
            code="bootstrap_invalid",
            title="Invalid bootstrap secret",
        )

    expired = cfg.bootstrap_expires_at is not None and now > cfg.bootstrap_expires_at
    consumed_replay = (
        allow_expired_consumed_replay
        and cfg.setup_state is SetupState.IN_SETUP
        and cfg.bootstrap_consumed_at is not None
    )
    if expired and not consumed_replay:
        raise ProblemException(
            status=403,
            code="bootstrap_invalid",
            title="Invalid bootstrap secret",
        )


def _validate_bootstrap_secret(
    cfg: SystemConfig | Any, secret: str, *, now: datetime.datetime
) -> None:
    """Verify proof before disclosing that setup has advanced."""
    _verify_bootstrap_proof(cfg, secret, now=now)
    if cfg.setup_state is not SetupState.UNINITIALIZED or cfg.bootstrap_consumed_at is not None:
        raise ProblemException(
            status=409,
            code="setup_already_complete",
            title="Setup has already advanced beyond administrator provisioning",
        )


async def _locked_singleton(session: AsyncSession) -> SystemConfig:
    statement = select(SystemConfig).with_for_update().execution_options(populate_existing=True)
    rows = (await session.execute(statement)).scalars().all()
    if len(rows) != 1:
        raise ProblemException(
            status=409,
            code="setup_not_initialized",
            title="The setup configuration singleton is unavailable",
        )
    return rows[0]


async def _assert_only_claim_administrator(session: AsyncSession, cfg: SystemConfig) -> None:
    ids = set(
        (
            await session.scalars(
                select(RoleAssignment.user_id)
                .join(Role, Role.id == RoleAssignment.role_id)
                .where(
                    RoleAssignment.org_id == cfg.org_id,
                    Role.name == SYSTEM_ADMIN_ROLE,
                )
            )
        ).all()
    )
    allowed = {cfg.bootstrap_admin_user_id} if cfg.bootstrap_admin_user_id is not None else set()
    if ids - allowed or (ids and not allowed):
        raise ProblemException(
            status=409,
            code="bootstrap_administrator_exists",
            title="An administrator already exists outside this bootstrap claim",
        )


async def _validate_request_proof(cfg: SystemConfig, secret: str) -> None:
    try:
        _validate_bootstrap_secret(cfg, secret, now=_now())
    except ProblemException as exc:
        if exc.code == "bootstrap_invalid":
            await _record_failure()
        raise


async def _validate_acknowledgment_proof(cfg: SystemConfig, secret: str) -> None:
    try:
        _verify_bootstrap_proof(
            cfg,
            secret,
            now=_now(),
            allow_expired_consumed_replay=True,
        )
    except ProblemException as exc:
        if exc.code == "bootstrap_invalid":
            await _record_failure()
        raise


def _claim_fields_are_complete(cfg: SystemConfig) -> bool:
    return (
        cfg.bootstrap_admin_claim_id is not None
        and cfg.bootstrap_admin_username is not None
        and cfg.bootstrap_claimed_at is not None
    )


async def _establish_claim(
    session: AsyncSession, *, secret: str, username: str
) -> tuple[uuid.UUID, uuid.UUID]:
    cfg = await _locked_singleton(session)
    await _check_rate_limit()
    await _validate_request_proof(cfg, secret)
    await lock_admin_set(session, cfg.org_id)
    await _assert_only_claim_administrator(session, cfg)
    if cfg.bootstrap_admin_claim_id is not None:
        if not _claim_fields_are_complete(cfg):
            raise ProblemException(
                status=409,
                code="bootstrap_not_ready",
                title="The first-administrator claim is incomplete",
            )
        bound_username = cfg.bootstrap_admin_username
        if bound_username is None:  # Kept explicit so optimized Python cannot remove the guard.
            raise ProblemException(
                status=409,
                code="bootstrap_not_ready",
                title="The first-administrator claim is incomplete",
            )
        _require_bound_username(bound_username, username)
        claim_id = cfg.bootstrap_admin_claim_id
        org_id = cfg.org_id
        await session.commit()
        return claim_id, org_id

    if any(
        value is not None
        for value in (
            cfg.bootstrap_admin_username,
            cfg.bootstrap_admin_user_id,
            cfg.bootstrap_claimed_at,
            cfg.bootstrap_credential_issued_at,
        )
    ):
        raise ProblemException(
            status=409,
            code="bootstrap_not_ready",
            title="The first-administrator claim is inconsistent",
        )

    claim_id = uuid.uuid4()
    cfg.bootstrap_admin_claim_id = claim_id
    cfg.bootstrap_admin_username = username
    cfg.bootstrap_claimed_at = _now()
    session.add(
        _bootstrap_audit_event(
            org_id=cfg.org_id,
            event_type=EventType.BOOTSTRAP_IDENTITY_CLAIMED,
            object_type=AuditObjectType.config,
            object_id=cfg.org_id,
            after={"username": username},
        )
    )
    org_id = cfg.org_id
    await session.commit()
    return claim_id, org_id


def _well_formed_marker(marker: str) -> bool:
    try:
        uuid.UUID(marker)
    except ValueError:
        return False
    return True


async def _release_unowned_claim(
    session: AsyncSession,
    *,
    cfg: SystemConfig,
    claim_id: uuid.UUID,
    username: str,
) -> bool:
    # The caller retains the singleton row and admin-set advisory lock from before the initial
    # Keycloak read. Re-check the admin set at the release decision even though supported writers
    # cannot pass the advisory lock; an unsupported/direct mutation therefore fails closed too.
    await _assert_only_claim_administrator(session, cfg)
    can_release = (
        cfg.setup_state is SetupState.UNINITIALIZED
        and cfg.bootstrap_consumed_at is None
        and cfg.bootstrap_admin_claim_id == claim_id
        and cfg.bootstrap_admin_username == username
        and cfg.bootstrap_admin_user_id is None
        and cfg.bootstrap_credential_issued_at is None
    )
    if can_release:
        cfg.bootstrap_admin_claim_id = None
        cfg.bootstrap_admin_username = None
        cfg.bootstrap_claimed_at = None
    await session.commit()
    return can_release


def _unrelated_lookup(lookup: UserLookup, *, claim_id: uuid.UUID) -> bool:
    if not lookup.found:
        return False
    marker = lookup.bootstrap_claim_id
    if marker is None:
        return True
    if not _well_formed_marker(marker):
        raise ProblemException(
            status=502,
            code="keycloak_unavailable",
            title="The identity provider returned unusable bootstrap state",
        )
    return marker != str(claim_id)


async def _resolve_identity(
    session: AsyncSession,
    *,
    profile: FirstAdministratorProfile,
    claim_id: uuid.UUID,
    cfg: SystemConfig,
) -> tuple[CredentiallessIdentity, Any]:
    client = identity_provisioning.keycloak_client()
    try:
        await client.__aenter__()
        try:
            # Bootstrap performs a collision-classification lookup before it delegates to the
            # shared create/adopt helper, so reconcile here before that very first identity read.
            # The shared helper deliberately repeats this idempotent GET to retain its invariant
            # for every ordinary and bootstrap caller; do not add a bypass flag at this boundary.
            await client.ensure_optional_user_profile_fields()
            initial = await client.find_user_by_username(profile.username)
            if _unrelated_lookup(initial, claim_id=claim_id):
                raise ProblemException(
                    status=409,
                    code="user_exists",
                    title="The requested username already exists",
                )
            try:
                identity = await identity_provisioning.ensure_credentialless_identity(
                    client,
                    IdentityProfile(
                        username=profile.username,
                        email=profile.email,
                        first_name=profile.first_name,
                        last_name=profile.last_name,
                    ),
                    bootstrap_claim_id=claim_id,
                    allow_matching_claim=True,
                )
            except KeycloakRejected:
                if initial.found is False:
                    final = await client.find_user_by_username(profile.username)
                    if final.found:
                        # A found identity (matching marker or otherwise) makes the original
                        # absence observation stale. Validate marker shape, retain the claim, and
                        # report the original definitive profile rejection.
                        _unrelated_lookup(final, claim_id=claim_id)
                    else:
                        await _release_unowned_claim(
                            session,
                            cfg=cfg,
                            claim_id=claim_id,
                            username=profile.username,
                        )
                raise
            except IdentityUsernameExists as exc:
                lookup = await client.find_user_by_username(profile.username)
                if not lookup.found:
                    raise KeycloakUnavailable(
                        "Keycloak username conflict could not be resolved"
                    ) from exc
                if _unrelated_lookup(lookup, claim_id=claim_id):
                    raise ProblemException(
                        status=409,
                        code="user_exists",
                        title="The requested username already exists",
                    ) from None
                identity = await identity_provisioning.ensure_credentialless_identity(
                    client,
                    IdentityProfile(
                        username=profile.username,
                        email=profile.email,
                        first_name=profile.first_name,
                        last_name=profile.last_name,
                    ),
                    bootstrap_claim_id=claim_id,
                    allow_matching_claim=True,
                )
            except KeycloakConflict as exc:
                if exc.field != "email":
                    raise
                lookup = await client.find_user_by_username(profile.username)
                if lookup.found:
                    if _unrelated_lookup(lookup, claim_id=claim_id):
                        raise ProblemException(
                            status=409,
                            code="user_exists",
                            title="The requested username already exists",
                        ) from None
                    identity = await identity_provisioning.ensure_credentialless_identity(
                        client,
                        IdentityProfile(
                            username=profile.username,
                            email=profile.email,
                            first_name=profile.first_name,
                            last_name=profile.last_name,
                        ),
                        bootstrap_claim_id=claim_id,
                        allow_matching_claim=True,
                    )
                else:
                    raise ProblemException(
                        status=409,
                        code="keycloak_email_exists",
                        title="The requested email already exists",
                    ) from None
        except Exception:
            await client.__aexit__(None, None, None)
            raise
    except ProblemException:
        raise
    except KeycloakNotConfigured as exc:
        raise ProblemException(
            status=503,
            code="keycloak_not_configured",
            title="Identity provisioning is not configured",
        ) from exc
    except KeycloakRejected as exc:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="The identity provider rejected the administrator profile",
        ) from exc
    except (KeycloakUnavailable, KeycloakConflict) as exc:
        raise ProblemException(
            status=502,
            code="keycloak_unavailable",
            title="The identity provider is unavailable",
        ) from exc
    return identity, client


def _assert_claim(cfg: SystemConfig, *, claim_id: uuid.UUID, username: str) -> None:
    if (
        cfg.setup_state is not SetupState.UNINITIALIZED
        or cfg.bootstrap_consumed_at is not None
        or cfg.bootstrap_admin_claim_id != claim_id
        or cfg.bootstrap_admin_username != username
        or cfg.bootstrap_claimed_at is None
    ):
        raise ProblemException(
            status=409,
            code="bootstrap_not_ready",
            title="The first-administrator claim no longer matches",
        )


async def _persist_user_and_role(
    session: AsyncSession,
    *,
    claim_id: uuid.UUID,
    org_id: uuid.UUID,
    profile: FirstAdministratorProfile,
    identity: CredentiallessIdentity,
    client: Any,
    cfg: SystemConfig,
) -> tuple[AppUser, dict[str, Any]]:
    _assert_claim(cfg, claim_id=claim_id, username=profile.username)
    await _assert_only_claim_administrator(session, cfg)
    if cfg.org_id != org_id:
        raise ProblemException(
            status=409,
            code="bootstrap_not_ready",
            title="The setup organization changed",
        )

    try:
        await identity_provisioning.reconcile_claimed_identity_profile(
            client,
            IdentityProfile(
                username=profile.username,
                email=profile.email,
                first_name=profile.first_name,
                last_name=profile.last_name,
            ),
            subject=identity.subject,
            bootstrap_claim_id=claim_id,
        )
    except KeycloakRejected:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="The identity provider rejected the administrator profile",
        ) from None
    except KeycloakUnavailable:
        raise ProblemException(
            status=502,
            code="keycloak_unavailable",
            title="The identity provider is unavailable",
        ) from None

    created_user = False
    if cfg.bootstrap_admin_user_id is not None:
        user = await session.get(AppUser, cfg.bootstrap_admin_user_id)
        if (
            user is None
            or user.org_id != org_id
            or user.keycloak_subject != identity.subject
            or user.status is not UserStatus.INVITED
        ):
            raise ProblemException(
                status=409,
                code="bootstrap_not_ready",
                title="The linked first administrator no longer matches",
            )
    else:
        user = await session.scalar(
            select(AppUser).where(AppUser.keycloak_subject == identity.subject)
        )
        if user is None:
            user = AppUser(
                org_id=org_id,
                keycloak_subject=identity.subject,
                display_name=profile.display_name,
                email=profile.email,
                status=UserStatus.INVITED,
            )
            session.add(user)
            await session.flush()
            created_user = True
        elif user.org_id != org_id or user.status is not UserStatus.INVITED:
            raise ProblemException(
                status=409,
                code="bootstrap_not_ready",
                title="The recovered first administrator user is not eligible",
            )

    user.display_name = profile.display_name
    user.email = profile.email

    role = await session.scalar(
        select(Role).where(Role.org_id == org_id, Role.name == SYSTEM_ADMIN_ROLE)
    )
    if role is None:
        raise ProblemException(
            status=500,
            code="role_missing",
            title="System Administrator role is not seeded",
        )
    assignment = await session.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.org_id == org_id,
            RoleAssignment.user_id == user.id,
            RoleAssignment.role_id == role.id,
        )
    )
    created_assignment = assignment is None
    if created_assignment:
        session.add(
            RoleAssignment(
                org_id=org_id,
                user_id=user.id,
                role_id=role.id,
                bound_scope=None,
            )
        )

    cfg.bootstrap_admin_user_id = user.id
    if created_user:
        session.add(
            _bootstrap_audit_event(
                org_id=org_id,
                event_type=EventType.USER_CREATED,
                object_type=AuditObjectType.user,
                object_id=user.id,
                after={
                    "status": UserStatus.INVITED.value,
                    "email": user.email,
                    "provisioning": "keycloak_created",
                },
            )
        )
    if created_assignment:
        session.add(
            _bootstrap_audit_event(
                org_id=org_id,
                event_type=EventType.ADMIN_BOOTSTRAPPED,
                object_type=AuditObjectType.user,
                object_id=user.id,
                after={"role": SYSTEM_ADMIN_ROLE},
            )
        )

    summary = _public_summary(user, username=profile.username)
    await session.commit()
    return user, summary


async def _lock_claim_identity_stage(
    session: AsyncSession,
    *,
    claim_id: uuid.UUID,
    org_id: uuid.UUID,
    username: str,
) -> SystemConfig:
    """Enter the bootstrap identity/persistence serialization boundary in canonical order."""
    cfg = await _locked_singleton(session)
    _assert_claim(cfg, claim_id=claim_id, username=username)
    if cfg.org_id != org_id:
        raise ProblemException(
            status=409,
            code="bootstrap_not_ready",
            title="The setup organization changed",
        )
    await lock_admin_set(session, org_id)
    await _assert_only_claim_administrator(session, cfg)
    return cfg


async def _issue_and_record_credential(
    session: AsyncSession,
    *,
    claim_id: uuid.UUID,
    username: str,
    user_id: uuid.UUID,
    identity: CredentiallessIdentity,
    client: Any,
) -> tuple[str, str]:
    while True:
        cfg = await _locked_singleton(session)
        _assert_claim(cfg, claim_id=claim_id, username=username)
        await lock_admin_set(session, cfg.org_id)
        await _assert_only_claim_administrator(session, cfg)
        if cfg.bootstrap_admin_user_id != user_id:
            raise ProblemException(
                status=409,
                code="bootstrap_not_ready",
                title="The linked first administrator no longer matches",
            )
        if cfg.bootstrap_credential_receipt_hash is None:
            break

        # A password reset is irreversible relative to PostgreSQL. Publish a durable pending
        # generation before touching Keycloak so rollback after the reset cannot resurrect the
        # prior receipt for an inactive password. Retaining issued_at distinguishes recovery from
        # the never-issued initial state; acknowledgment still performs the constant-time receipt
        # comparison against NULL and reports the generation as superseded.
        cfg.bootstrap_credential_receipt_hash = None
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise ProblemException(
                status=503,
                code="dependency_unavailable",
                title="Required bootstrap state could not be persisted",
            ) from exc
        # The commit releases both locks. Reacquire in canonical order and revalidate every
        # invariant; a concurrent issuer may have promoted another active generation meanwhile.

    # The network call stays inside this row-lock boundary so acknowledgment and remint cannot
    # pass the issuance generation. See docs/debt/20260815215020-bootstrap-credential-lock.md.
    try:
        password = await identity_provisioning.issue_temporary_credential(
            client, subject=identity.subject, username=username
        )
    except KeycloakUnavailable as exc:
        await session.rollback()
        raise ProblemException(
            status=502,
            code="keycloak_unavailable",
            title="The identity provider is unavailable",
        ) from exc

    receipt, receipt_digest = _new_credential_receipt()
    cfg.bootstrap_credential_issued_at = _now()
    cfg.bootstrap_credential_receipt_hash = receipt_digest
    try:
        await session.flush()
    except Exception as exc:
        await session.rollback()
        raise ProblemException(
            status=503,
            code="dependency_unavailable",
            title="Required bootstrap state could not be persisted",
        ) from exc

    try:
        async with session.begin_nested():
            session.add(
                _bootstrap_audit_event(
                    org_id=cfg.org_id,
                    event_type=EventType.USER_CREDENTIAL_ISSUED,
                    object_type=AuditObjectType.user,
                    object_id=user_id,
                    after={"credential_issued": True},
                )
            )
            await session.flush()
    except Exception:  # noqa: BLE001 — R64 permits only this isolated audit under-claim.
        logger.warning("setup.first_administrator_credential_audit_failed user_id=%s", str(user_id))

    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise ProblemException(
            status=503,
            code="dependency_unavailable",
            title="Required bootstrap state could not be persisted",
        ) from exc
    return password, receipt


async def provision_first_administrator(
    session: AsyncSession,
    *,
    secret: str,
    profile: FirstAdministratorProfile,
) -> FirstAdministratorProvisioned:
    profile = profile.normalized()
    await _check_rate_limit()
    try:
        claim_id, org_id = await _establish_claim(session, secret=secret, username=profile.username)
        cfg = await _lock_claim_identity_stage(
            session,
            claim_id=claim_id,
            org_id=org_id,
            username=profile.username,
        )
        identity, client = await _resolve_identity(
            session,
            profile=profile,
            claim_id=claim_id,
            cfg=cfg,
        )
    except Exception:
        # Every exception ends the transaction explicitly: singleton/advisory locks must never
        # depend on request-session cleanup after a provider failure.
        await session.rollback()
        raise
    try:
        try:
            _user, summary = await _persist_user_and_role(
                session,
                claim_id=claim_id,
                org_id=org_id,
                profile=profile,
                identity=identity,
                client=client,
                cfg=cfg,
            )
        except Exception:
            await session.rollback()
            raise
        # Capture all response values before the irreversible credential side effect.
        user_id = uuid.UUID(summary["id"])
        display_name = str(summary["display_name"])
        email = summary["email"]
        password, credential_receipt = await _issue_and_record_credential(
            session,
            claim_id=claim_id,
            username=profile.username,
            user_id=user_id,
            identity=identity,
            client=client,
        )
        await _reset_failures()
        return FirstAdministratorProvisioned(
            admin_user_id=user_id,
            username=profile.username,
            display_name=display_name,
            email=email,
            temporary_password=password,
            credential_receipt=credential_receipt,
            created=identity.created,
        )
    except Exception:
        await session.rollback()
        raise
    finally:
        await client.__aexit__(None, None, None)


async def _admin_assignment_exists(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    assignment = await session.scalar(
        select(RoleAssignment.id)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(
            RoleAssignment.org_id == org_id,
            RoleAssignment.user_id == user_id,
            Role.name == SYSTEM_ADMIN_ROLE,
        )
    )
    return assignment is not None


async def _acknowledge_first_administrator(
    session: AsyncSession, *, secret: str, credential_receipt: str
) -> dict[str, str]:
    await _check_rate_limit()
    cfg = await _locked_singleton(session)
    await _check_rate_limit()
    await _validate_acknowledgment_proof(cfg, secret)
    await lock_admin_set(session, cfg.org_id)

    admin_user_id = cfg.bootstrap_admin_user_id
    complete = (
        _claim_fields_are_complete(cfg)
        and admin_user_id is not None
        and cfg.bootstrap_credential_issued_at is not None
    )
    if cfg.setup_state is SetupState.IN_SETUP and cfg.bootstrap_consumed_at is not None:
        if not complete or admin_user_id is None:
            raise ProblemException(
                status=409,
                code="bootstrap_not_ready",
                title="The consumed first-administrator claim is incomplete",
            )
        if not _receipt_matches(credential_receipt, cfg.bootstrap_credential_receipt_hash):
            raise ProblemException(
                status=409,
                code="bootstrap_credential_superseded",
                title="The acknowledged credential generation is no longer active",
            )
        await _assert_only_claim_administrator(session, cfg)
        if not await _admin_assignment_exists(session, org_id=cfg.org_id, user_id=admin_user_id):
            raise ProblemException(
                status=409,
                code="bootstrap_not_ready",
                title="The consumed first-administrator assignment is missing",
            )
        await session.commit()
        await _reset_failures()
        return {"setup_state": SetupState.IN_SETUP.value, "admin_user_id": str(admin_user_id)}

    if cfg.setup_state is not SetupState.UNINITIALIZED or cfg.bootstrap_consumed_at is not None:
        raise ProblemException(
            status=409,
            code="setup_already_complete",
            title="Setup has already advanced beyond administrator provisioning",
        )
    if not complete or admin_user_id is None:
        raise ProblemException(
            status=409,
            code="bootstrap_not_ready",
            title="The first administrator is not ready for acknowledgment",
        )
    if not _receipt_matches(credential_receipt, cfg.bootstrap_credential_receipt_hash):
        raise ProblemException(
            status=409,
            code="bootstrap_credential_superseded",
            title="The acknowledged credential generation is no longer active",
        )
    await _assert_only_claim_administrator(session, cfg)
    if not await _admin_assignment_exists(session, org_id=cfg.org_id, user_id=admin_user_id):
        raise ProblemException(
            status=409,
            code="bootstrap_not_ready",
            title="The first-administrator assignment is missing",
        )

    cfg.bootstrap_consumed_at = _now()
    cfg.setup_state = SetupState.IN_SETUP
    session.add(
        _bootstrap_audit_event(
            org_id=cfg.org_id,
            event_type=EventType.BOOTSTRAP_CONSUMED,
            object_type=AuditObjectType.config,
            object_id=cfg.org_id,
            after=None,
        )
    )
    await session.commit()
    await _reset_failures()
    return {"setup_state": SetupState.IN_SETUP.value, "admin_user_id": str(admin_user_id)}


async def acknowledge_first_administrator(
    session: AsyncSession, *, secret: str, credential_receipt: str
) -> dict[str, str]:
    try:
        return await _acknowledge_first_administrator(
            session,
            secret=secret,
            credential_receipt=credential_receipt,
        )
    except Exception:
        await session.rollback()
        raise
