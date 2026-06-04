"""S-rec-2 unit: the pure ``retention_until`` ISO-8601 duration computation (doc 06 §5.3)."""

from __future__ import annotations

import datetime

import pytest

from easysynq_api.db.models._retention_enums import DispositionAction as DA
from easysynq_api.domain.records.retention import (
    action_preservation_rank,
    duration_ge,
    retention_until,
)

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


# --- the extend-forward comparators (S-rec-4, doc 06 §5.2) -------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("P10Y", "P5Y", True),  # longer >= shorter
        ("P5Y", "P10Y", False),  # shorter < longer (a reduction)
        ("P1Y", "P12M", True),  # equal forms compare equal
        ("P12M", "P1Y", True),
        ("P10Y", "P10Y", True),  # equal
        ("PERMANENT", "P10Y", True),  # PERMANENT is the maximum
        ("PERMANENT", "PERMANENT", True),
        ("P10Y", "PERMANENT", False),  # finite < PERMANENT
        ("P366D", "P1Y", True),  # 366 days >= 1 year (2000 is a leap year off the ref date)
    ],
)
def test_duration_ge(a: str, b: str, expected: bool) -> None:
    assert duration_ge(a, b) is expected


@pytest.mark.unit
def test_action_preservation_rank_ordering() -> None:
    # DESTROY < ARCHIVE_COLD == TRANSFER < RETAIN_PERMANENT.
    assert action_preservation_rank(DA.DESTROY) < action_preservation_rank(DA.ARCHIVE_COLD)
    assert action_preservation_rank(DA.ARCHIVE_COLD) == action_preservation_rank(DA.TRANSFER)
    assert action_preservation_rank(DA.TRANSFER) < action_preservation_rank(DA.RETAIN_PERMANENT)
