"""S-rec-2 unit: the pure ``retention_until`` ISO-8601 duration computation (doc 06 §5.3)."""

from __future__ import annotations

import datetime

import pytest

from easysynq_api.domain.records.retention import retention_until

_BASIS = datetime.date(2026, 1, 15)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        ("P10Y", datetime.date(2036, 1, 15)),
        ("P3Y", datetime.date(2029, 1, 15)),
        ("P1Y6M", datetime.date(2027, 7, 15)),
        ("P6M", datetime.date(2026, 7, 15)),
        ("P90D", datetime.date(2026, 4, 15)),
        ("P2W", datetime.date(2026, 1, 29)),
        ("P1Y2M10D", datetime.date(2027, 3, 25)),
    ],
)
def test_retention_until_iso_durations(duration: str, expected: datetime.date) -> None:
    assert retention_until(_BASIS, duration) == expected


@pytest.mark.unit
def test_retention_until_day_clamp_month_end() -> None:
    # Jan 31 + 1 month → Feb 28 (2027 is not a leap year): the day is clamped, never raises.
    assert retention_until(datetime.date(2027, 1, 31), "P1M") == datetime.date(2027, 2, 28)
    # Jan 31 + 1 month → Feb 29 in a leap year.
    assert retention_until(datetime.date(2028, 1, 31), "P1M") == datetime.date(2028, 2, 29)


@pytest.mark.unit
def test_retention_until_leap_year_basis() -> None:
    # Feb 29 + 1 year → Feb 28 (target year has no Feb 29).
    assert retention_until(datetime.date(2028, 2, 29), "P1Y") == datetime.date(2029, 2, 28)


@pytest.mark.unit
@pytest.mark.parametrize("duration", ["PERMANENT", "permanent", " Permanent "])
def test_retention_until_permanent_is_none(duration: str) -> None:
    assert retention_until(_BASIS, duration) is None


@pytest.mark.unit
def test_retention_until_none_basis_is_none() -> None:
    # An event:* basis whose event has not fired — no computable expiry, never auto-swept.
    assert retention_until(None, "P1Y") is None


@pytest.mark.unit
@pytest.mark.parametrize("duration", ["", "P", "10Y", "P10", "PT1H", "P1Yextra", "garbage"])
def test_retention_until_malformed_raises(duration: str) -> None:
    with pytest.raises(ValueError, match="duration"):
        retention_until(_BASIS, duration)
