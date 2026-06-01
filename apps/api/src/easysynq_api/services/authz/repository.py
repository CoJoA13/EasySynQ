"""Grant gathering: turn DB rows into the PDP's ``ResolvedGrant``s.

For a ``(user, permission_key)`` this collects every grant reaching the user —
role-derived (``role_assignment`` → ``role_grant``) and direct (``permission_override``
→ ``scope``) — and resolves each to a concrete scope the pure PDP can evaluate. Role
grants are always ALLOW (roles bundle allows; denies arrive via overrides). A
``role_assignment.bound_scope`` concretizes the role's parameterized ``scope_template``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.authz_grant import PermissionOverride
from ...db.models.permission import Permission
from ...db.models.role import Role, RoleAssignment, RoleGrant
from ...db.models.scope import Scope
from ...db.models.sod import SodConstraint
from ...db.models.system_config import SystemConfig
from ...domain.authz.types import Effect, ResolvedGrant, ScopeLevel


async def get_permission(session: AsyncSession, key: str) -> Permission | None:
    return (
        await session.execute(select(Permission).where(Permission.key == key))
    ).scalar_one_or_none()


def _scope_from_dict(
    raw: dict[str, Any] | None,
) -> tuple[ScopeLevel, dict[str, Any], dict[str, Any]]:
    data = raw or {"level": "SYSTEM"}
    level = ScopeLevel(data.get("level", "SYSTEM"))
    selector = data.get("selector") or {}
    predicates = data.get("predicates") or {}
    return level, dict(selector), dict(predicates)


def _grant_from_role(role_name: str, grant: RoleGrant, assignment: RoleAssignment) -> ResolvedGrant:
    # bound_scope (concrete, set at assignment) wins over the role's scope_template default.
    raw = assignment.bound_scope or grant.scope_template
    level, selector, predicates = _scope_from_dict(raw)
    return ResolvedGrant(
        effect=Effect.ALLOW,
        level=level,
        selector=selector,
        predicates=predicates,
        source=f"role:{role_name}",
        is_override=False,
    )


def _grant_from_override(override: PermissionOverride, scope: Scope) -> ResolvedGrant:
    predicates: dict[str, Any] = dict(scope.predicates or {})
    predicates.update(override.predicates or {})
    if override.valid_from is not None:
        predicates["valid_from"] = override.valid_from
    if override.valid_until is not None:
        predicates["valid_until"] = override.valid_until
    if override.require_reason:
        predicates["require_reason"] = True
    return ResolvedGrant(
        effect=override.effect,
        level=scope.level,
        selector=dict(scope.selector or {}),
        predicates=predicates,
        source="user_override",
        is_override=True,
    )


async def gather_grants(
    session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, permission_key: str
) -> list[ResolvedGrant]:
    """All grants reaching ``user`` for ``permission_key``, resolved for the PDP. Empty
    list if the permission key is unknown (→ deny-by-default at the PDP)."""
    permission = await get_permission(session, permission_key)
    if permission is None:
        return []

    grants: list[ResolvedGrant] = []

    role_rows = (
        await session.execute(
            select(Role.name, RoleGrant, RoleAssignment)
            .join(RoleAssignment, RoleAssignment.role_id == RoleGrant.role_id)
            .join(Role, Role.id == RoleGrant.role_id)
            .where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.org_id == org_id,
                RoleGrant.permission_id == permission.id,
            )
        )
    ).all()
    grants.extend(_grant_from_role(name, rg, ra) for name, rg, ra in role_rows)

    override_rows = (
        await session.execute(
            select(PermissionOverride, Scope)
            .join(Scope, Scope.id == PermissionOverride.scope_id)
            .where(
                PermissionOverride.user_id == user_id,
                PermissionOverride.org_id == org_id,
                PermissionOverride.permission_id == permission.id,
            )
        )
    ).all()
    grants.extend(_grant_from_override(ov, sc) for ov, sc in override_rows)

    return grants


async def gather_sod_constraints(session: AsyncSession, org_id: uuid.UUID) -> list[SodConstraint]:
    """The org's separation-of-duties constraints, passed to the PDP for sig-hook actions (S5)."""
    return list(
        (await session.execute(select(SodConstraint).where(SodConstraint.org_id == org_id)))
        .scalars()
        .all()
    )


async def get_allow_approver_release(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """The org's SoD-2 approver-release relaxation flag; ``False`` when no config row exists yet
    (the strict default — the first-run wizard that writes ``system_config`` is S8)."""
    value = (
        await session.execute(
            select(SystemConfig.allow_approver_release).where(SystemConfig.org_id == org_id)
        )
    ).scalar_one_or_none()
    return bool(value)


async def granted_permission_keys(
    session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID
) -> set[str]:
    """Every permission key the user has *any* grant for (role-derived or override) — the
    candidate set the effective-permissions view resolves at a given scope."""
    role_keys = (
        (
            await session.execute(
                select(Permission.key)
                .join(RoleGrant, RoleGrant.permission_id == Permission.id)
                .join(RoleAssignment, RoleAssignment.role_id == RoleGrant.role_id)
                .where(RoleAssignment.user_id == user_id, RoleAssignment.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    override_keys = (
        (
            await session.execute(
                select(Permission.key)
                .join(PermissionOverride, PermissionOverride.permission_id == Permission.id)
                .where(PermissionOverride.user_id == user_id, PermissionOverride.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    return set(role_keys) | set(override_keys)


async def role_system_domain_keys(session: AsyncSession, role_id: uuid.UUID) -> list[str]:
    """The system-domain permission keys bundled in a role — what the two-tier guard checks
    before a content-tier grantor may assign that role (R35)."""
    return list(
        (
            await session.execute(
                select(Permission.key)
                .join(RoleGrant, RoleGrant.permission_id == Permission.id)
                .where(RoleGrant.role_id == role_id, Permission.is_system_domain.is_(True))
            )
        )
        .scalars()
        .all()
    )
