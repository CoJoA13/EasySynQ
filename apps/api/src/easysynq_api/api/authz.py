"""Authorization admin surface (slice S2): the permission catalog, roles, and the
per-user role/override grants — all PEP-gated and deny-by-default (doc 15 §8.1-8.3).

Read routes need ``role.read`` / ``user.read``; grant routes need ``permission.grant`` and
pass the two-tier guard (R35). Every mutation bumps the permissions epoch. Response shapes
are hand-written to match the hand-authored contract (packages/contracts/openapi.yaml).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.app_user import AppUser
from ..db.models.audit_event import AuditEvent
from ..db.models.authz_grant import PermissionOverride
from ..db.models.permission import Permission
from ..db.models.role import Role, RoleAssignment, RoleGrant
from ..db.models.scope import Scope
from ..db.session import get_session
from ..domain.authz.types import Effect, ScopeLevel
from ..logging import request_id_var
from ..problems import ProblemException
from ..services.authz import (
    AuthzAuditSink,
    assert_can_assign_role,
    assert_can_grant,
    get_authz_audit_sink,
    invalidate_user_permissions,
    require,
)
from ..services.authz.effective import compute_effective_permissions

router = APIRouter(prefix="/api/v1", tags=["authz"])

# Dependency singletons — a require(...) call must not sit in an argument default (ruff B008).
_role_read = require("role.read")
_user_read = require("user.read")
_permission_grant = require("permission.grant")


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _audit_authz_change(
    session: AsyncSession,
    granter: AppUser,
    event_type: EventType,
    object_id: uuid.UUID,
    target_user_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append an authorization state-change ``audit_event`` row (doc 12 §4.1) to ``session`` BEFORE
    its commit, so the grant/revoke and its audit row commit atomically (object_type=permission,
    object_id = the affected assignment/override, scope_ref names the target user)."""
    session.add(
        AuditEvent(
            org_id=granter.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=granter.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.permission,
            object_id=object_id,
            scope_ref=f"user:{target_user_id}",
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


# --- request bodies ---------------------------------------------------------------------


class ScopeInput(BaseModel):
    level: Literal["SYSTEM", "FRAMEWORK", "PROCESS", "FOLDER", "DOC_CLASS", "ARTIFACT"]
    selector: dict[str, Any] | None = None
    predicates: dict[str, Any] | None = None


class OverrideCreate(BaseModel):
    permission_key: str
    effect: Literal["ALLOW", "DENY"]
    scope: ScopeInput
    valid_from: datetime.datetime | None = None
    valid_until: datetime.datetime | None = None
    require_reason: bool = False
    reason: str | None = None


class RoleAssignmentCreate(BaseModel):
    role_id: uuid.UUID | None = None
    role_name: str | None = None
    bound_scope: dict[str, Any] | None = None


# --- representations --------------------------------------------------------------------


def _permission(p: Permission) -> dict[str, Any]:
    return {
        "key": p.key,
        "resource": p.resource,
        "action": p.action,
        "is_system_domain": p.is_system_domain,
        "sod_sensitive": p.sod_sensitive,
        "sig_hook": p.sig_hook,
        "finest_scope": p.finest_scope.value,
    }


def _role(r: Role) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "name": r.name,
        "description": r.description,
        "is_reserved": r.is_reserved,
    }


def _assignment(a: RoleAssignment, role_name: str) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "role_id": str(a.role_id),
        "role_name": role_name,
        "bound_scope": a.bound_scope,
    }


def _override(o: PermissionOverride, scope: Scope, permission_key: str) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "permission_key": permission_key,
        "effect": o.effect.value,
        "scope": {
            "level": scope.level.value,
            "selector": scope.selector,
            "predicates": scope.predicates,
        },
        "valid_from": o.valid_from.isoformat() if o.valid_from else None,
        "valid_until": o.valid_until.isoformat() if o.valid_until else None,
        "require_reason": o.require_reason,
    }


# --- helpers ----------------------------------------------------------------------------


async def _get_user(session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> AppUser:
    # Scope every target lookup to the caller's org. v1 is single-org (D1), so this is a no-op
    # today, but it keeps the authz surface tenant-safe so multi-org stays purely additive
    # (doc 14 §1.1). A cross-org target reads as not-found (no existence leak).
    user = await session.get(AppUser, user_id)
    if user is None or user.org_id != org_id:
        raise ProblemException(status=404, code="not_found", title="User not found")
    return user


async def _resolve_role(
    session: AsyncSession, org_id: uuid.UUID, role_id: uuid.UUID | None, role_name: str | None
) -> Role:
    if (role_id is None) == (role_name is None):
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Provide exactly one of role_id or role_name",
        )
    stmt = select(Role).where(Role.org_id == org_id)
    stmt = (
        stmt.where(Role.id == role_id)
        if role_id is not None
        else stmt.where(Role.name == role_name)
    )
    role = (await session.execute(stmt)).scalar_one_or_none()
    if role is None:
        raise ProblemException(status=404, code="not_found", title="Role not found")
    return role


# --- catalog & roles (read) -------------------------------------------------------------


@router.get("/permissions")
async def list_permissions(
    _caller: AppUser = Depends(_role_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (await session.execute(select(Permission).order_by(Permission.key))).scalars().all()
    return [_permission(p) for p in rows]


@router.get("/roles")
async def list_roles(
    caller: AppUser = Depends(_role_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Role).where(Role.org_id == caller.org_id).order_by(Role.name)
            )
        )
        .scalars()
        .all()
    )
    return [_role(r) for r in rows]


@router.get("/roles/{role_id}")
async def get_role(
    role_id: uuid.UUID,
    caller: AppUser = Depends(_role_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    role = await session.get(Role, role_id)
    if role is None or role.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Role not found")
    grant_rows = (
        await session.execute(
            select(Permission.key, RoleGrant.scope_template)
            .join(RoleGrant, RoleGrant.permission_id == Permission.id)
            .where(RoleGrant.role_id == role.id)
            .order_by(Permission.key)
        )
    ).all()
    body = _role(role)
    body["grants"] = [{"permission_key": key, "scope_template": tmpl} for key, tmpl in grant_rows]
    return body


# --- per-user roles ---------------------------------------------------------------------


@router.get("/users/{user_id}/roles")
async def list_user_roles(
    user_id: uuid.UUID,
    caller: AppUser = Depends(_user_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _get_user(session, user_id, caller.org_id)
    rows = (
        await session.execute(
            select(RoleAssignment, Role.name)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(RoleAssignment.user_id == user_id, RoleAssignment.org_id == caller.org_id)
        )
    ).all()
    return [_assignment(a, name) for a, name in rows]


@router.post("/users/{user_id}/roles", status_code=status.HTTP_201_CREATED)
async def assign_user_role(
    user_id: uuid.UUID,
    body: RoleAssignmentCreate,
    granter: AppUser = Depends(_permission_grant),
    session: AsyncSession = Depends(get_session),
    sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    target = await _get_user(session, user_id, granter.org_id)
    role = await _resolve_role(session, target.org_id, body.role_id, body.role_name)
    await assert_can_assign_role(session, sink, granter, role.id)
    assignment = RoleAssignment(
        org_id=target.org_id, user_id=target.id, role_id=role.id, bound_scope=body.bound_scope
    )
    session.add(assignment)
    await session.flush()  # populate assignment.id for the audit row's object_id
    _audit_authz_change(
        session,
        granter,
        EventType.ROLE_ASSIGN,
        assignment.id,
        target.id,
        after={"role_id": str(role.id), "role_name": role.name, "bound_scope": body.bound_scope},
    )
    await session.commit()
    await session.refresh(assignment)
    await invalidate_user_permissions(target.id)
    return _assignment(assignment, role.name)


@router.delete("/users/{user_id}/roles/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_role(
    user_id: uuid.UUID,
    assignment_id: uuid.UUID,
    granter: AppUser = Depends(_permission_grant),
    session: AsyncSession = Depends(get_session),
) -> Response:
    assignment = await session.get(RoleAssignment, assignment_id)
    if assignment is None or assignment.user_id != user_id or assignment.org_id != granter.org_id:
        raise ProblemException(status=404, code="not_found", title="Role assignment not found")
    _audit_authz_change(
        session,
        granter,
        EventType.ROLE_REVOKE,
        assignment.id,
        user_id,
        before={"role_id": str(assignment.role_id)},
    )
    await session.delete(assignment)
    await session.commit()
    await invalidate_user_permissions(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- per-user overrides -----------------------------------------------------------------


@router.get("/users/{user_id}/overrides")
async def list_user_overrides(
    user_id: uuid.UUID,
    caller: AppUser = Depends(_user_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _get_user(session, user_id, caller.org_id)
    rows = (
        await session.execute(
            select(PermissionOverride, Scope, Permission.key)
            .join(Scope, Scope.id == PermissionOverride.scope_id)
            .join(Permission, Permission.id == PermissionOverride.permission_id)
            .where(
                PermissionOverride.user_id == user_id,
                PermissionOverride.org_id == caller.org_id,
            )
        )
    ).all()
    return [_override(o, sc, key) for o, sc, key in rows]


@router.post("/users/{user_id}/overrides", status_code=status.HTTP_201_CREATED)
async def create_user_override(
    user_id: uuid.UUID,
    body: OverrideCreate,
    granter: AppUser = Depends(_permission_grant),
    session: AsyncSession = Depends(get_session),
    sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    target = await _get_user(session, user_id, granter.org_id)
    permission = (
        await session.execute(select(Permission).where(Permission.key == body.permission_key))
    ).scalar_one_or_none()
    if permission is None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Unknown permission",
            detail=f"No such permission: {body.permission_key}",
        )
    await assert_can_grant(session, sink, granter, body.permission_key)

    scope = Scope(
        org_id=target.org_id,
        level=ScopeLevel(body.scope.level),
        selector=body.scope.selector,
        predicates=body.scope.predicates,
    )
    session.add(scope)
    await session.flush()
    override = PermissionOverride(
        org_id=target.org_id,
        user_id=target.id,
        permission_id=permission.id,
        effect=Effect(body.effect),
        scope_id=scope.id,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        require_reason=body.require_reason,
        reason=body.reason,
        created_by=granter.id,
    )
    session.add(override)
    await session.flush()  # populate override.id for the audit row's object_id
    _audit_authz_change(
        session,
        granter,
        EventType.OVERRIDE_ADD,
        override.id,
        target.id,
        after={
            "permission_key": permission.key,
            "effect": body.effect,
            "scope_level": body.scope.level,
        },
    )
    await session.commit()
    await session.refresh(override)
    await invalidate_user_permissions(target.id)
    return _override(override, scope, permission.key)


@router.delete("/users/{user_id}/overrides/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_override(
    user_id: uuid.UUID,
    override_id: uuid.UUID,
    granter: AppUser = Depends(_permission_grant),
    session: AsyncSession = Depends(get_session),
) -> Response:
    override = await session.get(PermissionOverride, override_id)
    if override is None or override.user_id != user_id or override.org_id != granter.org_id:
        raise ProblemException(status=404, code="not_found", title="Override not found")
    scope_id = override.scope_id
    _audit_authz_change(
        session,
        granter,
        EventType.OVERRIDE_REMOVE,
        override.id,
        user_id,
        before={"permission_id": str(override.permission_id)},
    )
    await session.delete(override)
    await session.flush()
    scope = await session.get(Scope, scope_id)
    if scope is not None:
        await session.delete(scope)
    await session.commit()
    await invalidate_user_permissions(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- effective permissions --------------------------------------------------------------


@router.get("/users/{user_id}/effective-permissions")
async def effective_permissions(
    user_id: uuid.UUID,
    scope_level: str | None = None,
    scope_id: str | None = None,
    caller: AppUser = Depends(_user_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    target = await _get_user(session, user_id, caller.org_id)
    return await compute_effective_permissions(
        session,
        user_id=target.id,
        org_id=target.org_id,
        scope_level=scope_level,
        scope_id=scope_id,
    )
