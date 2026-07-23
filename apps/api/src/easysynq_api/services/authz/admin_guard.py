"""Break-glass guard: an org must always retain at least one ACTIVE System Administrator.

Two admin surfaces can remove the last one — role revocation (``DELETE /users/{id}/roles/{aid}``)
and user deactivation (``PATCH /users/{id}`` → DISABLED). A per-path check-then-mutate RACES: two
concurrent transactions (one disabling admin A, one revoking admin B's System-Administrator role)
each still see the other active and both commit → a self-hosted lockout the spec forbids (doc 08
§9.1). So BOTH paths take ONE org-scoped transaction advisory lock BEFORE counting admins and
before the mutating delete/disable — serialising the count+mutation across both paths. The lock is
transaction-scoped: it auto-releases when the request's transaction commits or rolls back.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser, UserStatus
from ...db.models.role import Role, RoleAssignment

# The seeded role that carries the system-administration bundle (the appliance's qmsadmin holds it).
SYSTEM_ADMIN_ROLE = "System Administrator"

# A per-org transaction-scoped advisory-lock namespace for the admin set. Two-arg (ns, oid) form,
# distinct from the register-head locks (7710100/1/2 — risk/context/interested-parties) and the
# single-arg global LOCK_* Beat keys (see services/common/pg_locks.py).
_ADMIN_SET_LOCK_NS = 7710110


def _org_admin_lock_oid(org_id: uuid.UUID) -> int:
    """A stable signed int32 from the org id (PostgreSQL advisory keys are int4 in the two-arg
    form) — mirrors the register-head ``_org_head_lock_oid`` derivation."""
    return int.from_bytes(hashlib.blake2b(org_id.bytes, digest_size=4).digest(), "big", signed=True)


async def lock_admin_set(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Take the org-scoped transaction advisory lock that serialises every path which can remove
    the last active System Administrator. It MUST be held across the admin count AND the mutating
    delete/disable, in the SAME transaction — the lock releases only when that transaction ends."""
    await session.execute(
        # Cast to int4 so PG binds the two-arg pg_advisory_xact_lock(int, int) overload (a Python
        # int would otherwise bind as bigint, for which no two-arg form exists) — the context/risk
        # register-head-lock idiom.
        text("SELECT pg_advisory_xact_lock(CAST(:ns AS integer), CAST(:oid AS integer))"),
        {"ns": _ADMIN_SET_LOCK_NS, "oid": _org_admin_lock_oid(org_id)},
    )


async def _active_admin_user_ids(
    session: AsyncSession, org_id: uuid.UUID, *, exclude_assignment_id: uuid.UUID | None = None
) -> set[uuid.UUID]:
    """The distinct ACTIVE users holding a System-Administrator role assignment in ``org_id``,
    optionally excluding one assignment row (the one about to be revoked) — so a user who keeps the
    role through a *second* assignment still counts."""
    stmt = (
        select(RoleAssignment.user_id)
        .join(Role, Role.id == RoleAssignment.role_id)
        .join(AppUser, AppUser.id == RoleAssignment.user_id)
        .where(
            RoleAssignment.org_id == org_id,
            Role.name == SYSTEM_ADMIN_ROLE,
            AppUser.status == UserStatus.ACTIVE,
        )
    )
    if exclude_assignment_id is not None:
        stmt = stmt.where(RoleAssignment.id != exclude_assignment_id)
    return set((await session.execute(stmt)).scalars().all())


async def revoke_removes_last_admin(session: AsyncSession, assignment: RoleAssignment) -> bool:
    """Would revoking ``assignment`` leave the org with no ACTIVE System Administrator? Only an
    admin-role assignment can — revoking any other role never shrinks the admin set, so that case
    short-circuits WITHOUT taking the lock (no contention on ordinary role revokes). For an
    admin-role assignment it takes the shared lock, then compares the admin set before vs. after
    dropping this one assignment; a transition from ≥1 active admin to 0 is the last-admin removal.
    Call inside the request transaction that performs the delete."""
    role = await session.get(Role, assignment.role_id)
    if role is None or role.name != SYSTEM_ADMIN_ROLE:
        return False
    await lock_admin_set(session, assignment.org_id)
    before = await _active_admin_user_ids(session, assignment.org_id)
    if not before:
        # Already no active admin (a pre-existing / unreachable state) — this revoke isn't what
        # removes the last one, so don't block it.
        return False
    after = await _active_admin_user_ids(
        session, assignment.org_id, exclude_assignment_id=assignment.id
    )
    return not after


async def disable_removes_last_admin(
    session: AsyncSession, org_id: uuid.UUID, target_id: uuid.UUID
) -> bool:
    """Would disabling ``target`` (ACTIVE → DISABLED) leave the org with no ACTIVE System
    Administrator? Takes the shared lock, then checks whether ``target`` is the sole active admin.
    Call inside the request transaction that performs the status change (the caller guarantees the
    target is currently ACTIVE — the disable transition)."""
    await lock_admin_set(session, org_id)
    before = await _active_admin_user_ids(session, org_id)
    if not before:
        return False
    return before <= {target_id}  # target is the only active admin → disabling it empties the set
