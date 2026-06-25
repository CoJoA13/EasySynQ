"""Unit tests for the shared pure calendar parsers (S-notify-7). The editor (fail-loud → 422) and
the resolver (fail-safe → degrade) BOTH use these, so parity can't drift."""

import datetime

from easysynq_api.services.notifications.calendar_spec import (
    is_valid_timezone,
    parse_holiday,
    parse_working_days,
)


def test_working_days_valid_and_dedup():
    assert parse_working_days([1, 2, 3, 4, 5]) == frozenset({1, 2, 3, 4, 5})
    assert parse_working_days([1, 1, 2, 7]) == frozenset({1, 2, 7})  # dedup + ACCEPT


def test_working_days_broken_returns_none():
    assert parse_working_days([]) is None
    assert parse_working_days([0]) is None
    assert parse_working_days([8]) is None
    assert parse_working_days([True]) is None  # bool is an int subclass — rejected
    assert parse_working_days([1.0]) is None  # float rejected
    assert parse_working_days(["1"]) is None  # JSON-string rejected
    assert parse_working_days("67") is None
    assert parse_working_days(5) is None
    assert parse_working_days(None) is None


def test_parse_holiday_str_coercion_keeps_resolver_byte_identical():
    assert parse_holiday("2026-12-25") == datetime.date(2026, 12, 25)
    # The resolver coerced with str() — an int entry like 20260101 must still parse.
    assert parse_holiday(20260101) == datetime.date(2026, 1, 1)


def test_parse_holiday_broken_returns_none():
    assert parse_holiday("2026-13-01") is None
    assert parse_holiday("nope") is None
    assert parse_holiday("") is None
    assert parse_holiday(None) is None
    assert parse_holiday([1]) is None


def test_is_valid_timezone():
    assert is_valid_timezone("America/Chicago") is True
    assert is_valid_timezone("UTC") is True
    assert is_valid_timezone("Mars/Phobos") is False
    assert is_valid_timezone("") is False
