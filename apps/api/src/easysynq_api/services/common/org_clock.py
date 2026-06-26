"""The single canonical org-timezone resolver + a request/sweep-scoped contextvar (S-orgtz-unify,
R56).

One source of truth for "the org's timezone": the is_default working_calendar's tz, falling back to
organization.timezone, then env easysynq_org_timezone, then UTC. ``resolve_working_calendar``
(escalation.py) sources its tz from the SAME ``pick_tz`` chain, so the calendar/timer frame and the
review/date frame can never disagree (parity by construction).

``today_org()``/``_org_tz()`` (services/vault/review.py) read ``current_org_tz()``: the contextvar
value when set (the auth boundary sets it per-request; the escalation sweep sets it per-task around
the render), else the env fallback — so an unset context degrades to the pre-unify behaviour.

This module imports ONLY models + config — never workflow/engine/escalation — so it introduces no
import cycle.
"""

from __future__ import annotations

import contextlib
import uuid
import zoneinfo
from collections.abc import Iterator
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models.organization import Organization
from ...db.models.working_calendar import WorkingCalendar

_org_tz_var: ContextVar[zoneinfo.ZoneInfo | None] = ContextVar("org_tz", default=None)


def _valid_tz(name: str | None) -> zoneinfo.ZoneInfo | None:
    if not name:
        return None
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return None


def pick_tz(cal_tz: str | None, org_tz: str | None) -> zoneinfo.ZoneInfo:
    """The canonical chain: calendar tz → organization tz → env → UTC (each fail-safe)."""
    return (
        _valid_tz(cal_tz)
        or _valid_tz(org_tz)
        or _valid_tz(get_settings().easysynq_org_timezone)
        or zoneinfo.ZoneInfo("UTC")
    )


def current_org_tz() -> zoneinfo.ZoneInfo:
    """The org tz for the current request/sweep context; env fallback when unset (safe degrade)."""
    tz = _org_tz_var.get()
    if tz is not None:
        return tz
    return _valid_tz(get_settings().easysynq_org_timezone) or zoneinfo.ZoneInfo("UTC")


def set_request_org_tz(tz: zoneinfo.ZoneInfo) -> None:
    """Set the contextvar for the rest of THIS request task. No reset: a request runs in its own
    asyncio task whose context copy is discarded at task end, so it never leaks to another request.
    Use ``using_org_tz`` (which resets) in a worker that loops over tasks/orgs."""
    _org_tz_var.set(tz)


@contextlib.contextmanager
def using_org_tz(tz: zoneinfo.ZoneInfo) -> Iterator[None]:
    """Scope ``tz`` as the org tz for the block (sweeps/workers that loop over orgs — resets
    after)."""
    token = _org_tz_var.set(tz)
    try:
        yield
    finally:
        _org_tz_var.reset(token)


async def resolve_org_tz(session: AsyncSession, org_id: uuid.UUID) -> zoneinfo.ZoneInfo:
    """Resolve the canonical org tz from the DB (D-1). Never raises."""
    cal_tz = (
        await session.execute(
            select(WorkingCalendar.timezone)
            .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    org_tz = (
        await session.execute(select(Organization.timezone).where(Organization.id == org_id))
    ).scalar_one_or_none()
    return pick_tz(cal_tz, org_tz)


async def resolve_default_org_tz(session: AsyncSession) -> zoneinfo.ZoneInfo:
    """The canonical tz of the single/default org (D1) — for the global review-sweep horizon."""
    org_id = (
        await session.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
    ).scalar_one_or_none()
    if org_id is None:
        return _valid_tz(get_settings().easysynq_org_timezone) or zoneinfo.ZoneInfo("UTC")
    return await resolve_org_tz(session, org_id)
