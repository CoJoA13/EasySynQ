"""S8d integration proofs — the Users & Roles admin surface (roster / invite / enable-disable +
the reused S2 role-assign), on testcontainers PG/MinIO/Redis (no Keycloak — JWTs are minted).

The conftest defaults the shared DB to OPERATIONAL, so /api/v1/users is not latched. Each test mints
its own fresh System Administrator (via s5.grant_role), so cross-test admin state is benign.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_vault import _auth

pytestmark = pytest.mark.integration

_ADMIN = "System Administrator"


def _sub(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _admin(token_factory: Callable[..., str]) -> tuple[dict[str, str], str]:
    """A fresh System Administrator caller (holds user.* + permission.grant at SYSTEM tier)."""
    sub = _sub("admin")
    await s5.grant_role(sub, _ADMIN)
    return _auth(token_factory, sub), sub


async def _jit(
    app_client: AsyncClient, token_factory: Callable[..., str], sub: str
) -> dict[str, str]:
    """JIT-provision a plain (role-less, ACTIVE) user by calling /me, return its headers."""
    h = _auth(token_factory, sub)
    await app_client.get("/api/v1/me", headers=h)
    return h


async def test_list_users_requires_user_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    h_other = await _jit(app_client, token_factory, _sub("nobody"))
    forbidden = await app_client.get("/api/v1/users", headers=h_other)
    assert forbidden.status_code == 403

    h_admin, admin_sub = await _admin(token_factory)
    ok = await app_client.get("/api/v1/users", headers=h_admin)
    assert ok.status_code == 200
    me = next(u for u in ok.json() if u["keycloak_subject"] == admin_sub)
    assert _ADMIN in me["roles"]


async def test_invite_creates_invited_and_audits(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    h_admin, _ = await _admin(token_factory)
    sub = _sub("invitee")
    r = await app_client.post(
        "/api/v1/users",
        headers=h_admin,
        json={"keycloak_subject": sub, "display_name": "Invitee", "email": "inv@example.io"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "INVITED"
    user_id = uuid.UUID(r.json()["id"])

    dup = await app_client.post("/api/v1/users", headers=h_admin, json={"keycloak_subject": sub})
    assert dup.status_code == 409
    assert dup.json()["code"] == "user_exists"

    async with get_sessionmaker()() as s:
        ev = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.USER_CREATED, AuditEvent.object_id == user_id
            )
        )
    assert ev is not None

    h_other = await _jit(app_client, token_factory, _sub("nocreate"))
    forbidden = await app_client.post(
        "/api/v1/users", headers=h_other, json={"keycloak_subject": _sub("z")}
    )
    assert forbidden.status_code == 403


async def test_invited_reconciles_to_active_on_first_login(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The pre-created INVITED row flips to ACTIVE on the subject's first genuine login (JIT)."""
    h_admin, _ = await _admin(token_factory)
    sub = _sub("recon")
    inv = await app_client.post("/api/v1/users", headers=h_admin, json={"keycloak_subject": sub})
    assert inv.json()["status"] == "INVITED"

    me = await app_client.get("/api/v1/me", headers=_auth(token_factory, sub))
    assert me.status_code == 200
    assert me.json()["status"] == "ACTIVE"


async def test_disable_blocks_then_reenable(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    h_admin, _ = await _admin(token_factory)
    target_sub = _sub("target")
    h_target = await _jit(app_client, token_factory, target_sub)

    roster = (await app_client.get("/api/v1/users", headers=h_admin)).json()
    tid = next(u["id"] for u in roster if u["keycloak_subject"] == target_sub)

    disabled = await app_client.patch(
        f"/api/v1/users/{tid}", headers=h_admin, json={"status": "DISABLED"}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "DISABLED"
    # The disabled user is rejected on their next request.
    assert (await app_client.get("/api/v1/me", headers=h_target)).status_code == 403

    async with get_sessionmaker()() as s:
        ev = await s.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == EventType.USER_STATUS_CHANGED,
                AuditEvent.object_id == uuid.UUID(tid),
            )
        )
    assert ev is not None

    reenabled = await app_client.patch(
        f"/api/v1/users/{tid}", headers=h_admin, json={"status": "ACTIVE"}
    )
    assert reenabled.json()["status"] == "ACTIVE"
    assert (await app_client.get("/api/v1/me", headers=h_target)).status_code == 200

    # A non-admin cannot toggle status.
    forbidden = await app_client.patch(
        f"/api/v1/users/{tid}", headers=h_target, json={"status": "DISABLED"}
    )
    assert forbidden.status_code == 403


async def test_cannot_disable_sole_active_admin(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The break-glass guard (doc 08 §9.1): the last active System Administrator can't be off."""
    h_admin, admin_sub = await _admin(token_factory)
    roster = (await app_client.get("/api/v1/users", headers=h_admin)).json()
    me = next(u for u in roster if u["keycloak_subject"] == admin_sub)
    # Disable every OTHER active admin so `me` is the sole one (deterministic in the shared session
    # DB where prior tests left admins; each test mints its own fresh admin, so this is harmless).
    for u in roster:
        if u["id"] != me["id"] and _ADMIN in u["roles"] and u["status"] == "ACTIVE":
            resp = await app_client.patch(
                f"/api/v1/users/{u['id']}", headers=h_admin, json={"status": "DISABLED"}
            )
            assert resp.status_code == 200, resp.text

    blocked = await app_client.patch(
        f"/api/v1/users/{me['id']}", headers=h_admin, json={"status": "DISABLED"}
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "last_admin"


async def test_admin_assigns_seeded_role_visible_in_roster(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """End-to-end admin flow: invite → assign a seeded role via the reused S2 endpoint → the roster
    reflects it (and effective-permissions confirms the grant landed)."""
    h_admin, _ = await _admin(token_factory)
    sub = _sub("mara")
    invited = await app_client.post(
        "/api/v1/users", headers=h_admin, json={"keycloak_subject": sub}
    )
    uid = invited.json()["id"]

    assigned = await app_client.post(
        f"/api/v1/users/{uid}/roles", headers=h_admin, json={"role_name": "QMS Owner"}
    )
    assert assigned.status_code == 201, assigned.text

    roster = (await app_client.get("/api/v1/users", headers=h_admin)).json()
    row = next(u for u in roster if u["id"] == uid)
    assert "QMS Owner" in row["roles"]

    eff = await app_client.get(f"/api/v1/users/{uid}/effective-permissions", headers=h_admin)
    assert eff.status_code == 200
    assert any(p["effect"] == "ALLOW" for p in eff.json()["permissions"])
