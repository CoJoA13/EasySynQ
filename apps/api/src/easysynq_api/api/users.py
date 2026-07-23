"""User-lifecycle admin surface (slice S8d, doc 08 §10/§11): the roster + invite + enable/disable.

The per-user role/override *grants* live in ``api/authz.py`` (shipped in S2, reused unchanged); this
router owns the ``app_user`` lifecycle. All routes are PEP-gated + org-scoped + audited pre-commit.

**Invite** pre-creates an ``INVITED`` ``app_user`` bound to a Keycloak ``sub`` the operator supplies
(the operator creates the Keycloak account out-of-band — in-app Keycloak admin-API provisioning is a
v1 convenience, D1/no-Keycloak-in-CI). The row reconciles with the existing JIT lookup on that
subject's first login (``auth/dependencies.py`` matches on ``keycloak_subject`` and flips
INVITED→ACTIVE). **Enable/disable** toggles ``status`` between ACTIVE and DISABLED; disabling the
sole active System Administrator is refused (the break-glass principle, doc 08 §9.1).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.app_user import AppUser, UserStatus
from ..db.models.audit_event import AuditEvent
from ..db.models.role import Role, RoleAssignment
from ..db.session import get_session
from ..logging import request_id_var
from ..problems import ProblemException
from ..services.authz import disable_removes_last_admin, invalidate_user_permissions, require

router = APIRouter(prefix="/api/v1", tags=["users"])

# Dependency singletons — a require(...) call must not sit in an argument default (ruff B008).
_user_read = require("user.read")
_user_create = require("user.create")
_user_deactivate = require("user.deactivate")


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _emit_user_event(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    target_user_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a user-lifecycle ``audit_event`` (object_type=user) BEFORE commit, so the change + its
    audit row commit atomically (mirrors ``authz._audit_authz_change``). Hashes stay NULL (R12)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.user,
            object_id=target_user_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


class UserInvite(BaseModel):
    keycloak_subject: str
    display_name: str | None = None
    email: str | None = None


class UserStatusUpdate(BaseModel):
    status: Literal["ACTIVE", "DISABLED"]


def _represent(user: AppUser, role_names: list[str]) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "keycloak_subject": user.keycloak_subject,
        "display_name": user.display_name,
        "email": user.email,
        "status": user.status.value,
        "mfa_enrolled": user.mfa_enrolled,
        "is_guest": user.is_guest,
        "roles": role_names,
    }


async def _role_names_by_user(
    session: AsyncSession, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(RoleAssignment.user_id, Role.name)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(RoleAssignment.org_id == org_id, RoleAssignment.user_id.in_(user_ids))
            .order_by(Role.name)
        )
    ).all()
    out: dict[uuid.UUID, list[str]] = {}
    for user_id, name in rows:
        out.setdefault(user_id, []).append(name)
    return out


async def _get_user(session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> AppUser:
    # Org-scoped lookup; a cross-org target reads as not-found (no existence leak), keeping the
    # surface tenant-safe for the additive multi-org path (matches authz.py).
    user = await session.get(AppUser, user_id)
    if user is None or user.org_id != org_id:
        raise ProblemException(status=404, code="not_found", title="User not found")
    return user


@router.get("/users")
async def list_users(
    status_filter: str | None = None,
    is_guest: bool | None = None,
    caller: AppUser = Depends(_user_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The user roster (org-scoped). Optional ``?status_filter=`` / ``?is_guest=``."""
    stmt = select(AppUser).where(AppUser.org_id == caller.org_id)
    if status_filter is not None:
        try:
            stmt = stmt.where(AppUser.status == UserStatus(status_filter))
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title=f"Unknown status: {status_filter!r}"
            ) from exc
    if is_guest is not None:
        stmt = stmt.where(AppUser.is_guest == is_guest)
    users = list((await session.execute(stmt.order_by(AppUser.display_name))).scalars().all())
    names = await _role_names_by_user(session, caller.org_id, [u.id for u in users])
    return [_represent(u, names.get(u.id, [])) for u in users]


@router.get("/users/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    caller: AppUser = Depends(_user_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user = await _get_user(session, user_id, caller.org_id)
    names = await _role_names_by_user(session, caller.org_id, [user.id])
    return _represent(user, names.get(user.id, []))


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: UserInvite,
    caller: AppUser = Depends(_user_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Pre-create an INVITED user bound to a Keycloak subject (the operator creates the Keycloak
    account out-of-band; in-app provisioning is v1). Reconciles with JIT on first login. 409 if the
    subject already has an ``app_user`` row."""
    subject = body.keycloak_subject.strip()
    if not subject:
        raise ProblemException(
            status=422, code="validation_error", title="keycloak_subject must not be empty"
        )
    existing = await session.scalar(select(AppUser.id).where(AppUser.keycloak_subject == subject))
    if existing is not None:
        raise ProblemException(
            status=409, code="user_exists", title="A user with that subject already exists"
        )
    user = AppUser(
        org_id=caller.org_id,
        keycloak_subject=subject,
        display_name=body.display_name,
        email=body.email,
        status=UserStatus.INVITED,
    )
    session.add(user)
    await session.flush()  # populate user.id for the audit row
    _emit_user_event(
        session,
        caller,
        EventType.USER_CREATED,
        user.id,
        after={"status": UserStatus.INVITED.value, "email": body.email},
    )
    await session.commit()
    await session.refresh(user)
    return _represent(user, [])


@router.patch("/users/{user_id}")
async def update_user_status(
    user_id: uuid.UUID,
    body: UserStatusUpdate,
    caller: AppUser = Depends(_user_deactivate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enable/disable a user. Refuses to disable the **sole active System Administrator** (the
    break-glass principle, doc 08 §9.1) → 409 ``last_admin``."""
    target = await _get_user(session, user_id, caller.org_id)
    new_status = UserStatus(body.status)
    old_status = target.status
    if new_status == old_status:
        names = await _role_names_by_user(session, caller.org_id, [target.id])
        return _represent(target, names.get(target.id, []))

    # Break-glass (doc 08 §9.1): never disable the org's last active System Administrator. The check
    # and the status write run under one org-scoped lock (shared with the role-revocation path via
    # ``disable_removes_last_admin``) so a concurrent revoke+disable can't both win → lockout.
    if new_status == UserStatus.DISABLED and await disable_removes_last_admin(
        session, caller.org_id, target.id
    ):
        raise ProblemException(
            status=409,
            code="last_admin",
            title="Cannot disable the only active System Administrator",
        )

    target.status = new_status
    _emit_user_event(
        session,
        caller,
        EventType.USER_STATUS_CHANGED,
        target.id,
        before={"status": old_status.value},
        after={"status": new_status.value},
    )
    await session.commit()
    # ``get_current_user`` rejects a DISABLED status on the target's next request (403); bump the
    # perm epoch too so any cached effective-permission set for them is dropped immediately.
    await invalidate_user_permissions(target.id)
    await session.refresh(target)
    names = await _role_names_by_user(session, caller.org_id, [target.id])
    return _represent(target, names.get(target.id, []))
