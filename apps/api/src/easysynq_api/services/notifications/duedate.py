"""S-duedate-snap (R55): the resolve+snap seam used by every task-materialize site.

Snapping ``due_at`` to a working day at materialize (doc 10 §9.5) closes the weekend-OVERDUE gap at
the source: with ``due_at`` itself on a working day, OVERDUE (``now >= due_at``) and every
business-day offset land on a working day, so the timer needs no OVERDUE special-casing (the
S-notify-6 D-5 upstream fix).

Two shapes (spec §4/§5, D-5):
- INSTANT sites (engine ``now+hours``, DOC_ACK ``now+days``) call ``snap_due_at`` — an instant has
  no "calendar day", so it is simply snapped in the calendar's tz.
- DATE-ANCHORED sites (review/spawn/cadence) call ``resolve_calendar`` first and BUILD the due_at
  at midnight in ``cal.tz`` (not the env ``easysynq_org_timezone``), then ``snap_to_working_day`` —
  so a single business-day frame governs build + snap + the timer.
"""

import datetime
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .timer import Calendar, snap_to_working_day

__all__ = ["Calendar", "resolve_calendar", "snap_due_at", "snap_to_working_day"]


async def resolve_calendar(session: AsyncSession, org_id: uuid.UUID) -> Calendar:
    """The org's fail-safe working ``Calendar`` — the single tz/working-day frame for both BUILDING
    a date-anchored due_at (D-5) and snapping it.

    Lazy-imports ``resolve_working_calendar`` from ``escalation``: a top-level import would cycle
    (escalation → the ``workflow`` package ``__init__`` → ``workflow.engine``, one of the call
    sites); mirrors the function-local ``from ..notifications.dispatch import …`` lazy pattern.
    """
    from .escalation import resolve_working_calendar

    return await resolve_working_calendar(session, org_id)


async def snap_due_at(
    session: AsyncSession, org_id: uuid.UUID, due_at: datetime.datetime | None
) -> datetime.datetime | None:
    """Resolve the org's calendar and snap ``due_at`` forward to a working day. ``None`` passes
    through (an undated / SLA-less task stays undated). For INSTANT materialize sites."""
    if due_at is None:
        return None
    cal = await resolve_calendar(session, org_id)
    return snap_to_working_day(due_at, cal)
