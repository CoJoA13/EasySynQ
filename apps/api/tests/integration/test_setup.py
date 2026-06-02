"""S8a integration proofs — the setup latch + bootstrap-of-trust + org profile + finalize
(testcontainers PG/MinIO/Redis).

The conftest defaults the shared DB to OPERATIONAL; each test here resets the singleton to a clean
UNINITIALIZED state (fresh bootstrap secret, no admin, placeholder org, cleared rate-limit) so the
first-run flow is deterministic regardless of order.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable

import pytest
import redis.asyncio as aioredis
from httpx import AsyncClient
from sqlalchemy import delete, select

from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.storage_config import StorageConfig
from easysynq_api.db.models.system_config import SetupState, SystemConfig
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.setup import service as setup_service
from easysynq_api.services.setup.bootstrap import mint_secret
from easysynq_api.services.vault import storage

from .test_vault import _auth

pytestmark = pytest.mark.integration

_ADMIN = "System Administrator"


def _sub(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


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
        await s.execute(
            delete(RoleAssignment).where(
                RoleAssignment.role_id.in_(select(Role.id).where(Role.name == _ADMIN))
            )
        )
        await s.execute(delete(StorageConfig))  # reset G-B (S8b) so it starts unsatisfied
        org = (await s.execute(select(Organization))).scalar_one()
        org.short_code = "DEFAULT"
        org.legal_name = "EasySynQ (configure in setup)"
        await s.commit()
    async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as r:
        await r.delete(setup_service._RL_KEY)
    return secret


async def _bootstrap(client: AsyncClient, h: dict[str, str], secret: str) -> dict:
    r = await client.post("/api/v1/setup/bootstrap", headers=h, json={"secret": secret})
    assert r.status_code == 200, r.text
    return r.json()


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
    """The secret grants the caller System Administrator + advances to IN_SETUP, writing the
    BOOTSTRAP_CONSUMED + ADMIN_BOOTSTRAPPED audit rows — the in-app replacement for grant-role."""
    secret = await _reset_uninitialized()
    sub = _sub("admin")
    h = _auth(token_factory, sub)

    body = await _bootstrap(app_client, h, secret)
    assert body["setup_state"] == "IN_SETUP"
    admin_id = uuid.UUID(body["admin_user_id"])

    async with get_sessionmaker()() as s:
        assigned = await s.scalar(
            select(RoleAssignment.id)
            .join(Role, RoleAssignment.role_id == Role.id)
            .join(AppUser, RoleAssignment.user_id == AppUser.id)
            .where(AppUser.keycloak_subject == sub, Role.name == _ADMIN)
        )
        assert assigned is not None
        consumed = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.BOOTSTRAP_CONSUMED,
                AuditEvent.actor_id == admin_id,
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


async def test_bootstrap_rejects_wrong_secret_and_replay(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    secret = await _reset_uninitialized()
    h = _auth(token_factory, _sub("x"))

    bad = await app_client.post("/api/v1/setup/bootstrap", headers=h, json={"secret": "wrong"})
    assert bad.status_code == 403
    assert bad.json()["code"] == "bootstrap_invalid"

    await _bootstrap(app_client, h, secret)  # consumes it
    replay = await app_client.post("/api/v1/setup/bootstrap", headers=h, json={"secret": secret})
    assert replay.status_code == 409
    assert replay.json()["code"] == "bootstrap_already_consumed"


async def test_org_profile_requires_admin(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """org-profile is gated on config.update — a non-admin is 403; the admin succeeds."""
    secret = await _reset_uninitialized()
    payload = {"legal_name": "Acme Corp", "short_code": "ACME", "timezone": "Europe/London"}

    h_other = _auth(token_factory, _sub("other"))
    forbidden = await app_client.patch("/api/v1/setup/org-profile", headers=h_other, json=payload)
    assert forbidden.status_code == 403

    h_admin = _auth(token_factory, _sub("admin"))
    await _bootstrap(app_client, h_admin, secret)
    ok = await app_client.patch("/api/v1/setup/org-profile", headers=h_admin, json=payload)
    assert ok.status_code == 200, ok.text
    assert ok.json()["short_code"] == "ACME"
    async with get_sessionmaker()() as s:
        org = (await s.execute(select(Organization))).scalar_one()
        assert org.short_code == "ACME"
        assert org.timezone == "Europe/London"


async def test_org_profile_rejects_default_short_code(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    secret = await _reset_uninitialized()
    h = _auth(token_factory, _sub("admin"))
    await _bootstrap(app_client, h, secret)
    r = await app_client.patch(
        "/api/v1/setup/org-profile",
        headers=h,
        json={"legal_name": "Acme", "short_code": "DEFAULT", "timezone": "UTC"},
    )
    assert r.status_code == 422


async def test_finalize_blocked_then_operational_lifts_latch(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[HEADLINE] Finalize is blocked until G-E (org profile) passes; once it does, the latch flips
    to OPERATIONAL (SETUP_FINALIZED audited) and the QMS surface is no longer 423."""
    secret = await _reset_uninitialized()
    h = _auth(token_factory, _sub("fin"))
    body = await _bootstrap(app_client, h, secret)  # G-A satisfied
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
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The brute-force throttle: 5 failed attempts each 403, the 6th is 429 rate_limited."""
    await _reset_uninitialized()
    h = _auth(token_factory, _sub("rl"))
    for _ in range(5):
        bad = await app_client.post("/api/v1/setup/bootstrap", headers=h, json={"secret": "wrong"})
        assert bad.status_code == 403, bad.text
    locked = await app_client.post("/api/v1/setup/bootstrap", headers=h, json={"secret": "wrong"})
    assert locked.status_code == 429
    assert locked.json()["code"] == "rate_limited"


async def test_bootstrap_rejects_expired_secret(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    secret = await _reset_uninitialized()
    async with get_sessionmaker()() as s:
        cfg = (await s.execute(select(SystemConfig))).scalar_one()
        cfg.bootstrap_expires_at = setup_service._now() - datetime.timedelta(minutes=1)
        await s.commit()
    h = _auth(token_factory, _sub("exp"))
    r = await app_client.post("/api/v1/setup/bootstrap", headers=h, json={"secret": secret})
    assert r.status_code == 403
    assert r.json()["code"] == "bootstrap_expired"


async def test_bootstrap_rejects_when_no_secret_minted(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    await _reset_uninitialized()
    async with get_sessionmaker()() as s:
        cfg = (await s.execute(select(SystemConfig))).scalar_one()
        cfg.bootstrap_secret_hash = None
        await s.commit()
    h = _auth(token_factory, _sub("ns"))
    r = await app_client.post("/api/v1/setup/bootstrap", headers=h, json={"secret": "anything"})
    assert r.status_code == 409
    assert r.json()["code"] == "no_bootstrap_secret"


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
    h = _auth(token_factory, _sub("worm"))
    body = await _bootstrap(app_client, h, secret)
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
    h = _auth(token_factory, _sub("gb"))
    await _bootstrap(app_client, h, secret)
    await app_client.patch(
        "/api/v1/setup/org-profile",
        headers=h,
        json={"legal_name": "Acme", "short_code": "ACME", "timezone": "UTC"},
    )
    blocked = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert blocked.status_code == 409
    assert any(g["key"] == "G-B" for g in blocked.json()["failed_gates"])

    await _verify_storage(app_client, h)
    done = await app_client.post("/api/v1/setup/finalize", headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["setup_state"] == "OPERATIONAL"


async def test_verify_storage_rerun_updates_in_place(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Re-running verify-storage (resumable wizard: re-click / switch mode) UPDATEs the single
    storage_config row in place — not a second INSERT (which would 500 on UNIQUE(org_id))."""
    secret = await _reset_uninitialized()
    h = _auth(token_factory, _sub("rerun"))
    await _bootstrap(app_client, h, secret)

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
