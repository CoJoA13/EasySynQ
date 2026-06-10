"""Periodic re-review (D5 — doc 04 §9, doc 05 §9.1, spec S-drift-1).

The ONE recompute rule + the ``review_state`` read-time projection live here and nowhere else.
``next_review_due`` is STORED on ``documented_information`` (a confirm resets it from the review
date); ``review_state`` is NEVER stored (always derived — the owner's fork). Periods are integer
MONTHS (psycopg3 cannot load month-bearing PG intervals into timedelta)."""

from __future__ import annotations

import calendar
import datetime
from zoneinfo import ZoneInfo

from ...config import get_settings

REVIEW_PERIOD_DEFAULT_MONTHS = 24  # doc 04's "e.g. 12/24/36 months" middle value (owner fork)
REVIEW_LEAD_DAYS = 30  # doc 04 §9.1's lead window ("e.g. 30 days"); org-config later, additive


def add_months(day: datetime.date, months: int) -> datetime.date:
    """Calendar month-add, day clamped to the target month's length (Jan 31 + 1mo → Feb 28/29)."""
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _org_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().easysynq_org_timezone)


def today_org() -> datetime.date:
    """Today as a DATE in the org timezone (R8: dates display in org tz; UTC authoritative)."""
    return datetime.datetime.now(_org_tz()).date()


def compute_next_review_due(
    review_period_months: int | None,
    last_reviewed_at: datetime.datetime | None,
    effective_from: datetime.datetime | None,
) -> datetime.date | None:
    """anchor = the LATER of (last_reviewed_at, effective_from); + period months, org-tz dated.

    One rule, three triggers (release / review-confirm / PATCH): a re-release after a confirm
    anchors on the newer effective_from, a confirm after a release anchors on the newer review
    date. NULL period or no anchor → None (not scheduled)."""
    if review_period_months is None:
        return None
    anchors = [a for a in (last_reviewed_at, effective_from) if a is not None]
    if not anchors:
        return None
    return add_months(max(anchors).astimezone(_org_tz()).date(), review_period_months)


def review_state(next_review_due: datetime.date | None, today: datetime.date) -> str | None:
    """The derived currency projection: current | due_soon | overdue (None = not scheduled)."""
    if next_review_due is None:
        return None
    if today >= next_review_due:
        return "overdue"
    if today >= next_review_due - datetime.timedelta(days=REVIEW_LEAD_DAYS):
        return "due_soon"
    return "current"
