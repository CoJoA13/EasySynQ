"""Unit tests for the D5 review domain rules (services/vault/review.py)."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest

from easysynq_api.services.vault.review import (
    REVIEW_LEAD_DAYS,
    REVIEW_PERIOD_DEFAULT_MONTHS,
    add_months,
    compute_next_review_due,
    review_state,
)

pytestmark = pytest.mark.unit

UTC = datetime.UTC


def test_default_period_is_24_months() -> None:
    assert REVIEW_PERIOD_DEFAULT_MONTHS == 24


def test_add_months_simple() -> None:
    assert add_months(datetime.date(2026, 1, 15), 12) == datetime.date(2027, 1, 15)


def test_add_months_clamps_day_to_target_month() -> None:
    assert add_months(datetime.date(2026, 1, 31), 1) == datetime.date(2026, 2, 28)
    assert add_months(datetime.date(2024, 1, 31), 1) == datetime.date(2024, 2, 29)  # leap


def test_add_months_year_rollover() -> None:
    assert add_months(datetime.date(2026, 11, 30), 3) == datetime.date(2027, 2, 28)


def test_compute_none_when_period_null() -> None:
    eff = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    assert compute_next_review_due(None, None, eff, ZoneInfo("UTC")) is None


def test_compute_none_when_no_anchor() -> None:
    assert compute_next_review_due(24, None, None, ZoneInfo("UTC")) is None


def test_compute_anchors_on_effective_from() -> None:
    eff = datetime.datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    assert compute_next_review_due(24, None, eff, ZoneInfo("UTC")) == datetime.date(2028, 1, 10)


def test_compute_anchor_is_the_later_timestamp() -> None:
    eff = datetime.datetime(2026, 1, 10, tzinfo=UTC)
    reviewed = datetime.datetime(2026, 6, 1, tzinfo=UTC)
    # confirm after release → anchors on the review date
    assert compute_next_review_due(12, reviewed, eff, ZoneInfo("UTC")) == datetime.date(2027, 6, 1)
    # re-release after a confirm → anchors on the newer effective_from
    eff2 = datetime.datetime(2026, 9, 1, tzinfo=UTC)
    assert compute_next_review_due(12, reviewed, eff2, ZoneInfo("UTC")) == datetime.date(2027, 9, 1)


def test_review_state_projection_boundaries() -> None:
    due = datetime.date(2026, 7, 1)
    lead = datetime.timedelta(days=REVIEW_LEAD_DAYS)
    assert review_state(None, datetime.date(2026, 6, 9)) is None
    assert review_state(due, due - lead - datetime.timedelta(days=1)) == "current"
    assert review_state(due, due - lead) == "due_soon"  # boundary: lead-window entry day
    assert review_state(due, due - datetime.timedelta(days=1)) == "due_soon"
    assert review_state(due, due) == "overdue"  # boundary: the due day itself
    assert review_state(due, due + datetime.timedelta(days=30)) == "overdue"


def test_compute_converts_anchor_to_org_timezone() -> None:
    # The org-tz conversion must be load-bearing, not identity: the SAME UTC timestamp yields
    # a different date in Auckland vs UTC, so the explicit org_tz param is actually used.
    # 2026-01-31 11:00 UTC is 2026-02-01 00:00 Auckland (UTC+13 in January) → anchors on Feb 1.
    eff = datetime.datetime(2026, 1, 31, 11, 0, tzinfo=UTC)
    out = compute_next_review_due(12, None, eff, ZoneInfo("Pacific/Auckland"))
    assert out == datetime.date(2027, 2, 1)
    out_utc = compute_next_review_due(12, None, eff, ZoneInfo("UTC"))
    assert out_utc == datetime.date(2027, 1, 31)
