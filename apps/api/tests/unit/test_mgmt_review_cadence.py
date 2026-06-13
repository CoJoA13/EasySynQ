"""Unit tests for the management-review cadence math (S-mr-1, clause 9.3 §s6).

``next_mr_due`` is the pure "when is the next review due" rule: it adds ``cadence_months`` to the
last released review's ``effective_from`` date (calendar month-add via ``add_months``). A ``None``
history (no prior released MR) returns ``None`` — the SENTINEL the sweep reads as "mint the first
one now" (there is no anchor to count forward from, so the first scheduled review is due
immediately)."""

from __future__ import annotations

import datetime

from easysynq_api.services.mgmt_review.cadence import (
    MR_REVIEW_LEAD_DAYS,
    mr_review_state,
    next_mr_due,
)


def test_mr_review_state_buckets() -> None:
    due = datetime.date(2026, 9, 1)
    assert mr_review_state(None, datetime.date(2026, 6, 1)) is None  # not scheduled
    assert mr_review_state(due, datetime.date(2026, 9, 1)) == "overdue"  # today == due
    assert mr_review_state(due, datetime.date(2026, 9, 2)) == "overdue"  # past due
    lead = due - datetime.timedelta(days=MR_REVIEW_LEAD_DAYS)
    assert mr_review_state(due, lead) == "due_soon"  # exactly on the lead boundary
    assert mr_review_state(due, lead - datetime.timedelta(days=1)) == "current"  # before the lead


def test_next_mr_due_adds_cadence_to_last_effective() -> None:
    assert next_mr_due(datetime.date(2025, 6, 1), 12) == datetime.date(2026, 6, 1)


def test_next_mr_due_respects_a_non_default_cadence() -> None:
    # a 6-month cadence
    assert next_mr_due(datetime.date(2026, 1, 15), 6) == datetime.date(2026, 7, 15)


def test_next_mr_due_clamps_the_day_to_the_target_month_length() -> None:
    # Aug 31 + 6 months → Feb 28 (add_months clamps the day to the month length)
    assert next_mr_due(datetime.date(2025, 8, 31), 6) == datetime.date(2026, 2, 28)


def test_next_mr_due_none_history_is_the_mint_now_sentinel() -> None:
    # No prior released MR → None: the caller (sweep) treats None as "mint the first review now".
    assert next_mr_due(None, 12) is None
