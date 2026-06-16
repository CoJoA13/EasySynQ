"""S2 integration proofs — the seeded catalog/roles, PEP enforcement, the two-tier guard,
the audit hook, and AC#3 re-proven through the real DB→PEP→PDP path under testcontainers.

Grants are seeded directly in the DB (the first-admin bootstrap is an S8 concern); the
endpoints are then exercised over HTTP against the migrated testcontainer Postgres. Subjects
are unique per test (the session-scoped container persists) so tests stay isolated while the
read-only seed (permissions + roles) is shared.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz import RequestContext, ResourceContext, authorize
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.authz import gather_grants

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    """Fresh, collision-free Keycloak subjects for one test."""
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(
        admin=f"kc-avery-{salt}",
        qms=f"kc-mara-{salt}",
        sam=f"kc-sam-{salt}",
        priya=f"kc-priya-{salt}",
        nobody=f"kc-nobody-{salt}",
        nobody2=f"kc-nobody2-{salt}",
    )


def _auth(token_factory: Callable[..., str], subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_factory(subject)}"}


async def _ensure_user(session: object, subject: str) -> AppUser:
    s = session  # AsyncSession; loosely typed (tests are not under mypy)
    user = (
        await s.execute(select(AppUser).where(AppUser.keycloak_subject == subject))
    ).scalar_one_or_none()
    if user is None:
        org_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()
        user = AppUser(
            org_id=org_id, keycloak_subject=subject, display_name=subject, status=UserStatus.ACTIVE
        )
        s.add(user)
        await s.flush()
    return user


async def _ensure_user_id(subject: str) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        await s.commit()
        return user.id


async def _assign_role(subject: str, role_name: str, bound_scope: dict | None = None) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        role = (await s.execute(select(Role).where(Role.name == role_name))).scalar_one()
        s.add(
            RoleAssignment(
                org_id=user.org_id, user_id=user.id, role_id=role.id, bound_scope=bound_scope
            )
        )
        await s.commit()
        return user.id


async def _add_override(
    subject: str,
    permission_key: str,
    effect: str,
    level: str,
    selector: dict | None = None,
    predicates: dict | None = None,
) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == permission_key))
        ).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel(level), selector=selector, predicates=predicates
        )
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect(effect),
                scope_id=scope.id,
            )
        )
        await s.commit()
        return user.id


# --- seed correctness -------------------------------------------------------------------


async def test_seed_catalog_and_roles(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _assign_role(subj.admin, "System Administrator")
    h = _auth(token_factory, subj.admin)

    perms = (await app_client.get("/api/v1/permissions", headers=h)).json()
    # 96 closed v1 keys + the 2 additive retention.* keys (0028) + drift.read (0047)
    # + document.distribute (0048) + the 2 additive improvement.* keys (0052) — R38/R42/R46.
    assert len(perms) == 102
    by_key = {p["key"]: p for p in perms}
    assert by_key["user.read"]["is_system_domain"] is True
    assert by_key["document.read"]["is_system_domain"] is False
    assert by_key["document.approve"]["sig_hook"] is True
    assert by_key["document.read"]["sig_hook"] is False
    # R38: the additive retention-policy keys are CONTENT-domain, non-sig-hook, non-SoD-sensitive.
    for key in ("retention.read", "retention.manage"):
        assert by_key[key]["is_system_domain"] is False
        assert by_key[key]["sig_hook"] is False
        assert by_key[key]["sod_sensitive"] is False
    # R38/R41: drift.read is SYSTEM-domain (admin-side operational read), non-sig-hook.
    assert by_key["drift.read"]["is_system_domain"] is True
    assert by_key["drift.read"]["sig_hook"] is False
    assert by_key["drift.read"]["sod_sensitive"] is False
    # R38/R42: document.distribute is CONTENT-domain, ARTIFACT-finest, non-sig-hook, non-SoD.
    assert by_key["document.distribute"]["is_system_domain"] is False
    assert by_key["document.distribute"]["sig_hook"] is False
    assert by_key["document.distribute"]["sod_sensitive"] is False

    roles = (await app_client.get("/api/v1/roles", headers=h)).json()
    names = {r["name"] for r in roles}
    assert names == {
        "System Administrator",
        "QMS Owner",
        "Process Owner",
        "Author",
        "Approver",
        "Internal Auditor",
        "Employee (Read-only)",
        "External Auditor (Guest)",
        "Top Management",  # the Critical CAPA action-plan second-tier approver (S-capa-2, 0038)
    }

    approver = next(r for r in roles if r["name"] == "Approver")
    approver_keys = {
        g["permission_key"]
        for g in (await app_client.get(f"/api/v1/roles/{approver['id']}", headers=h)).json()[
            "grants"
        ]
    }
    assert "document.approve" in approver_keys
    assert "document.edit" not in approver_keys  # SoD: approver cannot edit

    admin_role = next(r for r in roles if r["name"] == "System Administrator")
    admin_keys = {
        g["permission_key"]
        for g in (await app_client.get(f"/api/v1/roles/{admin_role['id']}", headers=h)).json()[
            "grants"
        ]
    }
    assert "user.read" in admin_keys
    assert not any(k.startswith(("document.", "record.", "capa.")) for k in admin_keys)


# --- PEP enforcement --------------------------------------------------------------------


async def test_endpoint_denied_without_permission(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    denied = await app_client.get("/api/v1/roles", headers=_auth(token_factory, subj.nobody))
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"

    await _assign_role(subj.admin, "System Administrator")
    allowed = await app_client.get("/api/v1/roles", headers=_auth(token_factory, subj.admin))
    assert allowed.status_code == 200


# --- AC#4: system super-admin denied content authority ----------------------------------


async def test_admin_system_star_denied_content(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    admin_id = await _assign_role(subj.admin, "System Administrator")
    eff = (
        await app_client.get(
            f"/api/v1/users/{admin_id}/effective-permissions",
            headers=_auth(token_factory, subj.admin),
        )
    ).json()
    keys = {p["key"] for p in eff["permissions"]}
    assert "user.read" in keys  # holds system.*
    assert "document.approve" not in keys  # but NOT content authority (AC#4)
    assert "document.release" not in keys

    # Contrast: an Approver DOES hold document.approve — so the Admin's denial is the bundle's
    # absence of the grant, not a scope or evaluator quirk.
    approver_id = await _assign_role(
        subj.sam, "Approver", {"level": "DOC_CLASS", "selector": {"document_level": "L2_PROCEDURE"}}
    )
    approver_eff = (
        await app_client.get(
            f"/api/v1/users/{approver_id}/effective-permissions",
            params={"scope_level": "DOC_CLASS", "scope_id": "L2_PROCEDURE"},
            headers=_auth(token_factory, subj.admin),
        )
    ).json()
    assert "document.approve" in {p["key"] for p in approver_eff["permissions"]}


# --- AC#3: per-user DENY beats role ALLOW (real DB -> PEP -> PDP) ------------------------


async def test_per_user_deny_beats_role_allow(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    priya_id = await _assign_role(
        subj.priya, "Author", {"level": "FOLDER", "selector": {"folder_path": "SOPs.Purchasing"}}
    )
    await _add_override(
        subj.priya, "document.edit", "DENY", "ARTIFACT", selector={"artifact_id": "SOP-PUR-009"}
    )

    async with get_sessionmaker()() as s:
        user = (await s.execute(select(AppUser).where(AppUser.id == priya_id))).scalar_one()
        grants = await gather_grants(s, user.id, user.org_id, "document.edit")

    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC))
    denied = authorize(
        grants,
        "document.edit",
        ResourceContext(artifact_id="SOP-PUR-009", folder_path="SOPs.Purchasing"),
        ctx,
    )
    allowed = authorize(
        grants,
        "document.edit",
        ResourceContext(artifact_id="SOP-PUR-003", folder_path="SOPs.Purchasing"),
        ctx,
    )
    assert denied.allow is False
    assert denied.reason == "explicit_deny"
    assert denied.source == "user_override"
    assert allowed.allow is True
    assert allowed.source == "role:Author"


# --- two-tier grant guard (R35) ---------------------------------------------------------


async def test_two_tier_violation(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _assign_role(subj.qms, "QMS Owner")  # content-tier permission.grant
    await _assign_role(subj.admin, "System Administrator")  # system-tier permission.grant
    sam_id = await _ensure_user_id(subj.sam)
    mara_h = _auth(token_factory, subj.qms)
    avery_h = _auth(token_factory, subj.admin)

    content = {"permission_key": "document.read", "effect": "ALLOW", "scope": {"level": "SYSTEM"}}
    system = {"permission_key": "user.read", "effect": "ALLOW", "scope": {"level": "SYSTEM"}}

    # QMS Owner may grant a CONTENT permission (and the created override is returned)...
    r1 = await app_client.post(f"/api/v1/users/{sam_id}/overrides", headers=mara_h, json=content)
    assert r1.status_code == 201, r1.text
    assert r1.json()["permission_key"] == "document.read"
    assert r1.json()["effect"] == "ALLOW"
    # ...but NOT a SYSTEM-domain one -> 422 two_tier_violation.
    r2 = await app_client.post(f"/api/v1/users/{sam_id}/overrides", headers=mara_h, json=system)
    assert r2.status_code == 422
    assert r2.json()["code"] == "two_tier_violation"
    # The Admin (system-tier) may grant the same SYSTEM permission...
    r3 = await app_client.post(f"/api/v1/users/{sam_id}/overrides", headers=avery_h, json=system)
    assert r3.status_code == 201, r3.text
    assert r3.json()["permission_key"] == "user.read"
    # ...and may also grant CONTENT permissions (system tier can grant down).
    r4 = await app_client.post(
        f"/api/v1/users/{sam_id}/overrides",
        headers=avery_h,
        json={"permission_key": "document.export", "effect": "ALLOW", "scope": {"level": "SYSTEM"}},
    )
    assert r4.status_code == 201, r4.text

    roles = (await app_client.get("/api/v1/roles", headers=avery_h)).json()
    role_id = {r["name"]: r["id"] for r in roles}
    # A role bundling system permissions cannot be assigned by the content-tier grantor...
    blocked = await app_client.post(
        f"/api/v1/users/{sam_id}/roles",
        headers=mara_h,
        json={"role_id": role_id["System Administrator"]},
    )
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "two_tier_violation"
    # ...but a content-only role (Author) may be assigned by the content-tier grantor.
    ok = await app_client.post(
        f"/api/v1/users/{sam_id}/roles", headers=mara_h, json={"role_id": role_id["Author"]}
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["role_name"] == "Author"


# --- override round-trip reflected in effective-permissions (deny-wins) ------------------


async def test_override_reflected_then_denied(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _assign_role(subj.admin, "System Administrator")
    sam_id = await _ensure_user_id(subj.sam)
    h = _auth(token_factory, subj.admin)

    await app_client.post(
        f"/api/v1/users/{sam_id}/overrides",
        headers=h,
        json={"permission_key": "document.read", "effect": "ALLOW", "scope": {"level": "SYSTEM"}},
    )
    eff = (await app_client.get(f"/api/v1/users/{sam_id}/effective-permissions", headers=h)).json()[
        "permissions"
    ]
    by_key = {p["key"]: p for p in eff}
    assert by_key["document.read"]["effect"] == "ALLOW"
    assert by_key["document.read"]["source"] == "user_override"

    # A DENY override on the same permission wins (deny-always-wins).
    await app_client.post(
        f"/api/v1/users/{sam_id}/overrides",
        headers=h,
        json={"permission_key": "document.read", "effect": "DENY", "scope": {"level": "SYSTEM"}},
    )
    eff2 = (
        await app_client.get(f"/api/v1/users/{sam_id}/effective-permissions", headers=h)
    ).json()["permissions"]
    assert {p["key"]: p for p in eff2}["document.read"]["effect"] == "DENY"


# --- audit hook fires on allow AND deny -------------------------------------------------


async def test_audit_hook_on_allow_and_deny(
    app_client: AsyncClient,
    app_under_test: object,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    from easysynq_api.services.authz import CapturingAuthzAuditSink, get_authz_audit_sink

    sink = CapturingAuthzAuditSink()
    app_under_test.dependency_overrides[get_authz_audit_sink] = lambda: sink  # type: ignore[attr-defined]

    await _assign_role(subj.admin, "System Administrator")
    await app_client.get("/api/v1/roles", headers=_auth(token_factory, subj.admin))  # allow
    await app_client.get("/api/v1/roles", headers=_auth(token_factory, subj.nobody2))  # deny

    allow = next(
        e for e in sink.events if e.permission_key == "role.read" and e.decision == "allow"
    )
    deny = next(e for e in sink.events if e.permission_key == "role.read" and e.decision == "deny")
    # The hook captures the decision context, not just the verdict.
    assert allow.reason == "allow"
    assert deny.reason == "deny_by_default"
    assert allow.actor_id and allow.org_id
    assert deny.actor_id and deny.org_id
