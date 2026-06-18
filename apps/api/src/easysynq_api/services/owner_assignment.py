"""Owner-assignment — bind a user as the accountable owner of a process (S-owner-assignment-1).

Assigning a process owner is ONE QMS act (the endpoint gates ``process.assign_owner``) that does two
things in a single transaction:

1. Records the **RACI accountability fact** — an ``org_role_assignment`` row binding the user to the
   org's "Process Owner" ``org_role`` (resolve-or-created), scoped to the concrete process (doc 02
   §3.4: reference data, NOT a permission — "do not conflate the two kinds of role").
2. Mints/extends the **authorization** — a single PROCESS-scoped ``role_assignment`` for the seeded
   "Process Owner" permission-role whose ``bound_scope`` carries a growing ``process_ids`` set,
   substituting the seeded ``:assignment_process`` placeholder with real process ids. The PDP
   ``_matches_scope`` reads ``selector.process_ids`` so a SINGLE assignment authorizes across every
   owned process (and ``role_assignment`` has no uniqueness backstop, so a single growing row beats
   N rows). The mint is **system-attributed**: ``process.assign_owner`` (content/QMS tier) is the
   authority, so we do not additionally re-gate through the SYSTEM-tier ``permission.grant`` (the
   cutover / import_baseline system-mint precedent).

Every bind/unbind is audited (AZ-INV-5): a ``process``-typed PROCESS_OWNER_ASSIGNED/_REVOKED for the
RACI act + a ``permission``-typed ROLE_ASSIGN/ROLE_REVOKE for the bound_scope change — and the
user's cached PDP grants are invalidated. Scope only narrows (AZ-INV-8): the bound process_ids pins
the role's PROCESS template to concrete ids, never widening to SYSTEM. Concurrent owner-assigns for
the same user serialize on the parent ``app_user`` row (FOR UPDATE) so the single Process-Owner
``role_assignment`` is created/extended exactly once.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.app_user import AppUser
from ..db.models.audit_event import AuditEvent
from ..db.models.org_role import OrgRole
from ..db.models.org_role_assignment import OrgRoleAssignment
from ..db.models.process import Process
from ..db.models.role import Role, RoleAssignment
from ..logging import request_id_var
from ..problems import ProblemException
from ..services.authz import invalidate_user_permissions

# The generic QMS "Process Owner" — the name of BOTH the RACI org_role (resolve-or-created here) and
# the seeded permission-role (looked up; carries the grants). The two live in different tables and
# are deliberately distinct (doc 02 §3.4); the per-process specificity rides org_role_assignment.
# process_id and the bound_scope process_ids set, NOT a per-process org_role.
_PROCESS_OWNER_ROLE_NAME = "Process Owner"
# The placeholder the seeded Process-Owner role_grant scope_templates carry until owner-assignment
# binds a real process (migrations 0004/0036/0040/...); never a real process id.
_PLACEHOLDER = ":assignment_process"


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _audit(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    object_type: AuditObjectType,
    object_id: uuid.UUID,
    *,
    target_user_id: uuid.UUID,
    after: dict[str, Any],
) -> None:
    """Append an audit row BEFORE commit so the mutation + its audit row commit atomically
    (scope_ref names the target user, the authz._audit_authz_change shape; hashes stay NULL)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            scope_ref=f"user:{target_user_id}",
            after=after,
            request_id=_rid(),
        )
    )


def _bound_process_ids(bound_scope: dict[str, Any] | None) -> set[str]:
    """The concrete process ids carried by a Process-Owner bound_scope — reading BOTH the legacy
    single ``process_id`` and the ``process_ids`` set shapes, ignoring the unbound placeholder."""
    if not bound_scope:
        return set()
    selector = bound_scope.get("selector") or {}
    ids: set[str] = set()
    single = selector.get("process_id")
    if isinstance(single, str) and single and single != _PLACEHOLDER:
        ids.add(single)
    multi = selector.get("process_ids")
    if isinstance(multi, list | tuple | set):
        ids.update(str(x) for x in multi if x and x != _PLACEHOLDER)
    return ids


def _process_bound_scope(process_ids: set[str]) -> dict[str, Any]:
    return {"level": "PROCESS", "selector": {"process_ids": sorted(process_ids)}}


async def _resolve_or_create_org_role(
    session: AsyncSession, *, org_id: uuid.UUID, name: str, created_by: uuid.UUID
) -> OrgRole:
    existing = (
        await session.execute(select(OrgRole).where(OrgRole.org_id == org_id, OrgRole.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    # Create inside a SAVEPOINT so a concurrent first-ever create (UNIQUE(org_id, name) race) only
    # rolls back the nested step, then re-read the winner — the outer txn survives.
    try:
        async with session.begin_nested():
            role = OrgRole(org_id=org_id, name=name, created_by=created_by)
            session.add(role)
            await session.flush()
        return role
    except IntegrityError:
        return (
            await session.execute(
                select(OrgRole).where(OrgRole.org_id == org_id, OrgRole.name == name)
            )
        ).scalar_one()


async def _resolve_permission_role(session: AsyncSession, *, org_id: uuid.UUID, name: str) -> Role:
    role = (
        await session.execute(select(Role).where(Role.org_id == org_id, Role.name == name))
    ).scalar_one_or_none()
    if role is None:
        # The "Process Owner" permission-role is seeded in 0004 for every org; its absence is a
        # misconfigured install, not a client error.
        raise ProblemException(
            status=409,
            code="role_not_seeded",
            title=f"The '{name}' permission role is not present in this organization",
        )
    return role


async def _lock_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    # Serialization point: lock the parent app_user row so concurrent owner-assigns for the same
    # user create/extend the single Process-Owner role_assignment exactly once.
    await session.execute(select(AppUser.id).where(AppUser.id == user_id).with_for_update())


async def _owner_role_assignment(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID, role_id: uuid.UUID
) -> RoleAssignment | None:
    return (
        (
            await session.execute(
                select(RoleAssignment)
                .where(
                    RoleAssignment.org_id == org_id,
                    RoleAssignment.user_id == user_id,
                    RoleAssignment.role_id == role_id,
                )
                .order_by(RoleAssignment.id)
            )
        )
        .scalars()
        .first()
    )


async def assign_process_owner(
    session: AsyncSession,
    *,
    actor: AppUser,
    process: Process,
    user: AppUser,
    org_role_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Bind ``user`` as owner of ``process`` (idempotent). Records the RACI org_role_assignment AND
    mints/extends the PROCESS-scoped Process-Owner role_assignment; audits both; invalidates the
    user's cached grants. Returns the resulting owner view. Owns the transaction."""
    org_id = actor.org_id
    await _lock_user(session, user.id)

    # 1. RACI: the org_role (caller-supplied or the resolve-or-created generic "Process Owner").
    if org_role_id is not None:
        org_role = await session.get(OrgRole, org_role_id)
        if org_role is None or org_role.org_id != org_id:
            raise ProblemException(status=404, code="not_found", title="org_role not found")
    else:
        org_role = await _resolve_or_create_org_role(
            session, org_id=org_id, name=_PROCESS_OWNER_ROLE_NAME, created_by=actor.id
        )
    binding = (
        await session.execute(
            select(OrgRoleAssignment).where(
                OrgRoleAssignment.org_id == org_id,
                OrgRoleAssignment.org_role_id == org_role.id,
                OrgRoleAssignment.user_id == user.id,
                OrgRoleAssignment.process_id == process.id,
            )
        )
    ).scalar_one_or_none()
    if binding is None:
        binding = OrgRoleAssignment(
            org_id=org_id,
            org_role_id=org_role.id,
            user_id=user.id,
            process_id=process.id,
            created_by=actor.id,
        )
        session.add(binding)
        await session.flush()

    # 2. Authorization: mint/extend the single Process-Owner role_assignment's process_ids set.
    perm_role = await _resolve_permission_role(
        session, org_id=org_id, name=_PROCESS_OWNER_ROLE_NAME
    )
    assignment = await _owner_role_assignment(
        session, org_id=org_id, user_id=user.id, role_id=perm_role.id
    )
    process_ids = _bound_process_ids(assignment.bound_scope if assignment else None)
    process_ids.add(str(process.id))
    bound_scope = _process_bound_scope(process_ids)
    if assignment is None:
        assignment = RoleAssignment(
            org_id=org_id, user_id=user.id, role_id=perm_role.id, bound_scope=bound_scope
        )
        session.add(assignment)
    else:
        # Reassign a fresh dict — SQLAlchemy does not track in-place JSONB mutation (S-ing-4).
        assignment.bound_scope = bound_scope
    await session.flush()

    # 3. Audit both halves (AZ-INV-5), then commit + invalidate the user's cached PDP grants.
    _audit(
        session,
        actor,
        EventType.PROCESS_OWNER_ASSIGNED,
        AuditObjectType.process,
        process.id,
        target_user_id=user.id,
        after={
            "user_id": str(user.id),
            "org_role_id": str(org_role.id),
            "role_assignment_id": str(assignment.id),
        },
    )
    _audit(
        session,
        actor,
        EventType.ROLE_ASSIGN,
        AuditObjectType.permission,
        assignment.id,
        target_user_id=user.id,
        after={
            "role_id": str(perm_role.id),
            "role_name": perm_role.name,
            "bound_scope": bound_scope,
        },
    )
    result = {
        "process_id": str(process.id),
        "user_id": str(user.id),
        "org_role_id": str(org_role.id),
        "org_role_assignment_id": str(binding.id),
        "role_assignment_id": str(assignment.id),
        "bound_scope": bound_scope,
    }
    await session.commit()
    await invalidate_user_permissions(user.id)
    return result


async def revoke_process_owner(
    session: AsyncSession, *, actor: AppUser, process: Process, user: AppUser
) -> None:
    """Unbind ``user`` as owner of ``process``: delete the RACI binding(s) for (user, process) and
    remove the process from the Process-Owner role_assignment's process_ids set (deleting the
    assignment when the set empties). Audits both halves; invalidates. Owns the transaction."""
    org_id = actor.org_id
    await _lock_user(session, user.id)

    bindings = (
        (
            await session.execute(
                select(OrgRoleAssignment).where(
                    OrgRoleAssignment.org_id == org_id,
                    OrgRoleAssignment.user_id == user.id,
                    OrgRoleAssignment.process_id == process.id,
                )
            )
        )
        .scalars()
        .all()
    )
    if not bindings:
        raise ProblemException(
            status=404,
            code="not_found",
            title="This user is not a recorded owner of this process",
        )
    for b in bindings:
        await session.delete(b)

    perm_role = await _resolve_permission_role(
        session, org_id=org_id, name=_PROCESS_OWNER_ROLE_NAME
    )
    assignment = await _owner_role_assignment(
        session, org_id=org_id, user_id=user.id, role_id=perm_role.id
    )
    removed_assignment = False
    role_event_id: uuid.UUID | None = None
    if assignment is not None:
        role_event_id = assignment.id
        process_ids = _bound_process_ids(assignment.bound_scope)
        process_ids.discard(str(process.id))
        if process_ids:
            assignment.bound_scope = _process_bound_scope(process_ids)
        else:
            # No inert empty PROCESS grant — drop the assignment when the last process is removed.
            removed_assignment = True
            await session.delete(assignment)
        await session.flush()

    _audit(
        session,
        actor,
        EventType.PROCESS_OWNER_REVOKED,
        AuditObjectType.process,
        process.id,
        target_user_id=user.id,
        after={"user_id": str(user.id)},
    )
    if role_event_id is not None:
        _audit(
            session,
            actor,
            EventType.ROLE_REVOKE if removed_assignment else EventType.ROLE_ASSIGN,
            AuditObjectType.permission,
            role_event_id,
            target_user_id=user.id,
            after={"role_name": perm_role.name, "process_removed": str(process.id)},
        )
    await session.commit()
    await invalidate_user_permissions(user.id)
