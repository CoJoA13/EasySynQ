"""Monthly ``audit_event`` partition rotation (slice S6, doc 12 §4.4, doc 18 §4 line 213).

There is intentionally **no DEFAULT partition** (it would block creating a covering month once any
row landed in it). Instead this keeps a rolling runway of the current month + the next two, created
via the SECURITY-DEFINER ``easysynq_create_audit_partition`` function (so the non-owner app/beat
role can add + lock down a month without holding ``CREATE`` on the schema). Idempotent: creating a
that already exists is a no-op. Driven daily by the ``roll_partitions`` Beat task and on demand by
the ``easysynq audit ensure-partitions`` CLI (the Beat-down fallback).
"""

from __future__ import annotations

import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_MONTHS_AHEAD = 2  # current month + the next two → always ≥2 months of runway


def _month_start(value: datetime.date) -> datetime.date:
    return value.replace(day=1)


def _add_month(value: datetime.date) -> datetime.date:
    year, month = value.year, value.month + 1
    if month > 12:
        year, month = year + 1, 1
    return datetime.date(year, month, 1)


def upcoming_month_starts(today: datetime.date) -> list[datetime.date]:
    """The month-start dates whose partitions must exist: this month + ``_MONTHS_AHEAD`` more."""
    start = _month_start(today)
    starts = [start]
    for _ in range(_MONTHS_AHEAD):
        start = _add_month(start)
        starts.append(start)
    return starts


async def ensure_partitions(session: AsyncSession, today: datetime.date | None = None) -> list[str]:
    """Ensure the rolling window of monthly partitions exists; returns the month labels ensured."""
    day = today or datetime.datetime.now(datetime.UTC).date()
    ensured: list[str] = []
    for start in upcoming_month_starts(day):
        await session.execute(
            text("SELECT easysynq_create_audit_partition(:start)"),
            {"start": start},
        )
        ensured.append(start.strftime("%Y_%m"))
    await session.commit()
    return ensured
