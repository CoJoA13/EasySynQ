"""S8a integration proofs — the setup latch + bootstrap-of-trust + org profile + finalize
(testcontainers PG/MinIO/Redis).

The conftest defaults the shared DB to OPERATIONAL; each test here resets the singleton to a clean
UNINITIALIZED state (fresh bootstrap secret, no admin, placeholder org, cleared rate-limit) so the
first-run flow is deterministic regardless of order.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
import redis.asyncio as aioredis
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.backup_policy import BackupPolicy
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.storage_config import StorageConfig
from easysynq_api.db.models.system_config import SetupState, SystemConfig
from easysynq_api.db.models.working_calendar import WorkingCalendar
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.problems import ProblemException
from easysynq_api.services import backup as backup_service
from easysynq_api.services.identity import provisioning as identity_provisioning
from easysynq_api.services.keycloak_provisioning import (
    KeycloakConflict,
    KeycloakRejected,
    KeycloakUnavailable,
    UserLookup,
)
from easysynq_api.services.setup import service as setup_service
from easysynq_api.services.setup.bootstrap import mint_secret
from easysynq_api.services.vault import storage

from .test_vault import _auth

pytestmark = pytest.mark.integration

_ADMIN = "System Administrator"


def _sub(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


@dataclass(slots=True)
class _FakeIdentity:
    subject: str
    marker: str | None
    email: str | None
    first_name: str | None
    last_name: str | None
    unrelated_fields: dict[str, object] = field(
        default_factory=lambda: {
            "requiredActions": ["UPDATE_PASSWORD"],
            "federationLink": "directory-provider",
            "employeeId": ["E-100"],
        }
    )


@dataclass(slots=True)
class _PasswordResetBarrier:
    entered: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    active: int = 0
    max_active: int = 0


@dataclass(slots=True)
class _CreateRejectionBarrier:
    entered: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class _FakeKeycloak:
    accounts: dict[str, _FakeIdentity] = field(default_factory=dict)
    passwords: dict[str, list[str]] = field(default_factory=dict)
    operations: list[str] = field(default_factory=list)
    lookup_error: Exception | None = None
    fail_after_create: bool = False
    password_error: Exception | None = None
    password_barrier: _PasswordResetBarrier | None = None
    rejection_detail_template: str | None = None
    rejection_barrier: _CreateRejectionBarrier | None = None
    rejection_revalidation: UserLookup | Exception | None = None
    create_rejected: bool = False
    profile_rejection_detail_template: str | None = None

    async def __aenter__(self) -> _FakeKeycloak:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def ensure_optional_user_profile_fields(self) -> None:
        self.operations.append("profile")

    async def find_user_by_username(self, username: str) -> UserLookup:
        self.operations.append("lookup")
        if self.lookup_error is not None:
            raise self.lookup_error
        if self.create_rejected and self.rejection_revalidation is not None:
            if isinstance(self.rejection_revalidation, Exception):
                raise self.rejection_revalidation
            return self.rejection_revalidation
        account = self.accounts.get(username)
        if account is None:
            return UserLookup(found=False)
        return UserLookup(True, account.subject, account.marker)

    async def create_user(
        self,
        *,
        username: str,
        email: str | None,
        first_name: str | None,
        last_name: str | None,
        bootstrap_claim_id: uuid.UUID | None = None,
    ) -> str:
        self.operations.append("create")
        if username in self.accounts:
            account = self.accounts[username]
            raise KeycloakConflict("username", "duplicate", keycloak_subject=account.subject)
        if email is not None and any(account.email == email for account in self.accounts.values()):
            raise KeycloakConflict("email", "duplicate")
        rejection_detail_template = self.rejection_detail_template
        if rejection_detail_template is not None:
            if self.rejection_barrier is not None:
                await self.rejection_barrier.entered.put(username)
                await self.rejection_barrier.release.wait()
            self.create_rejected = True
            raise KeycloakRejected(
                rejection_detail_template.format(
                    claim=bootstrap_claim_id,
                    username=username,
                    email=email,
                )
            )
        subject = f"subject:{username}"
        self.accounts[username] = _FakeIdentity(
            subject=subject,
            marker=str(bootstrap_claim_id) if bootstrap_claim_id is not None else None,
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
        if self.fail_after_create:
            raise KeycloakUnavailable("create result was uncertain")
        return subject

    async def reconcile_claimed_user_profile(
        self,
        *,
        subject: str,
        username: str,
        bootstrap_claim_id: uuid.UUID,
        email: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        self.operations.append("reconcile")
        account = self.accounts.get(username)
        if (
            account is None
            or account.subject != subject
            or account.marker != str(bootstrap_claim_id)
        ):
            raise KeycloakUnavailable("claimed identity ownership could not be established")
        rejection = self.profile_rejection_detail_template
        if rejection is not None:
            raise KeycloakRejected(
                rejection.format(
                    subject=subject,
                    claim=bootstrap_claim_id,
                    username=username,
                    email=email,
                )
            )
        account.email = email
        account.first_name = first_name
        account.last_name = last_name

    async def set_temporary_password(self, *, subject: str, password: str) -> None:
        if self.password_error is not None:
            raise self.password_error
        if self.password_barrier is not None:
            barrier = self.password_barrier
            barrier.active += 1
            barrier.max_active = max(barrier.max_active, barrier.active)
            await barrier.entered.put(subject)
            try:
                await barrier.release.wait()
            finally:
                barrier.active -= 1
        self.passwords.setdefault(subject, []).append(password)

    def add_account(
        self,
        username: str,
        *,
        marker: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> str:
        subject = f"unrelated:{username}"
        self.accounts[username] = _FakeIdentity(
            subject,
            marker,
            email,
            first_name,
            last_name,
        )
        return subject


@pytest.fixture(autouse=True)
def _setup_keycloak(
    monkeypatch: pytest.MonkeyPatch,
) -> _FakeKeycloak:
    fake = _FakeKeycloak()
    monkeypatch.setattr(identity_provisioning, "keycloak_client", lambda: fake)
    return fake


async def _reset_uninitialized() -> str:
    """Reset the singleton install to a clean UNINITIALIZED state with a fresh secret; return it."""
    secret, stored = mint_secret()
    async with get_sessionmaker()() as s:
        cfg = (await s.execute(select(SystemConfig))).scalar_one()
        cfg.setup_state = SetupState.UNINITIALIZED
        cfg.finalized_at = None
        cfg.bootstrap_consumed_at = None
        cfg.bootstrap_secret_hash = stored
        cfg.bootstrap_expires_at = setup_service._now() + datetime.timedelta(hours=1)
        cfg.bootstrap_admin_claim_id = None
        cfg.bootstrap_admin_username = None
        cfg.bootstrap_admin_user_id = None
        cfg.bootstrap_claimed_at = None
        cfg.bootstrap_credential_issued_at = None
        cfg.bootstrap_credential_receipt_hash = None
        cfg.auth_method = None  # reset G-D (S8c) so it starts unsatisfied
        cfg.auth_test_login_ok = None
        cfg.auth_test_login_at = None
        await s.execute(
            delete(RoleAssignment).where(
                RoleAssignment.role_id.in_(select(Role.id).where(Role.name == _ADMIN))
            )
        )
        await s.execute(delete(StorageConfig))  # reset G-B (S8b) so it starts unsatisfied
        await s.execute(delete(BackupPolicy))  # reset G-C (S8b2) so it starts unsatisfied
        org = (await s.execute(select(Organization))).scalar_one()
        org.short_code = "DEFAULT"
        org.legal_name = "EasySynQ (configure in setup)"
        await s.commit()
    async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as r:
        await r.delete(setup_service._RL_KEY)
    return secret


async def _pass_restore_gate(result: str = "PASS") -> None:
    """Satisfy (or fail) gate G-C directly by persisting a backup_policy result — for tests whose
    subject is the latch/other gates, NOT the drill. The real drill→PASS path is proven in
    ``test_setup_finalize_requires_restore_pass`` (this session) without this shortcut."""
    async with get_sessionmaker()() as s:
        org_id = (await s.execute(select(Organization.id))).scalar_one()
        policy = await s.scalar(select(BackupPolicy).where(BackupPolicy.org_id == org_id))
        if policy is None:
            policy = BackupPolicy(
                org_id=org_id, destination=tempfile.gettempdir(), cron="0 2 * * *"
            )
            s.add(policy)
        policy.last_restore_test_at = setup_service._now()
        policy.last_restore_test_result = result
        await s.commit()


async def _pass_auth_gate() -> None:
    """Satisfy gate G-D directly by persisting the auth attestation — for tests whose subject is a
    different gate. The real configure-auth→proof path is proven in
    ``test_setup_finalize_requires_auth_proven`` without this shortcut."""
    async with get_sessionmaker()() as s:
        cfg = (await s.execute(select(SystemConfig))).scalar_one()
        cfg.auth_method = "LOCAL"
        cfg.auth_test_login_ok = True
        cfg.auth_test_login_at = setup_service._now()
        await s.commit()


async def _provision(
    client: AsyncClient,
    secret: str,
    username: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
    first_name: str | None = "First",
    last_name: str | None = "Administrator",
) -> Any:
    return await client.post(
        "/api/v1/setup/administrator",
        json={
            "secret": secret,
            "username": username,
            "display_name": display_name or f"Administrator {username}",
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        },
    )


async def _bootstrap(
    client: AsyncClient,
    token_factory: Callable[..., str],
    secret: str,
    prefix: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    username = _sub(prefix)
    provision = await _provision(client, secret, username)
    assert provision.status_code in {200, 201}, provision.text
    provisioned = provision.json()
    assert provisioned["temporary_password"]
    acknowledged = await client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={
            "secret": secret,
            "credential_receipt": provisioned["credential_receipt"],
        },
    )
    assert acknowledged.status_code == 200, acknowledged.text
    body = {**provisioned, **acknowledged.json()}
    return _auth(token_factory, f"subject:{username}"), body


async def _config() -> SystemConfig:
    async with get_sessionmaker()() as session:
        return (await session.execute(select(SystemConfig))).scalar_one()


async def _establish_claim_only(
    client: AsyncClient,
    keycloak: _FakeKeycloak,
    *,
    secret: str,
    username: str,
) -> None:
    keycloak.lookup_error = KeycloakUnavailable("claim-only lookup outage")
    try:
        response = await _provision(client, secret, username)
    finally:
        keycloak.lookup_error = None
    assert response.status_code == 502, response.text
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_username == username
    assert cfg.bootstrap_admin_user_id is None


async def _verify_storage(client: AsyncClient, h: dict[str, str], mode: str = "GOVERNANCE") -> dict:
    r = await client.post(
        "/api/v1/setup/verify-storage", headers=h, json={"object_lock_mode": mode}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_latch_blocks_qms_until_operational(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[HEADLINE] While UNINITIALIZED the QMS surface is 423 setup_incomplete, but the public
    /setup/state and the health probes stay reachable (so the wizard + ops can run)."""
    await _reset_uninitialized()
    h = _auth(token_factory, _sub("u"))

    locked = await app_client.get("/api/v1/documents", headers=h)
    assert locked.status_code == 423
    assert locked.json()["code"] == "setup_incomplete"

    state = await app_client.get("/api/v1/setup/state")  # public, latch-exempt
    assert state.status_code == 200
    assert state.json()["setup_state"] == "UNINITIALIZED"
    assert (await app_client.get("/healthz")).status_code == 200


async def test_bootstrap_grants_first_admin_and_audits(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The public secret flow creates, grants, then acknowledges the first administrator."""
    secret = await _reset_uninitialized()

    h, body = await _bootstrap(app_client, token_factory, secret, "admin")
    assert body["setup_state"] == "IN_SETUP"
    admin_id = uuid.UUID(body["admin_user_id"])
    org_id = await _org_id()

    async with get_sessionmaker()() as s:
        assigned = await s.scalar(
            select(RoleAssignment.id)
            .join(Role, RoleAssignment.role_id == Role.id)
            .join(AppUser, RoleAssignment.user_id == AppUser.id)
            .where(AppUser.id == admin_id, Role.name == _ADMIN)
        )
        assert assigned is not None
        consumed = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.BOOTSTRAP_CONSUMED,
                AuditEvent.object_id == org_id,
            )
        )
        bootstrapped = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.ADMIN_BOOTSTRAPPED,
                AuditEvent.object_id == admin_id,
            )
        )
    assert consumed is not None
    assert bootstrapped is not None
    assert h["Authorization"].startswith("Bearer ")


async def test_setup_detail_requires_config_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The public state probe supports routing; sensitive gate/config detail is admin-only."""
    secret = await _reset_uninitialized()
    other_headers = _auth(token_factory, _sub("setup-detail-other"))

    denied = await app_client.get("/api/v1/setup", headers=other_headers)

    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"

    admin_headers, _ = await _bootstrap(app_client, token_factory, secret, "setup-detail-admin")
    detail = await app_client.get("/api/v1/setup", headers=admin_headers)

    assert detail.status_code == 200
    assert {"gates", "org_profile", "backup", "auth", "tamper_evident"} <= set(detail.json())


async def test_bootstrap_rejects_wrong_secret_and_replay(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("x")

    bad = await _provision(app_client, "wrong", username)
    assert bad.status_code == 403
    assert bad.json()["code"] == "bootstrap_invalid"

    await _bootstrap(app_client, token_factory, secret, "x")  # consumes it
    replay = await _provision(app_client, secret, username)
    assert replay.status_code == 409
    assert replay.json()["code"] == "setup_already_complete"


async def test_first_administrator_credential_receipt_rotates_and_stale_receipt_is_rejected(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("response-loss")

    first = await _provision(app_client, secret, username)
    first_identity_operations = list(_setup_keycloak.operations)
    second = await _provision(app_client, secret, username)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert set(first_body) == {
        "administrator",
        "temporary_password",
        "credential_receipt",
        "password_delivery",
    }
    assert first_body["administrator"]["username"] == username
    assert first_body["administrator"]["status"] == "INVITED"
    assert first_body["password_delivery"] == "shown_once"
    assert first_body["temporary_password"] != second_body["temporary_password"]
    assert first_body["credential_receipt"] != second_body["credential_receipt"]
    assert "keycloak_subject" not in first.text
    assert first_identity_operations == [
        "profile",
        "lookup",
        "profile",
        "lookup",
        "create",
        "reconcile",
    ]
    subject = f"subject:{username}"
    assert _setup_keycloak.passwords[subject] == [
        first_body["temporary_password"],
        second_body["temporary_password"],
    ]
    cfg = await _config()
    assert cfg.setup_state is SetupState.UNINITIALIZED
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_username == username
    assert cfg.bootstrap_admin_user_id == uuid.UUID(first_body["administrator"]["id"])
    assert (
        cfg.bootstrap_credential_receipt_hash
        == hashlib.sha256(second_body["credential_receipt"].encode("utf-8")).hexdigest()
    )
    assert first_body["credential_receipt"] != cfg.bootstrap_credential_receipt_hash
    assert second_body["credential_receipt"] != cfg.bootstrap_credential_receipt_hash

    stale = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={
            "secret": secret,
            "credential_receipt": first_body["credential_receipt"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "bootstrap_credential_superseded"
    assert "credential_receipt" not in stale.json()
    assert (await _config()).setup_state is SetupState.UNINITIALIZED

    current = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={
            "secret": secret,
            "credential_receipt": second_body["credential_receipt"],
        },
    )
    assert current.status_code == 200, current.text


async def test_locked_singleton_refreshes_expire_on_commit_false_identity_map(
    app_client: AsyncClient,
) -> None:
    from easysynq_api.services.setup import administrator as administrator_service

    del app_client
    await _reset_uninitialized()
    async with get_sessionmaker()() as stale_session:
        stale = await administrator_service._locked_singleton(stale_session)
        assert stale.setup_state is SetupState.UNINITIALIZED
        await stale_session.commit()

        async with get_sessionmaker()() as advancing_session:
            current = await administrator_service._locked_singleton(advancing_session)
            current.setup_state = SetupState.IN_SETUP
            current.bootstrap_consumed_at = setup_service._now()
            await advancing_session.commit()

        refreshed = await administrator_service._locked_singleton(stale_session)
        assert refreshed is stale
        assert refreshed.setup_state is SetupState.IN_SETUP
        assert refreshed.bootstrap_consumed_at is not None
        await stale_session.rollback()


async def test_credential_reset_is_serialized_against_acknowledgment(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("credential-fence")
    initial = await _provision(app_client, secret, username)
    assert initial.status_code == 201
    subject = f"subject:{username}"

    barrier = _PasswordResetBarrier()
    _setup_keycloak.password_barrier = barrier
    first_reissue = asyncio.create_task(
        _provision(app_client, secret, username), name="credential-reissue-one"
    )
    second_reissue = asyncio.create_task(
        _provision(app_client, secret, username), name="credential-reissue-two"
    )
    await asyncio.wait_for(barrier.entered.get(), timeout=2)
    acknowledgment = asyncio.create_task(
        app_client.post(
            "/api/v1/setup/administrator/acknowledge",
            json={
                "secret": secret,
                "credential_receipt": initial.json()["credential_receipt"],
            },
        ),
        name="acknowledge-between-resets",
    )

    acknowledgment_was_blocked = False
    try:
        await asyncio.wait_for(asyncio.shield(acknowledgment), timeout=0.25)
    except TimeoutError:
        acknowledgment_was_blocked = True
    finally:
        barrier.release.set()

    acknowledged = await asyncio.wait_for(acknowledgment, timeout=2)
    reissued = await asyncio.wait_for(asyncio.gather(first_reissue, second_reissue), timeout=2)

    assert acknowledgment_was_blocked is True
    assert acknowledged.status_code == 409
    assert acknowledged.json()["code"] == "bootstrap_credential_superseded"
    assert barrier.max_active == 1
    assert {response.status_code for response in reissued} <= {200, 409}
    assert (await _config()).setup_state is SetupState.UNINITIALIZED
    active_digest = (await _config()).bootstrap_credential_receipt_hash
    active = next(
        response.json()
        for response in reissued
        if response.status_code == 200
        and hashlib.sha256(response.json()["credential_receipt"].encode("utf-8")).hexdigest()
        == active_digest
    )
    final_acknowledgment = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": active["credential_receipt"]},
    )
    assert final_acknowledgment.status_code == 200, final_acknowledgment.text
    assert len(_setup_keycloak.passwords[subject]) == 3


async def test_bootstrap_credential_issuance_serializes_break_glass_admin_grant(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.cli import grant_role as grant_role_cli
    from easysynq_api.services.authz import admin_guard

    secret = await _reset_uninitialized()
    barrier = _PasswordResetBarrier()
    _setup_keycloak.password_barrier = barrier
    bootstrap = asyncio.create_task(_provision(app_client, secret, _sub("bootstrap-lock")))
    await asyncio.wait_for(barrier.entered.get(), timeout=2)

    lock_attempted = threading.Event()
    grant_finished = threading.Event()

    def observed_lock(session: object, org_id: uuid.UUID) -> None:
        lock_attempted.set()
        admin_guard.lock_admin_set_sync(session, org_id)  # type: ignore[attr-defined, arg-type]

    def run_grant() -> str:
        try:
            return grant_role_cli.grant_role(_sub("racing-break-glass"))
        finally:
            grant_finished.set()

    monkeypatch.setattr(grant_role_cli, "lock_admin_set_sync", observed_lock, raising=False)
    competing_grant = asyncio.create_task(asyncio.to_thread(run_grant))
    attempted_before_release = await asyncio.to_thread(lock_attempted.wait, 0.5)
    finished_before_release = grant_finished.is_set()
    barrier.release.set()

    provisioned = await asyncio.wait_for(bootstrap, timeout=2)
    grant_result = await asyncio.wait_for(competing_grant, timeout=2)

    assert attempted_before_release is True
    assert finished_before_release is False
    assert provisioned.status_code == 201, provisioned.text
    assert "assigned" in grant_result


async def test_break_glass_ordinary_role_grant_does_not_take_admin_set_lock(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.cli import grant_role as grant_role_cli

    del app_client
    await _reset_uninitialized()
    attempts = 0

    def observed_lock(_session: object, _org_id: uuid.UUID) -> None:
        nonlocal attempts
        attempts += 1

    monkeypatch.setattr(grant_role_cli, "lock_admin_set_sync", observed_lock, raising=False)
    result = grant_role_cli.grant_role(_sub("ordinary-break-glass"), "Author")

    assert "assigned" in result
    assert attempts == 0


async def test_first_administrator_acknowledgment_is_idempotent_for_same_secret(
    app_client: AsyncClient,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("ack-replay")
    async with get_sessionmaker()() as session:
        consumed_before = await session.scalar(
            select(AuditEvent.id)
            .where(AuditEvent.event_type == EventType.BOOTSTRAP_CONSUMED)
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    consumed_before = consumed_before or 0
    provisioned = await _provision(app_client, secret, username)
    provisioned_body = provisioned.json()
    admin_id = uuid.UUID(provisioned_body["administrator"]["id"])

    first = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": provisioned_body["credential_receipt"]},
    )
    second = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": provisioned_body["credential_receipt"]},
    )

    assert first.status_code == second.status_code == 200
    assert (
        first.json()
        == second.json()
        == {
            "setup_state": "IN_SETUP",
            "admin_user_id": str(admin_id),
        }
    )
    async with get_sessionmaker()() as session:
        consumed = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == EventType.BOOTSTRAP_CONSUMED,
                        AuditEvent.object_id == (await _org_id()),
                        AuditEvent.id > consumed_before,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(consumed) == 1


async def test_acknowledgment_replay_accepts_matching_consumed_secret_after_expiry(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("ack-expired-replay")
    provisioned = await _provision(app_client, secret, username)
    provisioned_body = provisioned.json()
    admin_id = provisioned_body["administrator"]["id"]
    first = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": provisioned_body["credential_receipt"]},
    )
    password_count = len(_setup_keycloak.passwords[f"subject:{username}"])
    async with get_sessionmaker()() as session:
        cfg = (await session.execute(select(SystemConfig))).scalar_one()
        cfg.bootstrap_expires_at = setup_service._now() - datetime.timedelta(minutes=1)
        await session.commit()

    replay = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": provisioned_body["credential_receipt"]},
    )

    assert first.status_code == 200
    assert replay.status_code == 200, replay.text
    assert replay.json() == {"setup_state": "IN_SETUP", "admin_user_id": admin_id}
    assert len(_setup_keycloak.passwords[f"subject:{username}"]) == password_count


async def test_expired_consumed_acknowledgment_mismatch_is_generic_and_counted(
    app_client: AsyncClient,
) -> None:
    secret = await _reset_uninitialized()
    provisioned = await _provision(app_client, secret, _sub("ack-expired-wrong"))
    assert provisioned.status_code == 201
    credential_receipt = provisioned.json()["credential_receipt"]
    assert (
        await app_client.post(
            "/api/v1/setup/administrator/acknowledge",
            json={"secret": secret, "credential_receipt": credential_receipt},
        )
    ).status_code == 200
    async with get_sessionmaker()() as session:
        cfg = (await session.execute(select(SystemConfig))).scalar_one()
        cfg.bootstrap_expires_at = setup_service._now() - datetime.timedelta(minutes=1)
        await session.commit()

    denied = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": "wrong", "credential_receipt": credential_receipt},
    )

    assert denied.status_code == 403
    assert denied.json()["code"] == "bootstrap_invalid"
    assert denied.json()["title"] == "Invalid bootstrap secret"
    assert "IN_SETUP" not in denied.text
    async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as client:
        assert await client.get(setup_service._RL_KEY) == "1"


@pytest.mark.parametrize("broken_state", ["incomplete_claim", "missing_assignment"])
async def test_expired_consumed_acknowledgment_replay_preserves_fail_closed_checks(
    app_client: AsyncClient,
    broken_state: str,
) -> None:
    secret = await _reset_uninitialized()
    provisioned = await _provision(app_client, secret, _sub(f"ack-expired-{broken_state}"))
    provisioned_body = provisioned.json()
    admin_id = uuid.UUID(provisioned_body["administrator"]["id"])
    assert (
        await app_client.post(
            "/api/v1/setup/administrator/acknowledge",
            json={"secret": secret, "credential_receipt": provisioned_body["credential_receipt"]},
        )
    ).status_code == 200
    async with get_sessionmaker()() as session:
        cfg = (await session.execute(select(SystemConfig))).scalar_one()
        cfg.bootstrap_expires_at = setup_service._now() - datetime.timedelta(minutes=1)
        if broken_state == "incomplete_claim":
            cfg.bootstrap_credential_issued_at = None
        else:
            await session.execute(delete(RoleAssignment).where(RoleAssignment.user_id == admin_id))
        await session.commit()

    replay = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": provisioned_body["credential_receipt"]},
    )

    assert replay.status_code == 409
    assert replay.json()["code"] == "bootstrap_not_ready"


async def test_reminted_secret_recovers_pending_claim_and_advanced_setup_refuses_remint(
    app_client: AsyncClient,
) -> None:
    from easysynq_api.cli.setup import mint_bootstrap

    secret = await _reset_uninitialized()
    username = _sub("remint")
    issued = await _provision(app_client, secret, username)
    assert issued.status_code == 201
    before = await _config()
    claim_id = before.bootstrap_admin_claim_id
    admin_user_id = before.bootstrap_admin_user_id
    async with get_sessionmaker()() as session:
        cfg = (await session.execute(select(SystemConfig))).scalar_one()
        cfg.bootstrap_expires_at = setup_service._now() - datetime.timedelta(minutes=1)
        await session.commit()

    replacement = mint_bootstrap()
    recovered = await _provision(app_client, replacement, username)

    assert recovered.status_code == 200, recovered.text
    after = await _config()
    assert after.bootstrap_admin_claim_id == claim_id
    assert after.bootstrap_admin_username == username
    assert after.bootstrap_admin_user_id == admin_user_id

    acknowledged = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={
            "secret": replacement,
            "credential_receipt": recovered.json()["credential_receipt"],
        },
    )
    assert acknowledged.status_code == 200
    stored_hash = (await _config()).bootstrap_secret_hash
    with pytest.raises(SystemExit, match="UNINITIALIZED"):
        mint_bootstrap()
    assert (await _config()).bootstrap_secret_hash == stored_hash


async def test_remint_cannot_commit_after_racing_acknowledgment(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.cli import setup as setup_cli
    from easysynq_api.services.setup import administrator as administrator_service

    del _setup_keycloak
    secret = await _reset_uninitialized()
    username = _sub("remint-race")
    provisioned = await _provision(app_client, secret, username)
    assert provisioned.status_code == 201
    original_hash = (await _config()).bootstrap_secret_hash
    replacement_secret, replacement_hash = mint_secret()
    assignment_checked = asyncio.Event()
    allow_acknowledgment = asyncio.Event()
    mint_called = threading.Event()
    real_assignment_exists = administrator_service._admin_assignment_exists

    async def pause_acknowledgment_assignment(
        session: Any, *, org_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        assignment_checked.set()
        await allow_acknowledgment.wait()
        return await real_assignment_exists(session, org_id=org_id, user_id=user_id)

    def observed_mint_secret() -> tuple[str, str]:
        mint_called.set()
        return replacement_secret, replacement_hash

    def run_remint() -> tuple[str, str]:
        try:
            return "minted", setup_cli.mint_bootstrap()
        except SystemExit as exc:
            return "refused", str(exc)

    monkeypatch.setattr(
        administrator_service,
        "_admin_assignment_exists",
        pause_acknowledgment_assignment,
    )
    monkeypatch.setattr(setup_cli, "mint_secret", observed_mint_secret)
    acknowledgment = asyncio.create_task(
        app_client.post(
            "/api/v1/setup/administrator/acknowledge",
            json={
                "secret": secret,
                "credential_receipt": provisioned.json()["credential_receipt"],
            },
        )
    )
    await asyncio.wait_for(assignment_checked.wait(), timeout=2)
    remint = asyncio.create_task(asyncio.to_thread(run_remint))
    mint_ran_before_acknowledgment = await asyncio.to_thread(mint_called.wait, 0.5)
    allow_acknowledgment.set()

    acknowledged = await asyncio.wait_for(acknowledgment, timeout=2)
    remint_outcome = await asyncio.wait_for(remint, timeout=2)
    final_cfg = await _config()

    assert acknowledged.status_code == 200
    assert mint_ran_before_acknowledgment is False
    assert remint_outcome == (
        "refused",
        "bootstrap can only be minted while setup is UNINITIALIZED",
    )
    assert final_cfg.setup_state is SetupState.IN_SETUP
    assert final_cfg.bootstrap_secret_hash == original_hash


async def test_different_bound_username_discloses_only_username_after_valid_secret(
    app_client: AsyncClient,
) -> None:
    secret = await _reset_uninitialized()
    bound = _sub("bound")
    other = _sub("other")
    assert (await _provision(app_client, secret, bound)).status_code == 201

    response = await _provision(app_client, secret, other)

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "bootstrap_identity_bound"
    assert body["bound_username"] == bound
    assert "claim" not in response.text.lower()
    assert "subject:" not in response.text
    assert secret not in response.text


async def test_break_glass_administrator_blocks_public_claim(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    from easysynq_api.cli.grant_role import grant_role

    secret = await _reset_uninitialized()
    grant_role(_sub("existing-admin"))

    response = await _provision(app_client, secret, _sub("second-admin"))

    assert response.status_code == 409
    assert response.json()["code"] == "bootstrap_administrator_exists"
    assert (await _config()).bootstrap_admin_claim_id is None
    assert _setup_keycloak.accounts == {}


async def test_unrelated_administrator_blocks_pending_claim_without_releasing_it(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    from easysynq_api.cli.grant_role import grant_role

    secret = await _reset_uninitialized()
    bound_username = _sub("bound-admin")
    await _establish_claim_only(
        app_client,
        _setup_keycloak,
        secret=secret,
        username=bound_username,
    )
    before = await _config()
    grant_role(_sub("unrelated-admin"))

    response = await _provision(app_client, secret, bound_username)

    assert response.status_code == 409
    assert response.json()["code"] == "bootstrap_administrator_exists"
    after = await _config()
    assert after.bootstrap_admin_claim_id == before.bootstrap_admin_claim_id
    assert after.bootstrap_admin_username == bound_username


async def test_claim_owned_administrator_can_reissue_and_acknowledge(
    app_client: AsyncClient,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("claim-admin")

    created = await _provision(app_client, secret, username)
    reissued = await _provision(app_client, secret, username)
    acknowledged = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": reissued.json()["credential_receipt"]},
    )

    assert created.status_code == 201, created.text
    assert reissued.status_code == 200, reissued.text
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["setup_state"] == "IN_SETUP"


async def test_first_administrator_canonicalizes_mixed_case_username_end_to_end(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    submitted = f"  Mixed.Admin-{uuid.uuid4().hex[:8]}  "
    canonical = submitted.strip().lower()

    first = await _provision(app_client, secret, submitted)
    retried = await _provision(app_client, secret, submitted.swapcase())

    assert first.status_code == 201, first.text
    assert retried.status_code == 200, retried.text
    assert first.json()["administrator"]["username"] == canonical
    assert retried.json()["administrator"]["username"] == canonical
    cfg = await _config()
    assert cfg.bootstrap_admin_username == canonical
    assert set(_setup_keycloak.accounts) == {canonical}
    async with get_sessionmaker()() as session:
        claimed = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == EventType.BOOTSTRAP_IDENTITY_CLAIMED,
                AuditEvent.object_id == cfg.org_id,
                AuditEvent.after["username"].as_string() == canonical,
            )
        )
    assert claimed is not None


@pytest.mark.parametrize("marker", [None, "00000000-0000-0000-0000-000000000001"])
async def test_unrelated_username_collision_retains_claim(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
    marker: str | None,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("collision")
    _setup_keycloak.add_account(username, marker=marker)

    collision = await _provision(app_client, secret, username)

    assert collision.status_code == 409
    assert collision.json()["code"] == "user_exists"
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_username == username
    assert cfg.bootstrap_admin_user_id is None
    assert cfg.bootstrap_credential_issued_at is None
    assert _setup_keycloak.operations[:2] == ["profile", "lookup"]


async def test_reliable_email_collision_retains_claim(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    email = "duplicate@example.local"
    _setup_keycloak.add_account(_sub("email-owner"), email=email)

    username = _sub("email-collision")
    collision = await _provision(app_client, secret, username, email=email)

    assert collision.status_code == 409
    assert collision.json()["code"] == "keycloak_email_exists"
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_username == username


async def test_keycloak_rejection_detail_never_echoes_marker_or_profile(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("rejected-profile")
    email = "rejected-profile@example.local"
    _setup_keycloak.rejection_detail_template = (
        "upstream echoed claim={claim}; username={username}; email={email}"
    )

    response = await _provision(app_client, secret, username, email=email)

    assert response.status_code == 422
    body = response.json()
    cfg = await _config()
    assert body["code"] == "validation_error"
    assert body["title"] == "The identity provider rejected the administrator profile"
    assert "detail" not in body
    assert secret not in response.text
    assert username not in response.text
    assert email not in response.text
    assert str(cfg.bootstrap_admin_claim_id) not in response.text


async def test_keycloak_rejection_releases_unowned_claim_and_allows_corrected_identity(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    _setup_keycloak.rejection_detail_template = "definitive profile rejection"

    rejected = await _provision(app_client, secret, "rejected-admin")

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "validation_error"
    assert (await _config()).bootstrap_admin_claim_id is None
    assert _setup_keycloak.accounts == {}

    _setup_keycloak.rejection_detail_template = None
    corrected = await _provision(app_client, secret, "corrected-admin")
    assert corrected.status_code == 201, corrected.text


async def test_keycloak_rejection_revalidation_found_marker_retains_claim(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("rejected-marker-race")
    await _establish_claim_only(
        app_client,
        _setup_keycloak,
        secret=secret,
        username=username,
    )
    before = await _config()
    assert before.bootstrap_admin_claim_id is not None
    _setup_keycloak.rejection_detail_template = "definitive profile rejection"
    _setup_keycloak.rejection_revalidation = UserLookup(
        True,
        f"external:{username}",
        str(before.bootstrap_admin_claim_id),
    )

    rejected = await _provision(app_client, secret, username)

    assert rejected.status_code == 422, rejected.text
    after = await _config()
    assert after.bootstrap_admin_claim_id == before.bootstrap_admin_claim_id
    assert after.bootstrap_admin_username == username


async def test_keycloak_rejection_revalidation_outage_retains_claim(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("rejected-revalidation-outage")
    _setup_keycloak.rejection_detail_template = "definitive profile rejection"
    _setup_keycloak.rejection_revalidation = KeycloakUnavailable("revalidation unavailable")

    rejected = await _provision(app_client, secret, username)

    assert rejected.status_code == 502, rejected.text
    assert rejected.json()["code"] == "keycloak_unavailable"
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_username == username


async def test_same_claim_request_cannot_create_while_rejection_is_in_flight(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.setup import administrator as administrator_service

    secret = await _reset_uninitialized()
    username = _sub("same-claim-rejection-race")
    barrier = _CreateRejectionBarrier()
    _setup_keycloak.rejection_detail_template = "first request rejected"
    _setup_keycloak.rejection_barrier = barrier

    first = asyncio.create_task(_provision(app_client, secret, username))
    await asyncio.wait_for(barrier.entered.get(), timeout=2)
    _setup_keycloak.rejection_detail_template = None
    singleton_acquired = asyncio.Event()
    real_locked_singleton = administrator_service._locked_singleton

    async def observe_second_singleton(session: Any) -> SystemConfig:
        cfg = await real_locked_singleton(session)
        if asyncio.current_task() is second:
            singleton_acquired.set()
        return cfg

    monkeypatch.setattr(administrator_service, "_locked_singleton", observe_second_singleton)
    second = asyncio.create_task(_provision(app_client, secret, username))
    acquired_before_release = False
    try:
        await asyncio.wait_for(singleton_acquired.wait(), timeout=0.25)
        acquired_before_release = True
    except TimeoutError:
        pass
    finally:
        barrier.release.set()

    rejected = await asyncio.wait_for(first, timeout=2)
    created = await asyncio.wait_for(second, timeout=2)

    assert acquired_before_release is False
    assert rejected.status_code == 422, rejected.text
    assert created.status_code == 201, created.text
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_user_id is not None


async def test_lookup_outage_retains_claim_and_fails_closed(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("malformed-marker")
    _setup_keycloak.lookup_error = KeycloakUnavailable("bootstrap marker was malformed")

    response = await _provision(app_client, secret, username)

    assert response.status_code == 502
    assert response.json()["code"] == "keycloak_unavailable"
    assert _setup_keycloak.operations == ["profile", "lookup"]
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_username == username


async def test_malformed_marker_retains_claim_and_fails_closed(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("malformed-marker")
    _setup_keycloak.add_account(username, marker="not-a-uuid")

    response = await _provision(app_client, secret, username)

    assert response.status_code == 502
    assert response.json()["code"] == "keycloak_unavailable"
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_username == username


async def test_uncertain_create_retains_claim_and_retry_adopts_marked_identity(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("uncertain-create")
    _setup_keycloak.fail_after_create = True

    uncertain = await _provision(app_client, secret, username)

    assert uncertain.status_code == 502
    claimed = await _config()
    assert claimed.bootstrap_admin_claim_id is not None
    assert claimed.bootstrap_admin_user_id is None
    assert _setup_keycloak.accounts[username].marker == str(claimed.bootstrap_admin_claim_id)

    _setup_keycloak.fail_after_create = False
    recovered = await _provision(app_client, secret, username)
    assert recovered.status_code == 200, recovered.text


async def test_database_failure_after_marked_create_retries_same_identity(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.setup import administrator as administrator_service

    secret = await _reset_uninitialized()
    username = _sub("db-failure")
    real_builder = administrator_service._bootstrap_audit_event

    def fail_user_created(**kwargs: Any) -> AuditEvent:
        if kwargs["event_type"] is EventType.USER_CREATED:
            raise RuntimeError("forced user-state database failure")
        return real_builder(**kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(administrator_service, "_bootstrap_audit_event", fail_user_created)
        with pytest.raises(RuntimeError, match="forced user-state database failure"):
            await _provision(app_client, secret, username)

    claimed = await _config()
    assert claimed.bootstrap_admin_claim_id is not None
    assert claimed.bootstrap_admin_user_id is None
    assert _setup_keycloak.accounts[username].marker == str(claimed.bootstrap_admin_claim_id)
    recovered = await _provision(app_client, secret, username)
    assert recovered.status_code == 200, recovered.text


async def test_claimed_retry_reconciles_corrected_profile_in_both_stores(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("corrected-profile")
    original_email = "original@example.local"
    corrected_email = "corrected@example.local"
    _setup_keycloak.fail_after_create = True

    uncertain = await _provision(
        app_client,
        secret,
        username,
        email=original_email,
        display_name="Original Administrator",
        first_name="Original",
        last_name="Administrator",
    )

    assert uncertain.status_code == 502, uncertain.text
    claimed = await _config()
    assert claimed.bootstrap_admin_claim_id is not None
    original_unrelated = dict(_setup_keycloak.accounts[username].unrelated_fields)
    _setup_keycloak.fail_after_create = False

    recovered = await _provision(
        app_client,
        secret,
        username,
        email=corrected_email,
        display_name="Corrected Administrator",
        first_name="Corrected",
        last_name=None,
    )

    assert recovered.status_code == 200, recovered.text
    account = _setup_keycloak.accounts[username]
    assert account.marker == str(claimed.bootstrap_admin_claim_id)
    assert account.email == corrected_email
    assert account.first_name == "Corrected"
    assert account.last_name is None
    assert account.unrelated_fields == original_unrelated
    current = await _config()
    assert current.bootstrap_admin_user_id is not None
    async with get_sessionmaker()() as session:
        user = await session.get(AppUser, current.bootstrap_admin_user_id)
    assert user is not None
    assert user.display_name == "Corrected Administrator"
    assert user.email == corrected_email


async def test_concurrent_profile_retries_converge_on_one_complete_profile(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("concurrent-profile")
    initial = await _provision(app_client, secret, username)
    assert initial.status_code == 201, initial.text
    profiles = {
        (
            "Profile Alpha",
            "alpha@example.local",
            "Alpha",
            "Administrator",
        ),
        (
            "Profile Beta",
            "beta@example.local",
            "Beta",
            None,
        ),
    }

    retries = await asyncio.gather(
        _provision(
            app_client,
            secret,
            username,
            display_name="Profile Alpha",
            email="alpha@example.local",
            first_name="Alpha",
            last_name="Administrator",
        ),
        _provision(
            app_client,
            secret,
            username,
            display_name="Profile Beta",
            email="beta@example.local",
            first_name="Beta",
            last_name=None,
        ),
    )

    assert {response.status_code for response in retries} == {200}
    cfg = await _config()
    assert cfg.bootstrap_admin_user_id is not None
    async with get_sessionmaker()() as session:
        user = await session.get(AppUser, cfg.bootstrap_admin_user_id)
    assert user is not None
    account = _setup_keycloak.accounts[username]
    final_profile = (
        user.display_name,
        account.email,
        account.first_name,
        account.last_name,
    )
    assert final_profile in profiles
    assert user.email == account.email
    assert account.marker == str(cfg.bootstrap_admin_claim_id)


async def test_rejected_claimed_profile_update_retains_claim_redacts_and_recovers(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("rejected-claimed-profile")
    _setup_keycloak.fail_after_create = True
    uncertain = await _provision(
        app_client,
        secret,
        username,
        email="original@example.local",
        first_name="Original",
        last_name="Administrator",
    )
    assert uncertain.status_code == 502, uncertain.text
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    account = _setup_keycloak.accounts[username]
    marker = account.marker
    unrelated = dict(account.unrelated_fields)

    _setup_keycloak.fail_after_create = False
    rejected_email = "provider-rejected@example.local"
    _setup_keycloak.profile_rejection_detail_template = (
        "rejected subject={subject}; claim={claim}; username={username}; email={email}"
    )
    rejected = await _provision(
        app_client,
        secret,
        username,
        email=rejected_email,
        display_name="Rejected Projection",
        first_name="Rejected",
        last_name=None,
    )

    assert rejected.status_code == 422, rejected.text
    body = rejected.json()
    assert body["title"] == "The identity provider rejected the administrator profile"
    assert body["status"] == 422
    assert body["code"] == "validation_error"
    assert "detail" not in body
    assert username not in rejected.text
    assert rejected_email not in rejected.text
    assert str(cfg.bootstrap_admin_claim_id) not in rejected.text
    retained = await _config()
    assert retained.bootstrap_admin_claim_id == cfg.bootstrap_admin_claim_id
    assert retained.bootstrap_admin_user_id is None
    assert account.marker == marker
    assert account.email == "original@example.local"
    assert account.first_name == "Original"
    assert account.last_name == "Administrator"
    assert account.unrelated_fields == unrelated

    _setup_keycloak.profile_rejection_detail_template = None
    corrected = await _provision(
        app_client,
        secret,
        username,
        email="accepted@example.local",
        display_name="Accepted Projection",
        first_name="Accepted",
        last_name=None,
    )
    assert corrected.status_code == 200, corrected.text
    assert account.email == "accepted@example.local"
    assert account.first_name == "Accepted"
    assert account.last_name is None


async def test_password_failure_keeps_linked_user_and_role_for_retry(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("password-failure")
    _setup_keycloak.password_error = KeycloakUnavailable("password endpoint unavailable")

    failed = await _provision(app_client, secret, username)

    assert failed.status_code == 502
    cfg = await _config()
    assert cfg.bootstrap_admin_user_id is not None
    assert cfg.bootstrap_credential_issued_at is None
    async with get_sessionmaker()() as session:
        assigned = await session.scalar(
            select(RoleAssignment.id)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(
                RoleAssignment.user_id == cfg.bootstrap_admin_user_id,
                Role.name == _ADMIN,
            )
        )
    assert assigned is not None

    _setup_keycloak.password_error = None
    recovered = await _provision(app_client, secret, username)
    assert recovered.status_code == 200, recovered.text


async def test_credential_audit_savepoint_failure_preserves_acknowledgeable_receipt_state(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from easysynq_api.services.setup import administrator as administrator_service

    secret = await _reset_uninitialized()
    username = _sub("audit-failure")
    real_builder = administrator_service._bootstrap_audit_event

    def fail_credential_audit(**kwargs: Any) -> AuditEvent:
        event = real_builder(**kwargs)
        if event.event_type is EventType.USER_CREDENTIAL_ISSUED:
            event.actor_type = None  # type: ignore[assignment]  # force DB INSERT failure
        return event

    monkeypatch.setattr(administrator_service, "_bootstrap_audit_event", fail_credential_audit)
    response = await _provision(app_client, secret, username)

    assert response.status_code == 201, response.text
    body = response.json()
    cfg = await _config()
    assert cfg.bootstrap_admin_user_id is not None
    assert cfg.bootstrap_credential_issued_at is not None
    assert (
        cfg.bootstrap_credential_receipt_hash
        == hashlib.sha256(body["credential_receipt"].encode("utf-8")).hexdigest()
    )
    async with get_sessionmaker()() as session:
        credential_audit = await session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.USER_CREDENTIAL_ISSUED,
                AuditEvent.object_id == cfg.bootstrap_admin_user_id,
            )
        )
    assert credential_audit is None
    log_text = caplog.text
    assert secret not in log_text
    assert body["temporary_password"] not in log_text
    assert body["credential_receipt"] not in log_text
    assert f"subject:{username}" not in log_text
    assert str(cfg.bootstrap_admin_user_id) in log_text
    acknowledged = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": body["credential_receipt"]},
    )
    assert acknowledged.status_code == 200, acknowledged.text


async def test_receipt_state_commit_failure_returns_no_credential_and_retry_rotates(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from easysynq_api.services.setup import administrator as administrator_service

    secret = await _reset_uninitialized()
    username = _sub("receipt-state-commit")
    receipts = iter(("A" * 43, "B" * 43))

    def deterministic_receipt() -> tuple[str, str]:
        receipt = next(receipts)
        return receipt, hashlib.sha256(receipt.encode("utf-8")).hexdigest()

    real_commit = AsyncSession.commit
    failed_once = False

    async def fail_first_receipt_state_commit(session: AsyncSession) -> None:
        nonlocal failed_once
        has_receipt_state = any(
            isinstance(value, SystemConfig) and value.bootstrap_credential_receipt_hash is not None
            for value in session.identity_map.values()
        )
        if has_receipt_state and not session.in_nested_transaction() and not failed_once:
            failed_once = True
            raise RuntimeError("forced receipt-state commit failure")
        await real_commit(session)

    monkeypatch.setattr(
        administrator_service,
        "_new_credential_receipt",
        deterministic_receipt,
        raising=False,
    )
    monkeypatch.setattr(AsyncSession, "commit", fail_first_receipt_state_commit)

    failed = await _provision(app_client, secret, username)

    assert failed_once is True
    assert failed.status_code == 503, failed.text
    assert failed.json()["code"] == "dependency_unavailable"
    assert "temporary_password" not in failed.text
    assert "credential_receipt" not in failed.text
    assert "A" * 43 not in failed.text
    first_password = _setup_keycloak.passwords[f"subject:{username}"][-1]
    assert first_password not in failed.text
    assert secret not in failed.text
    after_failure = await _config()
    assert after_failure.bootstrap_credential_issued_at is None
    assert after_failure.bootstrap_credential_receipt_hash is None
    assert "A" * 43 not in caplog.text
    assert first_password not in caplog.text
    assert secret not in caplog.text

    retried = await _provision(app_client, secret, username)

    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["credential_receipt"] == "B" * 43
    assert body["credential_receipt"] != "A" * 43
    assert _setup_keycloak.passwords[f"subject:{username}"][-1] != first_password
    current = await _config()
    assert (
        current.bootstrap_credential_receipt_hash
        == hashlib.sha256(body["credential_receipt"].encode("utf-8")).hexdigest()
    )
    acknowledged = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": body["credential_receipt"]},
    )
    assert acknowledged.status_code == 200, acknowledged.text


async def test_acknowledgment_requires_issued_credential_and_admin_assignment(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.setup import administrator as administrator_service

    secret = await _reset_uninitialized()
    username = _sub("ack-not-ready")
    _setup_keycloak.password_error = KeycloakUnavailable("password endpoint unavailable")
    assert (await _provision(app_client, secret, username)).status_code == 502
    comparisons = 0

    def observed_receipt_match(_receipt: str, _stored_digest: str | None) -> bool:
        nonlocal comparisons
        comparisons += 1
        return True

    monkeypatch.setattr(
        administrator_service,
        "_receipt_matches",
        observed_receipt_match,
        raising=False,
    )

    response = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": "x" * 43},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "bootstrap_not_ready"
    assert comparisons == 0
    assert (await _config()).setup_state is SetupState.UNINITIALIZED


async def test_linked_claim_is_never_released_after_marker_conflict(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("linked-conflict")
    assert (await _provision(app_client, secret, username)).status_code == 201
    before = await _config()
    _setup_keycloak.accounts[username].marker = str(uuid.uuid4())

    collision = await _provision(app_client, secret, username)

    assert collision.status_code == 409
    assert collision.json()["code"] == "user_exists"
    after = await _config()
    assert after.bootstrap_admin_claim_id == before.bootstrap_admin_claim_id
    assert after.bootstrap_admin_user_id == before.bootstrap_admin_user_id
    assert after.bootstrap_credential_issued_at is not None


async def test_bootstrap_rate_limit_dependency_outage_fails_closed_before_claim(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = await _reset_uninitialized()

    def unavailable_redis() -> Any:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(setup_service, "_redis", unavailable_redis)
    response = await _provision(app_client, secret, _sub("redis-outage"))

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert (await _config()).bootstrap_admin_claim_id is None


async def test_non_uninitialized_state_denies_provisioning_even_with_bearer(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    secret = await _reset_uninitialized()
    _, body = await _bootstrap(app_client, token_factory, secret, "advanced")

    response = await app_client.post(
        "/api/v1/setup/administrator",
        headers=_auth(token_factory, _sub("irrelevant-bearer")),
        json={
            "secret": secret,
            "username": _sub("second-admin"),
            "display_name": "Second Administrator",
        },
    )

    assert body["setup_state"] == "IN_SETUP"
    assert response.status_code == 409
    assert response.json()["code"] == "setup_already_complete"


async def test_first_administrator_audit_order_actor_and_payload_secrecy(
    app_client: AsyncClient,
) -> None:
    secret = await _reset_uninitialized()
    username = _sub("audit-order")
    async with get_sessionmaker()() as session:
        baseline = await session.scalar(
            select(AuditEvent.id).order_by(AuditEvent.id.desc()).limit(1)
        )
    baseline = baseline or 0
    provisioned = await _provision(app_client, secret, username)
    password = provisioned.json()["temporary_password"]
    credential_receipt = provisioned.json()["credential_receipt"]
    admin_id = uuid.UUID(provisioned.json()["administrator"]["id"])
    acknowledged = await app_client.post(
        "/api/v1/setup/administrator/acknowledge",
        json={"secret": secret, "credential_receipt": credential_receipt},
    )
    assert acknowledged.status_code == 200
    cfg = await _config()
    subject = f"subject:{username}"

    async with get_sessionmaker()() as session:
        events = (
            (
                await session.execute(
                    select(AuditEvent)
                    .where(
                        AuditEvent.id > baseline,
                        AuditEvent.event_type.in_(
                            {
                                EventType.BOOTSTRAP_IDENTITY_CLAIMED,
                                EventType.USER_CREATED,
                                EventType.ADMIN_BOOTSTRAPPED,
                                EventType.USER_CREDENTIAL_ISSUED,
                                EventType.BOOTSTRAP_CONSUMED,
                            }
                        ),
                    )
                    .order_by(AuditEvent.id)
                )
            )
            .scalars()
            .all()
        )

    assert [event.event_type for event in events] == [
        EventType.BOOTSTRAP_IDENTITY_CLAIMED,
        EventType.USER_CREATED,
        EventType.ADMIN_BOOTSTRAPPED,
        EventType.USER_CREDENTIAL_ISSUED,
        EventType.BOOTSTRAP_CONSUMED,
    ]
    for event in events:
        assert event.actor_type.value == "system"
        assert event.actor_id is None
        assert event.org_id == cfg.org_id
        assert (
            set(event.after or {})
            <= {
                EventType.BOOTSTRAP_IDENTITY_CLAIMED: {"username"},
                EventType.USER_CREATED: {"status", "email", "provisioning"},
                EventType.ADMIN_BOOTSTRAPPED: {"role"},
                EventType.USER_CREDENTIAL_ISSUED: {"credential_issued"},
                EventType.BOOTSTRAP_CONSUMED: set(),
            }[event.event_type]
        )
        rendered = repr(event.after)
        assert secret not in rendered
        assert password not in rendered
        assert credential_receipt not in rendered
        assert subject not in rendered
        assert str(cfg.bootstrap_admin_claim_id) not in rendered
    assert events[1].object_id == admin_id


async def test_concurrent_first_administrator_requests_converge_on_single_state(
    app_client: AsyncClient,
) -> None:
    import asyncio

    secret = await _reset_uninitialized()
    username = _sub("concurrent")
    async with get_sessionmaker()() as session:
        claimed_before = await session.scalar(
            select(AuditEvent.id)
            .where(AuditEvent.event_type == EventType.BOOTSTRAP_IDENTITY_CLAIMED)
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
    claimed_before = claimed_before or 0

    first, second = await asyncio.gather(
        _provision(app_client, secret, username),
        _provision(app_client, secret, username),
    )

    assert {first.status_code, second.status_code} == {200, 201}
    cfg = await _config()
    assert cfg.bootstrap_admin_claim_id is not None
    assert cfg.bootstrap_admin_user_id is not None
    async with get_sessionmaker()() as session:
        users = (
            (
                await session.execute(
                    select(AppUser.id).where(AppUser.keycloak_subject == f"subject:{username}")
                )
            )
            .scalars()
            .all()
        )
        assignments = (
            (
                await session.execute(
                    select(RoleAssignment.id)
                    .join(Role, Role.id == RoleAssignment.role_id)
                    .where(
                        RoleAssignment.user_id == cfg.bootstrap_admin_user_id, Role.name == _ADMIN
                    )
                )
            )
            .scalars()
            .all()
        )
        claimed = (
            (
                await session.execute(
                    select(AuditEvent.id).where(
                        AuditEvent.event_type == EventType.BOOTSTRAP_IDENTITY_CLAIMED,
                        AuditEvent.id > claimed_before,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert users == [cfg.bootstrap_admin_user_id]
    assert len(assignments) == 1
    assert len(claimed) == 1


async def test_org_profile_requires_admin(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """org-profile is gated on config.update — a non-admin is 403; the admin succeeds."""
    secret = await _reset_uninitialized()
    payload = {"legal_name": "Acme Corp", "short_code": "ACME", "timezone": "Europe/London"}

    h_other = _auth(token_factory, _sub("other"))
    forbidden = await app_client.patch("/api/v1/setup/org-profile", headers=h_other, json=payload)
    assert forbidden.status_code == 403

    h_admin, _ = await _bootstrap(app_client, token_factory, secret, "admin")
    ok = await app_client.patch("/api/v1/setup/org-profile", headers=h_admin, json=payload)
    assert ok.status_code == 200, ok.text
    assert ok.json()["short_code"] == "ACME"
    async with get_sessionmaker()() as s:
        org = (await s.execute(select(Organization))).scalar_one()
        assert org.short_code == "ACME"
        assert org.timezone == "Europe/London"
        # S-notify-6: set_org_profile must SYNC the org's default working_calendar tz — else a
        # non-UTC org's business-day SLAs evaluate against the migration-time UTC seed.
        cal = (
            await s.execute(
                select(WorkingCalendar).where(
                    WorkingCalendar.org_id == org.id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one_or_none()
        assert cal is not None and cal.timezone == "Europe/London", (
            "default working_calendar tz must follow the org tz set in setup"
        )


async def test_org_profile_rejects_default_short_code(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    secret = await _reset_uninitialized()
    h, _ = await _bootstrap(app_client, token_factory, secret, "admin")
    r = await app_client.patch(
        "/api/v1/setup/org-profile",
        headers=h,
        json={"legal_name": "Acme", "short_code": "DEFAULT", "timezone": "UTC"},
    )
    assert r.status_code == 422


async def test_set_org_profile_preserves_customized_calendar_tz(
    app_under_test: Any,
) -> None:
    """Fix 2 (Codex P2): set_org_profile must NOT overwrite a default calendar whose tz has been
    operator-customized beyond the org tz. Only a calendar that still tracks the org tz
    (calendar.tz == old org tz) should be updated — preserving an editor-set tz is the invariant.

    Uses the singleton org + a temp actor; restores the original calendar tz + org state in
    finally so subsequent setup tests see a clean shared org."""
    # Read current org + calendar state.
    async with get_sessionmaker()() as s:
        org = (await s.execute(select(Organization))).scalar_one()
        org_id = org.id
        orig_org_tz = org.timezone
        orig_legal_name = org.legal_name
        orig_short_code = org.short_code
        cal = (
            await s.execute(
                select(WorkingCalendar).where(
                    WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one_or_none()
        orig_cal_tz = cal.timezone if cal is not None else None

    if orig_cal_tz is None:
        pytest.skip("no default calendar seeded for this org — skipping customization test")

    # Create a temp actor on the singleton org.
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        actor = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-ctz-{salt}",
            display_name="Tz Test",
            email=None,
        )
        s.add(actor)
        await s.commit()
        actor_id = actor.id

    try:
        # Mark the default calendar as "customized" by setting a tz different from the org tz.
        custom_tz = "Pacific/Auckland"
        async with get_sessionmaker()() as s:
            await s.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
                .values(timezone=custom_tz)
            )
            await s.commit()

        # Call set_org_profile with a different org tz.  The customized calendar tz must be
        # preserved (NOT overwritten to the new org tz).
        new_org_tz = "America/Denver"
        valid_code = orig_short_code if orig_short_code != "DEFAULT" else "CTZTEST1"
        valid_name = orig_legal_name if orig_legal_name else "Test Org Tz"
        async with get_sessionmaker()() as s:
            actor = await s.get(AppUser, actor_id)
            await setup_service.set_org_profile(
                s, actor, legal_name=valid_name, short_code=valid_code, timezone=new_org_tz
            )
            # set_org_profile commits internally.

        # Verify: the calendar tz was NOT changed to new_org_tz (it stays at custom_tz).
        async with get_sessionmaker()() as s:
            cal = (
                await s.execute(
                    select(WorkingCalendar).where(
                        WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                    )
                )
            ).scalar_one_or_none()
            assert cal is not None
            assert cal.timezone == custom_tz, (
                f"customized calendar tz must be preserved; "
                f"got {cal.timezone!r}, expected {custom_tz!r}"
            )
    finally:
        # Restore calendar tz + org profile. The temp actor is NOT deleted here: set_org_profile
        # commits an ORG_PROFILE_SET audit_event with actor_id = actor.id, so a DELETE on the
        # actor violates the fk_audit_event_actor_id_app_user FK. The actor is left in the shared
        # test DB (harmless — testcontainers recreates the DB each session; existing tests never
        # assert absolute user counts). This mirrors the no-cleanup-of-grant-users precedent used
        # throughout this integration suite.
        async with get_sessionmaker()() as s:
            org_row = await s.get(Organization, org_id)
            if org_row is not None:
                org_row.legal_name = orig_legal_name
                org_row.short_code = orig_short_code
                org_row.timezone = orig_org_tz
            await s.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
                .values(timezone=orig_cal_tz)
            )
            await s.commit()


async def test_finalize_blocked_then_operational_lifts_latch(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[HEADLINE] Finalize is blocked until G-E (org profile) passes; once it does, the latch flips
    to OPERATIONAL (SETUP_FINALIZED audited) and the QMS surface is no longer 423."""
    secret = await _reset_uninitialized()
    h, body = await _bootstrap(app_client, token_factory, secret, "fin")  # G-A satisfied
    admin_id = uuid.UUID(body["admin_user_id"])

    blocked = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "setup_gates_unsatisfied"
    assert any(g["key"] == "G-E" for g in blocked.json()["failed_gates"])

    await app_client.patch(
        "/api/v1/setup/org-profile",
        headers=h,
        json={"legal_name": "Acme Corp", "short_code": "ACME", "timezone": "UTC"},
    )
    await _verify_storage(app_client, h)  # G-B (S8b) is now a required finalize gate too
    await _pass_restore_gate()  # G-C (S8b2) is required too — drill proven separately (AC#5)
    await _pass_auth_gate()  # G-D (S8c) is required too — configure-auth proven separately
    done = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["setup_state"] == "OPERATIONAL"
    assert done.json()["finalized_at"]

    async with get_sessionmaker()() as s:
        finalized = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.SETUP_FINALIZED,
                AuditEvent.actor_id == admin_id,
            )
        )
    assert finalized is not None

    # The latch has lifted: the QMS surface answers normally (200 filtered list), not 423.
    lifted = await app_client.get("/api/v1/documents", headers=h)
    assert lifted.status_code != 423


async def test_latch_exemptions_are_boundary_anchored(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The wizard's exemptions pass through while UNINITIALIZED, but a sibling that merely shares a
    prefix (e.g. /api/v1/members vs the /api/v1/me exemption) stays latched — boundary-anchored."""
    await _reset_uninitialized()
    h = _auth(token_factory, _sub("ex"))
    assert (await app_client.get("/api/v1/auth/config")).status_code == 200
    assert (await app_client.get("/api/v1/me", headers=h)).status_code == 200
    collide = await app_client.get(
        "/api/v1/members", headers=h
    )  # no such route; must NOT be exempt
    assert collide.status_code == 423


async def test_bootstrap_rate_limit_locks_out(
    app_client: AsyncClient,
) -> None:
    """The brute-force throttle: 5 failed attempts each 403, the 6th is 429 rate_limited."""
    await _reset_uninitialized()
    username = _sub("rl")
    for _ in range(5):
        bad = await _provision(app_client, "wrong", username)
        assert bad.status_code == 403, bad.text
    locked = await _provision(app_client, "wrong", username)
    assert locked.status_code == 429
    assert locked.json()["code"] == "rate_limited"


async def test_concurrent_invalid_bootstrap_attempts_stop_at_limit(
    app_client: AsyncClient,
    _setup_keycloak: _FakeKeycloak,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every racing request passes the fast pre-lock check; lock-time admission still limits the
    serialized invalid proofs to exactly the configured failure budget."""
    from easysynq_api.services.setup import administrator as administrator_service

    await _reset_uninitialized()
    request_count = setup_service._RL_MAX + 3
    outer_arrivals: set[asyncio.Task[Any]] = set()
    all_outer_arrived = asyncio.Event()
    real_check = administrator_service._check_rate_limit

    async def synchronize_outer_checks() -> None:
        task = asyncio.current_task()
        assert task is not None
        if task not in outer_arrivals:
            outer_arrivals.add(task)
            if len(outer_arrivals) == request_count:
                all_outer_arrived.set()
            await asyncio.wait_for(all_outer_arrived.wait(), timeout=2)
        await real_check()

    monkeypatch.setattr(administrator_service, "_check_rate_limit", synchronize_outer_checks)
    responses = await asyncio.wait_for(
        asyncio.gather(
            *(
                _provision(app_client, "wrong", _sub(f"rl-race-{index}"))
                for index in range(request_count)
            )
        ),
        timeout=10,
    )

    assert [response.status_code for response in responses].count(403) == setup_service._RL_MAX
    assert [response.status_code for response in responses].count(429) == 3
    assert {response.json()["code"] for response in responses} == {
        "bootstrap_invalid",
        "rate_limited",
    }
    assert _setup_keycloak.operations == []


@pytest.mark.parametrize("endpoint", ["provision", "acknowledge"])
async def test_bootstrap_rate_limit_is_rechecked_after_singleton_lock(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    """Both public proof endpoints admit again only after the singleton lock is held."""
    from easysynq_api.services.setup import administrator as administrator_service

    await _reset_uninitialized()
    events: list[str] = []
    real_check = administrator_service._check_rate_limit
    real_lock = administrator_service._locked_singleton
    real_request_proof = administrator_service._validate_request_proof
    real_acknowledgment_proof = administrator_service._validate_acknowledgment_proof

    async def observed_check() -> None:
        events.append("rate")
        await real_check()

    async def observed_lock(session: Any) -> SystemConfig:
        cfg = await real_lock(session)
        events.append("locked")
        return cfg

    async def observed_request_proof(cfg: SystemConfig, secret: str) -> None:
        events.append("proof")
        await real_request_proof(cfg, secret)

    async def observed_acknowledgment_proof(cfg: SystemConfig, secret: str) -> None:
        events.append("proof")
        await real_acknowledgment_proof(cfg, secret)

    monkeypatch.setattr(administrator_service, "_check_rate_limit", observed_check)
    monkeypatch.setattr(administrator_service, "_locked_singleton", observed_lock)
    monkeypatch.setattr(administrator_service, "_validate_request_proof", observed_request_proof)
    monkeypatch.setattr(
        administrator_service,
        "_validate_acknowledgment_proof",
        observed_acknowledgment_proof,
    )

    if endpoint == "provision":
        response = await _provision(app_client, "wrong", _sub("rl-order"))
    else:
        response = await app_client.post(
            "/api/v1/setup/administrator/acknowledge",
            json={"secret": "wrong", "credential_receipt": "x" * 43},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "bootstrap_invalid"
    assert events == ["rate", "locked", "rate", "proof"]


async def test_atomic_failure_counter_repairs_legacy_no_ttl_and_preserves_live_ttl(
    app_client: AsyncClient,
) -> None:
    del app_client
    await _reset_uninitialized()
    async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as client:
        await client.set(setup_service._RL_KEY, "2", ex=120)
        live_before = await client.ttl(setup_service._RL_KEY)

    await setup_service._record_failure()

    async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as client:
        assert await client.get(setup_service._RL_KEY) == "3"
        live_after = await client.ttl(setup_service._RL_KEY)
        assert 0 < live_after <= live_before
        assert live_after >= live_before - 2
        await client.set(setup_service._RL_KEY, "4")
        assert await client.ttl(setup_service._RL_KEY) == -1

    await setup_service._record_failure()

    async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as client:
        assert await client.get(setup_service._RL_KEY) == "5"
        repaired_ttl = await client.ttl(setup_service._RL_KEY)
    assert setup_service._RL_WINDOW_SECONDS - 2 <= repaired_ttl <= setup_service._RL_WINDOW_SECONDS


async def test_atomic_failure_counter_rejects_malformed_state_fail_closed(
    app_client: AsyncClient,
) -> None:
    del app_client
    await _reset_uninitialized()
    async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as client:
        await client.set(setup_service._RL_KEY, "not-an-integer")

    with pytest.raises(ProblemException) as excinfo:
        await setup_service._record_failure()

    assert excinfo.value.status == 503
    assert excinfo.value.code == "dependency_unavailable"


async def test_bootstrap_rejects_expired_secret(
    app_client: AsyncClient,
) -> None:
    secret = await _reset_uninitialized()
    async with get_sessionmaker()() as s:
        cfg = (await s.execute(select(SystemConfig))).scalar_one()
        cfg.bootstrap_expires_at = setup_service._now() - datetime.timedelta(minutes=1)
        await s.commit()
    r = await _provision(app_client, secret, _sub("exp"))
    assert r.status_code == 403
    assert r.json()["code"] == "bootstrap_invalid"


async def test_bootstrap_rejects_when_no_secret_minted(
    app_client: AsyncClient,
) -> None:
    await _reset_uninitialized()
    async with get_sessionmaker()() as s:
        cfg = (await s.execute(select(SystemConfig))).scalar_one()
        cfg.bootstrap_secret_hash = None
        await s.commit()
    r = await _provision(app_client, "anything", _sub("ns"))
    assert r.status_code == 403
    assert r.json()["code"] == "bootstrap_invalid"


async def test_grant_role_break_glass_still_works(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The grant-role CLI remains the break-glass path (owner-level insert, bypasses the PEP)."""
    from easysynq_api.cli.grant_role import grant_role

    await _reset_uninitialized()
    sub = _sub("bg")
    result = grant_role(sub)  # sync, owner DSN
    assert "assigned" in result

    async with get_sessionmaker()() as s:
        assigned = await s.scalar(
            select(RoleAssignment.id)
            .join(Role, RoleAssignment.role_id == Role.id)
            .join(AppUser, RoleAssignment.user_id == AppUser.id)
            .where(AppUser.keycloak_subject == sub, Role.name == _ADMIN)
        )
    assert assigned is not None


# --- S8b: G-B WORM-verify -----------------------------------------------------------------


async def test_worm_probe_detects_enforcement(app_client: AsyncClient) -> None:
    """[S8b] The probe verifies the object-locked `documents` bucket (early delete denied) and
    correctly reports the plain `staging` bucket as NOT WORM. (Depends on app_client only to wire
    the testcontainer S3 settings.)"""
    docs = await storage.worm_probe("documents")
    assert docs.verified is True, docs.detail
    assert docs.retain_until is not None

    staging = await storage.worm_probe("staging")  # plain bucket, no object-lock
    assert staging.verified is False


async def test_verify_storage_passes_and_satisfies_g_b(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[HEADLINE S8b] verify-storage proves WORM, sets worm_verified_at + a WORM_VERIFIED audit row,
    and flips gate G-B (the live finalize gate)."""
    secret = await _reset_uninitialized()
    h, body = await _bootstrap(app_client, token_factory, secret, "worm")
    admin_id = uuid.UUID(body["admin_user_id"])

    res = await _verify_storage(app_client, h, "GOVERNANCE")
    assert res["worm_verified"] is True
    assert res["object_lock_mode"] == "GOVERNANCE"

    detail = await app_client.get("/api/v1/setup", headers=h)
    assert detail.json()["gates"]["G-B"] is True

    async with get_sessionmaker()() as s:
        cfg = (await s.execute(select(StorageConfig))).scalar_one()
        assert cfg.worm_verified_at is not None
        assert cfg.object_lock_mode == "GOVERNANCE"
        worm_audit = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.WORM_VERIFIED,
                AuditEvent.actor_id == admin_id,
            )
        )
    assert worm_audit is not None


async def test_verify_storage_requires_storage_manage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A non-admin (no bootstrap → no storage.manage) cannot verify storage."""
    await _reset_uninitialized()
    h = _auth(token_factory, _sub("nope"))
    r = await app_client.post(
        "/api/v1/setup/verify-storage", headers=h, json={"object_lock_mode": "GOVERNANCE"}
    )
    assert r.status_code == 403


async def test_finalize_blocked_on_g_b_until_worm_verified(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """With G-A + G-E satisfied but WORM not yet verified, finalize is blocked on G-B; verifying
    storage then lets it finalize."""
    secret = await _reset_uninitialized()
    h, _ = await _bootstrap(app_client, token_factory, secret, "gb")
    await app_client.patch(
        "/api/v1/setup/org-profile",
        headers=h,
        json={"legal_name": "Acme", "short_code": "ACME", "timezone": "UTC"},
    )
    blocked = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert blocked.status_code == 409
    assert any(g["key"] == "G-B" for g in blocked.json()["failed_gates"])

    await _verify_storage(app_client, h)
    await _pass_restore_gate()  # G-C (S8b2) is required too — drill proven separately (AC#5)
    await _pass_auth_gate()  # G-D (S8c) is required too — configure-auth proven separately
    done = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["setup_state"] == "OPERATIONAL"


async def test_verify_storage_rerun_updates_in_place(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Re-running verify-storage (resumable wizard: re-click / switch mode) UPDATEs the single
    storage_config row in place — not a second INSERT (which would 500 on UNIQUE(org_id))."""
    secret = await _reset_uninitialized()
    h, _ = await _bootstrap(app_client, token_factory, secret, "rerun")

    await _verify_storage(app_client, h, "GOVERNANCE")
    async with get_sessionmaker()() as s:
        first = (await s.execute(select(StorageConfig))).scalar_one()
        first_at = first.worm_verified_at
    assert first.object_lock_mode == "GOVERNANCE"

    res = await _verify_storage(app_client, h, "COMPLIANCE")
    assert res["object_lock_mode"] == "COMPLIANCE"
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(StorageConfig))).scalars().all()
    assert len(rows) == 1  # UPDATE in place, not a second INSERT
    assert rows[0].object_lock_mode == "COMPLIANCE"
    assert first_at is not None and rows[0].worm_verified_at >= first_at


# --- S8b2: G-C backup/restore drill (AC#5) ------------------------------------------------
#
# These exercise the REAL drill (pg_dump/pg_restore against the testcontainer PG + a MinIO scratch
# bucket). The CI `integration` job's runner carries postgresql-client-16; a runner/host without it
# makes the drill an honest FAIL (a missing binary is caught + reported, never a 500).


async def _org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (await s.execute(select(Organization.id))).scalar_one()


async def _bootstrap_through_storage(
    app_client: AsyncClient, token_factory: Callable[..., str], sub: str
) -> tuple[dict[str, str], uuid.UUID]:
    """reset → bootstrap (G-A) → org (G-E) → verify-storage (G-B). Returns (headers, admin_id)."""
    secret = await _reset_uninitialized()
    h, body = await _bootstrap(app_client, token_factory, secret, sub)
    await app_client.patch(
        "/api/v1/setup/org-profile",
        headers=h,
        json={"legal_name": "Acme Corp", "short_code": "ACME", "timezone": "UTC"},
    )
    await _verify_storage(app_client, h)
    return h, uuid.UUID(body["admin_user_id"])


async def test_setup_finalize_requires_restore_pass(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[HEADLINE / AC#5] Finalize is BLOCKED on G-C until a real backup→restore-into-scratch drill
    PASSES the integrity triad; "configured but unverified" does NOT satisfy it. test name is the
    doc-18 §7 acceptance proof."""
    dest = tempfile.mkdtemp(prefix="easysynq-drill-")
    try:
        h, admin_id = await _bootstrap_through_storage(app_client, token_factory, "ac5")
        org_id = await _org_id()

        # G-A/E/B satisfied, but no restore test yet → finalize blocked specifically on G-C.
        blocked = await app_client.post("/api/v1/setup/finalize", headers=h)
        assert blocked.status_code == 409
        assert any(g["key"] == "G-C" for g in blocked.json()["failed_gates"])

        # Configure the backup destination (records the policy — does NOT satisfy G-C on its own).
        cfg = await app_client.post(
            "/api/v1/setup/configure-backup", headers=h, json={"destination": dest}
        )
        assert cfg.status_code == 200, cfg.text
        still_blocked = await app_client.post("/api/v1/setup/finalize", headers=h)
        assert still_blocked.status_code == 409  # configured ≠ verified
        assert any(g["key"] == "G-C" for g in still_blocked.json()["failed_gates"])

        # Run the REAL drill (the endpoint only enqueues; drive the worker coroutine directly).
        result = await backup_service.run_restore_test(org_id, admin_id)
        assert result["result"] == "PASS", result

        # The persisted PASS flips G-C → finalize succeeds; a RESTORE_TEST_PASSED row was written.
        detail = await app_client.get("/api/v1/setup", headers=h)
        assert detail.json()["gates"]["G-C"] is True
        assert detail.json()["backup"]["last_restore_test_result"] == "PASS"

        await _pass_auth_gate()  # G-D (S8c) is required too — configure-auth proven separately
        done = await app_client.post("/api/v1/setup/finalize", headers=h)
        assert done.status_code == 200, done.text
        assert done.json()["setup_state"] == "OPERATIONAL"

        async with get_sessionmaker()() as s:
            passed = await s.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.event_type == EventType.RESTORE_TEST_PASSED,
                    AuditEvent.object_id == org_id,
                )
            )
        assert passed is not None
    finally:
        shutil.rmtree(dest, ignore_errors=True)


async def test_restore_drill_failure_blocks_finalize(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[NEGATIVE / AC#5] If the integrity triad fails (here: a restored scratch blob is corrupted
    after restore), the drill is FAIL — last_restore_test_result is not PASS, G-C stays unsatisfied,
    and finalize stays blocked. The drill must never falsely claim recoverability."""
    import boto3

    dest = tempfile.mkdtemp(prefix="easysynq-drill-fail-")
    try:
        h, admin_id = await _bootstrap_through_storage(app_client, token_factory, "ac5fail")
        org_id = await _org_id()
        await app_client.post(
            "/api/v1/setup/configure-backup", headers=h, json={"destination": dest}
        )

        settings = get_settings()
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

        def _corrupt(handle: backup_service.ScratchHandle) -> None:
            # Corrupt every restored scratch blob so the re-hash leg mismatches. At fresh-setup time
            # there are no blobs yet, so fall back to dropping a row (row-count parity then fails).
            paginator = client.get_paginator("list_objects_v2")
            wrote = False
            for page in paginator.paginate(
                Bucket=handle.scratch_bucket, Prefix=handle.object_prefix
            ):
                for obj in page.get("Contents", []):
                    client.put_object(
                        Bucket=handle.scratch_bucket, Key=obj["Key"], Body=b"corrupted-bytes"
                    )
                    wrote = True
            if not wrote:
                _drop_a_scratch_row(handle)

        result = await backup_service.run_restore_test(org_id, admin_id, after_restore=_corrupt)
        assert result["result"] == "FAIL", result

        detail = await app_client.get("/api/v1/setup", headers=h)
        assert detail.json()["gates"]["G-C"] is False
        assert detail.json()["backup"]["last_restore_test_result"] == "FAIL"

        blocked = await app_client.post("/api/v1/setup/finalize", headers=h)
        assert blocked.status_code == 409
        assert any(g["key"] == "G-C" for g in blocked.json()["failed_gates"])

        async with get_sessionmaker()() as s:
            failed = await s.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.event_type == EventType.RESTORE_TEST_FAILED,
                    AuditEvent.object_id == org_id,
                )
            )
        assert failed is not None
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def _drop_a_scratch_row(handle: backup_service.ScratchHandle) -> None:
    """Delete one row from the restored scratch DB so row-count parity fails (the fallback fault
    when the fresh-setup DB carries no blobs to corrupt)."""
    import psycopg

    from easysynq_api.services.backup.dsn import conn_kwargs

    with (
        psycopg.connect(
            **conn_kwargs(handle.owner_dsn, dbname=handle.scratch_db), autocommit=True
        ) as conn,
        conn.cursor() as cur,
    ):
        cur.execute("DELETE FROM permission WHERE ctid IN (SELECT ctid FROM permission LIMIT 1)")


# --- S8c: G-D auth-config gate -------------------------------------------------------------
#
# The live OIDC-issuer probe is monkeypatched (CI runs no Keycloak; the integration conftest stubs
# JWKS). The non-bootstrap login proof is real: the minted admin token is a valid JWKS-validated JWT
# distinct from the install-secret bootstrap POST (which authorizes outside the PEP).


async def _stub_auth_probe(monkeypatch: pytest.MonkeyPatch, *, ok: bool) -> None:
    async def _probe(_issuer: str, _discovery_url: str | None = None) -> tuple[bool, str]:
        return ok, "stubbed reachable" if ok else "stubbed unreachable"

    monkeypatch.setattr(setup_service.auth_check, "probe_oidc_discovery", _probe)


async def test_setup_finalize_requires_auth_proven(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[HEADLINE / G-D] Finalize is BLOCKED on G-D until configure-auth proves a non-bootstrap
    login; a recorded-but-unproven auth does not satisfy it. Once proven, finalize → OPERATIONAL."""
    h, admin_id = await _bootstrap_through_storage(app_client, token_factory, "gd")
    await _pass_restore_gate()  # G-A/E/B/C satisfied; only G-D outstanding

    blocked = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert blocked.status_code == 409
    assert any(g["key"] == "G-D" for g in blocked.json()["failed_gates"])

    await _stub_auth_probe(monkeypatch, ok=True)
    res = await app_client.post(
        "/api/v1/setup/configure-auth",
        headers=h,
        json={"method": "LOCAL", "mfa_acknowledged": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["auth_test_login_ok"] is True

    detail = await app_client.get("/api/v1/setup", headers=h)
    assert detail.json()["gates"]["G-D"] is True
    assert detail.json()["auth"]["method"] == "LOCAL"

    done = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["setup_state"] == "OPERATIONAL"

    async with get_sessionmaker()() as s:
        for evt in (EventType.AUTH_CONFIGURED, EventType.AUTH_TEST_LOGIN_OK):
            row = await s.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.event_type == evt, AuditEvent.actor_id == admin_id
                )
            )
            assert row is not None, evt


async def test_configure_auth_unreachable_idp_blocks_finalize(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """[NEGATIVE / G-D] An unreachable/misconfigured IdP → 422 auth_unavailable, the signal stays
    null, G-D stays red, finalize stays blocked, and AUTH_TEST_LOGIN_FAILED is audited — no
    false-PASS that would strand the org on a broken login."""
    h, admin_id = await _bootstrap_through_storage(app_client, token_factory, "gdfail")
    await _pass_restore_gate()

    await _stub_auth_probe(monkeypatch, ok=False)
    res = await app_client.post("/api/v1/setup/configure-auth", headers=h, json={"method": "LOCAL"})
    assert res.status_code == 422
    assert res.json()["code"] == "auth_unavailable"

    detail = await app_client.get("/api/v1/setup", headers=h)
    assert detail.json()["gates"]["G-D"] is False
    assert detail.json()["auth"]["configured"] is False

    blocked = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert blocked.status_code == 409
    assert any(g["key"] == "G-D" for g in blocked.json()["failed_gates"])

    async with get_sessionmaker()() as s:
        failed = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.AUTH_TEST_LOGIN_FAILED,
                AuditEvent.actor_id == admin_id,
            )
        )
    assert failed is not None


async def test_configure_auth_requires_config_update(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """configure-auth is gated on config.update — a non-admin is 403 (before any probe runs)."""
    await _reset_uninitialized()
    await _stub_auth_probe(monkeypatch, ok=True)
    h_other = _auth(token_factory, _sub("noauth"))
    r = await app_client.post(
        "/api/v1/setup/configure-auth", headers=h_other, json={"method": "LOCAL"}
    )
    assert r.status_code == 403


async def test_configure_auth_rejects_bad_method(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = await _reset_uninitialized()
    await _stub_auth_probe(monkeypatch, ok=True)
    h, _ = await _bootstrap(app_client, token_factory, secret, "badmethod")
    r = await app_client.post("/api/v1/setup/configure-auth", headers=h, json={"method": "WAT"})
    assert r.status_code == 422
