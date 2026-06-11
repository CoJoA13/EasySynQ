# apps/api/tests/unit/test_objective_rules.py
import datetime
from decimal import Decimal

import pytest

from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.domain.objectives.rules import (
    attainment,
    pct_toward_target,
    rag_status,
)

pytestmark = pytest.mark.unit

HI = ObjectiveDirection.HIGHER_IS_BETTER
LO = ObjectiveDirection.LOWER_IS_BETTER
D = Decimal


def test_unmeasured_when_current_is_none() -> None:
    assert (
        rag_status(current=None, target=D(90), direction=HI, at_risk_threshold=D(80))
        == "unmeasured"
    )


def test_higher_is_better_green_amber_red() -> None:
    assert rag_status(current=D(95), target=D(90), direction=HI, at_risk_threshold=D(80)) == "green"
    assert rag_status(current=D(85), target=D(90), direction=HI, at_risk_threshold=D(80)) == "amber"
    assert rag_status(current=D(75), target=D(90), direction=HI, at_risk_threshold=D(80)) == "red"
    # boundary: current == target → green; current == threshold → amber
    assert rag_status(current=D(90), target=D(90), direction=HI, at_risk_threshold=D(80)) == "green"
    assert rag_status(current=D(80), target=D(90), direction=HI, at_risk_threshold=D(80)) == "amber"


def test_lower_is_better_green_amber_red() -> None:
    # "reduce complaints": target 5, at_risk 10
    assert rag_status(current=D(4), target=D(5), direction=LO, at_risk_threshold=D(10)) == "green"
    assert rag_status(current=D(8), target=D(5), direction=LO, at_risk_threshold=D(10)) == "amber"
    assert rag_status(current=D(12), target=D(5), direction=LO, at_risk_threshold=D(10)) == "red"
    assert rag_status(current=D(5), target=D(5), direction=LO, at_risk_threshold=D(10)) == "green"
    assert rag_status(current=D(10), target=D(5), direction=LO, at_risk_threshold=D(10)) == "amber"


def test_no_threshold_collapses_amber_to_red() -> None:
    assert rag_status(current=D(85), target=D(90), direction=HI, at_risk_threshold=None) == "red"
    assert rag_status(current=D(95), target=D(90), direction=HI, at_risk_threshold=None) == "green"


def test_pct_toward_target_higher_is_better() -> None:
    # baseline 50, target 100, current 75 → 50%
    assert pct_toward_target(
        current=D(75), target=D(100), baseline=D(50), direction=HI
    ) == pytest.approx(0.5)
    # no baseline → fraction of target
    assert pct_toward_target(
        current=D(45), target=D(90), baseline=None, direction=HI
    ) == pytest.approx(0.5)
    # current None → None; zero span → None
    assert pct_toward_target(current=None, target=D(90), baseline=None, direction=HI) is None
    assert pct_toward_target(current=D(75), target=D(50), baseline=D(50), direction=HI) is None


def test_pct_toward_target_lower_is_better() -> None:
    # baseline 10 (start), target 5, current 8 → (10-8)/(10-5) = 40% (NOT 160%)
    assert pct_toward_target(
        current=D(8), target=D(5), baseline=D(10), direction=LO
    ) == pytest.approx(0.4)
    # met-or-better reads ≥100%: current 5 → 100%, current 3 → 140%
    assert pct_toward_target(
        current=D(5), target=D(5), baseline=D(10), direction=LO
    ) == pytest.approx(1.0)
    # lower-is-better WITHOUT a baseline is undefined → None (the no-160%-bug case)
    assert pct_toward_target(current=D(8), target=D(5), baseline=None, direction=LO) is None
    # zero span → None
    assert pct_toward_target(current=D(7), target=D(5), baseline=D(5), direction=LO) is None


def test_attainment_met_missed_in_progress() -> None:
    due = datetime.date(2026, 6, 30)
    # before due → in_progress regardless of value
    assert (
        attainment(
            current=D(50), target=D(90), direction=HI, due_date=due, today=datetime.date(2026, 6, 1)
        )
        == "in_progress"
    )
    # at/after due: target reached → met, else missed
    assert (
        attainment(
            current=D(95), target=D(90), direction=HI, due_date=due, today=datetime.date(2026, 7, 1)
        )
        == "met"
    )
    assert (
        attainment(
            current=D(50), target=D(90), direction=HI, due_date=due, today=datetime.date(2026, 7, 1)
        )
        == "missed"
    )
    # lower-is-better met
    assert (
        attainment(
            current=D(3), target=D(5), direction=LO, due_date=due, today=datetime.date(2026, 7, 1)
        )
        == "met"
    )
    # current None at due → missed (never measured)
    assert (
        attainment(
            current=None, target=D(90), direction=HI, due_date=due, today=datetime.date(2026, 7, 1)
        )
        == "missed"
    )
