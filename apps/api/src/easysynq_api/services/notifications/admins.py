"""Resolve the org's System-Administrator users for failure notifications (spec §6)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser
from ...db.models.role import Role, RoleAssignment

_ADMIN_ROLE = "System Administrator"


async def admin_user_ids(session: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        (
            await session.execute(
                select(RoleAssignment.user_id)
                .join(Role, Role.id == RoleAssignment.role_id)
                .join(AppUser, AppUser.id == RoleAssignment.user_id)
                .where(Role.name == _ADMIN_ROLE, AppUser.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    return list(dict.fromkeys(rows))
