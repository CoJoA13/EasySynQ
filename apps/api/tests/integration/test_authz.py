"""S2 integration proofs — the seeded catalog/roles, PEP enforcement, the two-tier guard,
the audit hook, and AC#3 re-proven through the real DB→PEP→PDP path under testcontainers.

Grants are seeded directly in the DB (the first-admin bootstrap is an S8 concern); the
endpoints are then exercised over HTTP against the migrated testcontainer Postgres. Subjects
are unique per test (the session-scoped container persists) so tests stay isolated while the
read-only seed (permissions + roles) is shared.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz import RequestContext, ResourceContext, authorize
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.authz import (
    disable_removes_last_admin,
    gather_grants,
    revoke_removes_last_admin,
)

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
        "Register Steward",  # the register-head lifecycle steward (S-register-steward, R52, 0062)
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

    # S-records-C (migration 0057): the Process Owner role gains clauseMap.read at SYSTEM (the
    # create-in-process wizard's clause step) — a NEW grant on an EXISTING key (catalog stays 102,
    # R38 additive). SYSTEM scope so a bound owner's PROCESS bound_scope cannot clamp it away.
    process_owner = next(r for r in roles if r["name"] == "Process Owner")
    po_grants = (await app_client.get(f"/api/v1/roles/{process_owner['id']}", headers=h)).json()[
        "grants"
    ]
    clausemap = next(g for g in po_grants if g["permission_key"] == "clauseMap.read")
    assert clausemap["scope_template"]["level"] == "SYSTEM"

    # S-risk-1 (migration 0058): the Process Owner role gains register.manage at PROCESS (the risk
    # register's row CRUD) — a NEW grant on an EXISTING key (catalog stays 102, R38 additive).
    # PROCESS
    # scope, NOT SYSTEM: a SYSTEM template is exempt from bound_scope clamping (authz/repository)
    # and
    # would let a bound owner manage every process's risks — the OPPOSITE of clauseMap.read (an
    # org-level resource → SYSTEM). The bound owner's bound_scope clamps the :assignment_process
    # placeholder to owned processes.
    register_manage = next(g for g in po_grants if g["permission_key"] == "register.manage")
    assert register_manage["scope_template"]["level"] == "PROCESS"

    # S-register-steward (migration 0062, R52): a NEW reserved Register Steward role holds the full
    # register stewardship set at SYSTEM — the FIRST seeded role to hold document.release (release
    # was SYSTEM-override-only in v1). It EXCLUDES document.approve (SoD: the approver stays
    # separate). No new key (catalog stays 102 — asserted above).
    steward = next(r for r in roles if r["name"] == "Register Steward")
    assert steward["is_reserved"] is True
    steward_grants = {
        g["permission_key"]: g["scope_template"]["level"]
        for g in (await app_client.get(f"/api/v1/roles/{steward['id']}", headers=h)).json()[
            "grants"
        ]
    }
    assert steward_grants == {
        "register.read": "SYSTEM",
        "register.manage": "SYSTEM",
        "document.release": "SYSTEM",
        "document.read": "SYSTEM",
        "document.read_draft": "SYSTEM",
    }
    assert "document.approve" not in steward_grants  # SoD: the approver stays a separate role


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


async def _add_permission_grant_override(
    subject: str, *, valid_until: datetime.datetime | None
) -> None:
    """Give ``subject`` a non-``content_only`` SYSTEM ``permission.grant`` override (the would-be
    system-tier grant). ``valid_until`` in the past makes it lapsed; None makes it live."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == "permission.grant"))
        ).scalar_one()
        scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)  # no content_only → system tier
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
                valid_until=valid_until,
            )
        )
        await s.commit()


async def test_two_tier_lapsed_system_override_does_not_elevate(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """CR-3: a LAPSED (or not-yet-valid) non-``content_only`` ``permission.grant`` override must NOT
    confer system-tier authority. The old raw ``any(effect is ALLOW and not content_only)`` scan
    counted the expired ALLOW — permanently elevating a content-tier grantor across the R35
    ADMIN/QMS boundary; running the grant through the PDP drops it on ``valid_until``."""
    # Grantor = QMS Owner (a LIVE content_only permission.grant → passes require())
    # PLUS a LAPSED non-content_only permission.grant SYSTEM override (the wrongly-counted grant).
    await _assign_role(subj.qms, "QMS Owner")
    await _add_permission_grant_override(
        subj.qms, valid_until=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    )
    sam_id = await _ensure_user_id(subj.sam)
    qms_h = _auth(token_factory, subj.qms)
    system_grant = {"permission_key": "user.read", "effect": "ALLOW", "scope": {"level": "SYSTEM"}}

    # The lapsed override confers no system tier → the two-tier guard still refuses a SYSTEM grant.
    blocked = await app_client.post(
        f"/api/v1/users/{sam_id}/overrides", headers=qms_h, json=system_grant
    )
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["code"] == "two_tier_violation"

    # Positive control (no over-restriction): a LIVE non-content_only override makes
    # the grantor genuinely system-tier, so the same SYSTEM grant now succeeds.
    await _add_permission_grant_override(subj.qms, valid_until=None)
    ok = await app_client.post(
        f"/api/v1/users/{sam_id}/overrides", headers=qms_h, json=system_grant
    )
    assert ok.status_code == 201, ok.text


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


# --- two-tier REVOKE guard (R35, revoke side) --------------------------------------------


async def test_two_tier_revoke_role_and_delete_override(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Revoke-side R35: a content-tier grantor must not revoke a system-domain role NOR delete a
    system-domain override (both re-shape system access), and the denial must be AUDITED — a bare
    check+422 would leave the trail showing only the outer require()'s ALLOW. Pre-fix both revokes
    had no two-tier guard, so the content-tier QMS Owner succeeded (204). Positive control: the
    Admin (system-tier) may do both."""
    qms_id = await _assign_role(subj.qms, "QMS Owner")  # content-tier permission.grant
    await _assign_role(subj.admin, "System Administrator")  # system-tier permission.grant
    sam_id = await _assign_role(subj.sam, "System Administrator")  # target: a system-domain role
    await _add_override(
        subj.sam, "user.read", "ALLOW", "SYSTEM"
    )  # target: a system-domain override
    mara_h = _auth(token_factory, subj.qms)
    avery_h = _auth(token_factory, subj.admin)

    sam_roles = (await app_client.get(f"/api/v1/users/{sam_id}/roles", headers=avery_h)).json()
    sa_assignment = next(r["id"] for r in sam_roles if r["role_name"] == "System Administrator")
    sam_ovrs = (await app_client.get(f"/api/v1/users/{sam_id}/overrides", headers=avery_h)).json()
    sys_override = next(o["id"] for o in sam_ovrs if o["permission_key"] == "user.read")

    # Content-tier QMS Owner: BOTH revokes are refused with 422 two_tier_violation...
    r_role = await app_client.delete(
        f"/api/v1/users/{sam_id}/roles/{sa_assignment}", headers=mara_h
    )
    assert r_role.status_code == 422, r_role.text
    assert r_role.json()["code"] == "two_tier_violation"
    r_ovr = await app_client.delete(
        f"/api/v1/users/{sam_id}/overrides/{sys_override}", headers=mara_h
    )
    assert r_ovr.status_code == 422, r_ovr.text
    assert r_ovr.json()["code"] == "two_tier_violation"

    # ...and each denial is durably AUDITED (DbAuthzAuditSink commits on its own session, so the row
    # survives the request's ProblemException rollback) — TWO_TIER_VIOLATION, actor = the grantor.
    async with get_sessionmaker()() as s:
        denies = await s.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.event_type == EventType.TWO_TIER_VIOLATION,
                AuditEvent.actor_id == qms_id,
            )
        )
    assert denies is not None and denies >= 2

    # Positive control (no over-restriction): the Admin (system-tier) may revoke + delete.
    ok_role = await app_client.delete(
        f"/api/v1/users/{sam_id}/roles/{sa_assignment}", headers=avery_h
    )
    assert ok_role.status_code == 204, ok_role.text
    ok_ovr = await app_client.delete(
        f"/api/v1/users/{sam_id}/overrides/{sys_override}", headers=avery_h
    )
    assert ok_ovr.status_code == 204, ok_ovr.text


# --- last-admin serialisation across the two removal paths --------------------------------


async def test_admin_removal_paths_serialise_under_one_lock(
    app_under_test: object, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Finding-1 hardening: the disable and role-revoke paths take ONE org-scoped transaction lock,
    so two CONCURRENT removals of the last two admins can't both win (→ self-hosted lockout). With
    the lock the second op reads the freshly-committed set and is refused; without it both read the
    peer as still-active before either commits and both proceed (the set is emptied)."""
    a_id = await _assign_role(subj.admin, "System Administrator")
    b_id = await _assign_role(subj.sam, "System Administrator")
    async with get_sessionmaker()() as s:
        org_id = (await s.execute(select(AppUser.org_id).where(AppUser.id == a_id))).scalar_one()
        b_assignment_id = (
            await s.execute(
                select(RoleAssignment.id)
                .join(Role, Role.id == RoleAssignment.role_id)
                .where(RoleAssignment.user_id == b_id, Role.name == "System Administrator")
            )
        ).scalar_one()
        # Make A and B the ONLY active admins in the org so both racing ops target the last two.
        others = (
            (
                await s.execute(
                    select(AppUser.id)
                    .join(RoleAssignment, RoleAssignment.user_id == AppUser.id)
                    .join(Role, Role.id == RoleAssignment.role_id)
                    .where(
                        AppUser.org_id == org_id,
                        Role.name == "System Administrator",
                        AppUser.status == UserStatus.ACTIVE,
                        AppUser.id.notin_([a_id, b_id]),
                    )
                )
            )
            .scalars()
            .all()
        )
        for uid in set(others):
            u = await s.get(AppUser, uid)
            if u is not None:
                u.status = UserStatus.DISABLED
        await s.commit()

    async def do_disable_a() -> bool:
        async with get_sessionmaker()() as s:
            refused = await disable_removes_last_admin(s, org_id, a_id)
            if not refused:
                u = await s.get(AppUser, a_id)
                assert u is not None
                u.status = UserStatus.DISABLED
                await s.commit()
            return refused

    async def do_revoke_b() -> bool:
        async with get_sessionmaker()() as s:
            assignment = await s.get(RoleAssignment, b_assignment_id)
            assert assignment is not None
            refused = await revoke_removes_last_admin(s, assignment)
            if not refused:
                await s.delete(assignment)
                await s.commit()
            return refused

    refusals = await asyncio.gather(do_disable_a(), do_revoke_b())
    # Exactly ONE op is refused → the admin set is never emptied (no lock → both proceed → sum 0).
    assert sum(refusals) == 1, refusals
    # And exactly one of the last two admins survives (A still active, or B still holding the role).
    async with get_sessionmaker()() as s:
        remaining = (
            (
                await s.execute(
                    select(AppUser.id)
                    .join(RoleAssignment, RoleAssignment.user_id == AppUser.id)
                    .join(Role, Role.id == RoleAssignment.role_id)
                    .where(
                        AppUser.org_id == org_id,
                        Role.name == "System Administrator",
                        AppUser.status == UserStatus.ACTIVE,
                        AppUser.id.in_([a_id, b_id]),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(set(remaining)) == 1, remaining
