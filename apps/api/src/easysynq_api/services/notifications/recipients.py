"""Resolve a task's notification recipients (spec §5): the assignee if set, else each candidate-pool
member. Loads the AppUser + the per-user master email toggle (absence ⇒ enabled, spec §3.4)."""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser
from ...db.models.notification import NotificationPreference
from ...db.models.workflow import Task


@dataclasses.dataclass(frozen=True)
class Recipient:
    user_id: uuid.UUID
    email: str | None
    display_name: str
    first_name: str
    email_enabled: bool


def _first_name(display_name: str | None) -> str:
    if display_name and display_name.strip():
        return display_name.strip().split()[0]
    return "there"


def _pool_ids(task: Task) -> list[uuid.UUID]:
    if task.assignee_user_id is not None:
        return [task.assignee_user_id]
    out: list[uuid.UUID] = []
    for raw in task.candidate_pool or []:
        try:
            out.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    return out


async def resolve_recipients(session: AsyncSession, task: Task) -> list[Recipient]:
    ids = _pool_ids(task)
    if not ids:
        return []
    users = (await session.execute(select(AppUser).where(AppUser.id.in_(ids)))).scalars().all()
    pref_rows = (
        await session.execute(
            select(NotificationPreference.user_id, NotificationPreference.email_enabled).where(
                NotificationPreference.user_id.in_(ids)
            )
        )
    ).all()
    prefs: dict[uuid.UUID, bool] = {row[0]: row[1] for row in pref_rows}
    return [
        Recipient(
            user_id=u.id,
            email=u.email,
            display_name=u.display_name or "",
            first_name=_first_name(u.display_name),
            email_enabled=prefs.get(u.id, True),  # absence ⇒ enabled
        )
        for u in users
    ]
