# apps/api/tests/unit/test_risk_rules.py
"""Pure risk-scoring rules + the criteria GOLDEN pin (S-risk-1, R49 derive-and-freeze).

The golden ``test_default_criteria_is_golden_pinned`` exists so an in-place edit to a
``scoring_method``'s band thresholds FAILS CI — forcing the mint-a-new-value path the freeze relies
on (spec §4 L2-CRITICAL-2; the convention alone is unenforced)."""

import pytest

from easysynq_api.db.models._risk_enums import ScoringMethod
from easysynq_api.domain.risk.rules import (
    BAND_RANK,
    BAND_TONE,
    RiskBand,
    default_criteria,
    risk_band,
    risk_rating,
)


def test_risk_rating_worked_example() -> None:
    # doc 15 §8.10b: likelihood 4 x severity 5 → risk_rating 20.
    assert risk_rating(4, 5, ScoringMethod.MATRIX_5X5) == 20


@pytest.mark.parametrize(
    ("likelihood", "severity", "expected"),
    [(1, 1, 1), (5, 5, 25), (2, 3, 6), (3, 4, 12), (4, 4, 16), (5, 2, 10)],
)
def test_risk_rating_products(likelihood: int, severity: int, expected: int) -> None:
    assert risk_rating(likelihood, severity, ScoringMethod.MATRIX_5X5) == expected


@pytest.mark.parametrize(("likelihood", "severity"), [(0, 3), (6, 2), (3, 0), (2, 6)])
def test_risk_rating_rejects_out_of_range(likelihood: int, severity: int) -> None:
    with pytest.raises(ValueError):
        risk_rating(likelihood, severity, ScoringMethod.MATRIX_5X5)


def test_default_criteria_is_golden_pinned() -> None:
    # ⚠ Changing this literal in place re-grades every stored row's band against the new thresholds.
    # That is forbidden: to change the methodology, mint a NEW ScoringMethod value (append-only) so
    # existing rows keep their frozen criteria. This test is the enforcement (spec §4 / R49).
    assert default_criteria(ScoringMethod.MATRIX_5X5) == {
        "method": "5x5_matrix",
        "max_rating": 25,
        "bands": [
            {"band": "critical", "min": 20},
            {"band": "high", "min": 12},
            {"band": "medium", "min": 6},
            {"band": "low", "min": 1},
        ],
    }


@pytest.mark.parametrize(
    ("rating", "band"),
    [
        (25, RiskBand.critical),
        (20, RiskBand.critical),
        (16, RiskBand.high),
        (12, RiskBand.high),
        (10, RiskBand.medium),
        (6, RiskBand.medium),
        (5, RiskBand.low),
        (1, RiskBand.low),
    ],
)
def test_risk_band_against_default_criteria(rating: int, band: RiskBand) -> None:
    assert risk_band(rating, default_criteria(ScoringMethod.MATRIX_5X5)) is band


def test_risk_band_is_total_over_1_to_25() -> None:
    crit = default_criteria(ScoringMethod.MATRIX_5X5)
    # Every rating 1..25 (incl. the unreachable 5x5 gaps) maps to a real band — never unscored.
    for rating in range(1, 26):
        assert risk_band(rating, crit) is not RiskBand.unscored


def test_risk_band_below_floor_is_unscored() -> None:
    assert risk_band(0, default_criteria(ScoringMethod.MATRIX_5X5)) is RiskBand.unscored


def test_band_rank_orders_danger_first() -> None:
    assert (
        BAND_RANK[RiskBand.critical]
        < BAND_RANK[RiskBand.high]
        < BAND_RANK[RiskBand.medium]
        < BAND_RANK[RiskBand.low]
        < BAND_RANK[RiskBand.unscored]
    )


def test_band_tone_reuses_objectives_vocabulary() -> None:
    # Critical + High share the danger tone (distinguished by label/rank, never colour alone).
    assert BAND_TONE[RiskBand.critical] == "danger"
    assert BAND_TONE[RiskBand.high] == "danger"
    assert BAND_TONE[RiskBand.medium] == "warning"
    assert BAND_TONE[RiskBand.low] == "success"
    assert BAND_TONE[RiskBand.unscored] == "neutral"
