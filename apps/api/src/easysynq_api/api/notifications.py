"""Notification reads + the per-user master email toggle (spec §9).

Authenticated-self: every query is scoped by recipient_user_id = caller.id
(no permission key — the GET /tasks posture; refute L3-2/L3-4).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models.app_user import AppUser
from ..db.models.notification import Notification, NotificationPreference
from ..db.session import get_session
from ..problems import ProblemException

router = APIRouter(prefix="/api/v1", tags=["notifications"])


def _view(n: Notification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "event_key": n.event_key,
        "subject_type": n.subject_type,
        "subject_id": str(n.subject_id) if n.subject_id else None,
        "title": n.title,
        "body": n.body,
        "deep_link": n.deep_link,
        "created_at": n.created_at.isoformat(),
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


@router.get("/notifications")
async def list_notifications(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    stmt = select(Notification).where(Notification.recipient_user_id == caller.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(min(limit, 200))
    rows = (await session.execute(stmt)).scalars().all()
    return [_view(n) for n in rows]


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient_user_id == caller.id,
            )
            .values(read_at=datetime.datetime.now(datetime.UTC))
        ),
    )
    if result.rowcount == 0:
        raise ProblemException(status=404, code="not_found", title="No such notification")
    await session.commit()
    return {"status": "ok"}


@router.post("/notifications/read-all")
async def mark_all_read(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(Notification)
            .where(
                Notification.recipient_user_id == caller.id,
                Notification.read_at.is_(None),
            )
            .values(read_at=datetime.datetime.now(datetime.UTC))
        ),
    )
    await session.commit()
    return {"marked": result.rowcount}


class PreferenceView(BaseModel):
    email_enabled: bool


@router.get("/me/notification-preferences")
async def get_preferences(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PreferenceView:
    pref = await session.get(NotificationPreference, caller.id)
    return PreferenceView(email_enabled=pref.email_enabled if pref else True)


@router.put("/me/notification-preferences")
async def put_preferences(
    body: PreferenceView,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PreferenceView:
    await session.execute(
        pg_insert(NotificationPreference)
        .values(user_id=caller.id, email_enabled=body.email_enabled)
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "email_enabled": body.email_enabled,
                "updated_at": datetime.datetime.now(datetime.UTC),
            },
        )
    )
    await session.commit()
    return body
