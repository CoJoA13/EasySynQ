"""Notification reads + the per-user master email toggle (spec §9).

Authenticated-self: every query is scoped by recipient_user_id = caller.id
(no permission key — the GET /tasks posture; refute L3-2/L3-4).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, cast
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._notification_enums import NotificationDigestMode
from ..db.models.app_user import AppUser
from ..db.models.notification import Notification, NotificationPreference
from ..db.session import get_session
from ..problems import ProblemException
from ..services.notifications.classes import NotificationClass
from ..services.notifications.preferences import effective_preferences

router = APIRouter(prefix="/api/v1", tags=["notifications"])

_VALID_MODES = {m.value for m in NotificationDigestMode}
_VALID_CLASSES = {c.value for c in NotificationClass}
_COLUMN_FOR_CLASS = {
    "action_required": "digest_mode_action_required",
    "awareness": "digest_mode_awareness",
    "critical": "digest_mode_critical",
    "admin_ops": "digest_mode_admin_ops",
}


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
    digest_modes: dict[str, str]
    digest_hour: int
    timezone: str
    quiet_start: str | None
    quiet_end: str | None


class PreferenceUpdate(BaseModel):
    email_enabled: bool | None = None
    digest_modes: dict[str, str] | None = None
    digest_hour: int | None = None
    timezone: str | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None


def _fmt_time(t: datetime.time | None) -> str | None:
    return t.strftime("%H:%M") if t is not None else None


def _parse_time(s: str) -> datetime.time:
    try:
        h, m = s.split(":")
        return datetime.time(int(h), int(m))
    except Exception as exc:
        raise ProblemException(
            status=422, code="invalid_time", title="quiet hours must be 'HH:MM'"
        ) from exc


def _to_view(pref: NotificationPreference | None) -> PreferenceView:
    eff = effective_preferences(pref)
    return PreferenceView(
        email_enabled=eff.email_enabled,
        digest_modes={c.value: eff.modes[c].value for c in NotificationClass},
        digest_hour=eff.digest_hour,
        timezone=eff.timezone,
        quiet_start=_fmt_time(eff.quiet_start),
        quiet_end=_fmt_time(eff.quiet_end),
    )


@router.get("/me/notification-preferences")
async def get_preferences(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PreferenceView:
    pref = await session.get(NotificationPreference, caller.id)
    return _to_view(pref)


@router.put("/me/notification-preferences")
async def put_preferences(
    body: PreferenceUpdate,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PreferenceView:
    provided = body.model_fields_set
    # Ensure the row exists atomically — two concurrent first-time PUTs both see pref is None
    # without this and race to INSERT the same user_id PK → one 500s.
    await session.execute(
        pg_insert(NotificationPreference)
        .values(user_id=caller.id, email_enabled=True)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    pref = await session.get(NotificationPreference, caller.id)
    if pref is None:  # pragma: no cover — the upsert guarantees this branch is unreachable
        raise RuntimeError("notification preference row missing after upsert")

    if "email_enabled" in provided and body.email_enabled is not None:
        pref.email_enabled = body.email_enabled

    if "digest_modes" in provided and body.digest_modes is not None:
        for klass, mode in body.digest_modes.items():
            if klass not in _VALID_CLASSES:
                raise ProblemException(
                    status=422, code="invalid_class", title=f"unknown class {klass}"
                )
            if mode not in _VALID_MODES:
                raise ProblemException(
                    status=422, code="invalid_mode", title=f"unknown mode {mode}"
                )
            setattr(pref, _COLUMN_FOR_CLASS[klass], NotificationDigestMode(mode))

    if "digest_hour" in provided and body.digest_hour is not None:
        if not (0 <= body.digest_hour <= 23):
            raise ProblemException(
                status=422, code="invalid_hour", title="digest_hour must be 0..23"
            )
        pref.digest_hour = body.digest_hour

    if "timezone" in provided and body.timezone is not None:
        if body.timezone not in available_timezones():
            raise ProblemException(status=422, code="invalid_timezone", title="unknown timezone")
        pref.timezone = body.timezone

    # quiet hours: both-or-neither (based on what was PROVIDED, not stored state)
    if "quiet_start" in provided or "quiet_end" in provided:
        if ("quiet_start" in provided) != ("quiet_end" in provided):
            raise ProblemException(
                status=422,
                code="invalid_quiet_hours",
                title="set both quiet_start and quiet_end together, or neither",
            )
        start = body.quiet_start or None  # "" and None both mean "no value"
        end = body.quiet_end or None
        if (start is None) != (end is None):
            raise ProblemException(
                status=422,
                code="invalid_quiet_hours",
                title="quiet_start and quiet_end must both be set, or both cleared",
            )
        pref.quiet_start = _parse_time(start) if start else None
        pref.quiet_end = _parse_time(end) if end else None

    pref.updated_at = datetime.datetime.now(datetime.UTC)
    await session.commit()
    refreshed = await session.get(NotificationPreference, caller.id)
    return _to_view(refreshed)
