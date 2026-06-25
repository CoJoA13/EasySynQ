"""S-notify-7: the working-calendar admin editor service. Reads/writes the org's single is_default
working_calendar row. Validation is FAIL-LOUD (422 via ProblemException) using the SAME shared
strict parsers the fail-safe resolver trusts, so a saved calendar never silently degrades.

The service does NOT commit — the route commits (the api/config.py precedent). This keeps the
INSERT-branch test leak-free (the app role can't DELETE a working_calendar row)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.organization import Organization
from ...db.models.working_calendar import WorkingCalendar
from ...logging import request_id_var
from ...problems import ProblemException
from .calendar_spec import is_valid_timezone, parse_holiday, parse_working_days

_MAX_WORKING_DAYS_LEN = 31
_MAX_HOLIDAYS_LEN = 1000
_DEFAULT_WORKING_DAYS = [1, 2, 3, 4, 5]


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _422(title: str) -> ProblemException:
    return ProblemException(status=422, code="validation_error", title=title)


async def _load_default(session: AsyncSession, org_id: uuid.UUID) -> WorkingCalendar | None:
    return (
        await session.execute(
            select(WorkingCalendar)
            .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
            .limit(
                1
            )  # defensive (mirror resolve_working_calendar) — the partial index guarantees <=1
        )
    ).scalar_one_or_none()


def _view(
    name: str, working_days: list[int], holidays: list[str], timezone: str, exists: bool
) -> dict[str, Any]:
    return {
        "name": name,
        "working_days": working_days,
        "holidays": holidays,
        "timezone": timezone,
        "exists": exists,
    }


async def get_working_calendar(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """The org's is_default working_calendar as a view dict, or the synthesized Mon-Fri default
    (tz = organization.timezone, exists=False) when none. Stored values are SANITIZED through the
    shared parsers (kept-good) so a malformed legacy entry can never wedge a later Save."""
    row = await _load_default(session, org_id)
    if row is None:
        org = await session.get(Organization, org_id)
        org_tz = org.timezone if org is not None else "UTC"
        return _view("Default", list(_DEFAULT_WORKING_DAYS), [], org_tz, exists=False)
    wd = parse_working_days(row.working_days)
    working_days = sorted(wd) if wd is not None else list(_DEFAULT_WORKING_DAYS)
    raw_holidays = row.holidays if isinstance(row.holidays, list) else []
    parsed = [h for h in (parse_holiday(x) for x in raw_holidays) if h is not None]
    holidays = sorted(d.isoformat() for d in parsed)
    tz = row.timezone if is_valid_timezone(row.timezone or "") else "UTC"
    return _view(row.name, working_days, holidays, tz, exists=True)


def _validate(
    name: str, working_days: list[Any], holidays: list[Any], timezone: str
) -> tuple[str, list[int], list[str], str]:
    name = name.strip()
    if not name:
        raise _422("name must not be empty")
    if len(name) > 255:
        raise _422("name must be at most 255 characters")
    if isinstance(working_days, list) and len(working_days) > _MAX_WORKING_DAYS_LEN:
        raise _422("working_days is too long")
    wd = parse_working_days(working_days)
    if wd is None:
        raise _422("working_days must be a non-empty list of ISO weekdays 1..7")
    if not isinstance(holidays, list):
        raise _422("holidays must be a list")
    if len(holidays) > _MAX_HOLIDAYS_LEN:
        raise _422("holidays is too long")
    dates: set[datetime.date] = set()
    for h in holidays:
        parsed = parse_holiday(h)
        if parsed is None:
            raise _422(f"holiday is not a valid YYYY-MM-DD date: {h!r}")
        dates.add(parsed)
    if not is_valid_timezone(timezone):
        raise _422(f"unknown IANA timezone: {timezone!r}")
    return name, sorted(wd), sorted(d.isoformat() for d in dates), timezone


async def update_working_calendar(
    session: AsyncSession,
    *,
    actor: AppUser,
    name: str,
    working_days: list[Any],
    holidays: list[Any],
    timezone: str,
) -> dict[str, Any]:
    """Validate (fail-loud → 422) → atomic ON CONFLICT upsert of the is_default row → audit
    CONFIG_UPDATED on a real diff. Does NOT commit (the route commits)."""
    org_id = actor.org_id
    name, wd, hol, tz = _validate(name, working_days, holidays, timezone)

    before = await get_working_calendar(session, org_id)  # existing row or synthesized default
    before_fields = {k: before[k] for k in ("name", "working_days", "holidays", "timezone")}
    after_fields = {"name": name, "working_days": wd, "holidays": hol, "timezone": tz}

    stmt = (
        pg_insert(WorkingCalendar)
        .values(
            id=uuid.uuid4(),
            org_id=org_id,
            name=name,
            working_days=wd,
            holidays=hol,
            timezone=tz,
            is_default=True,
        )
        .on_conflict_do_update(
            index_elements=["org_id"],
            index_where=sa.text("is_default"),
            set_={
                "name": name,
                "working_days": wd,
                "holidays": hol,
                "timezone": tz,
                "updated_at": sa.func.now(),
            },
        )
    )
    await session.execute(stmt)

    if before_fields != after_fields:
        session.add(
            AuditEvent(
                org_id=org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=actor.id,
                actor_type=ActorType.user,
                event_type=EventType.CONFIG_UPDATED,
                object_type=AuditObjectType.config,
                object_id=org_id,
                before={"working_calendar": before_fields},
                after={"working_calendar": after_fields},
                request_id=_rid(),
            )
        )
    return _view(name, wd, hol, tz, exists=True)
