# apps/api/tests/unit/test_objective_measurement_rag.py
"""S-obj-charts (Part 1) — per-reading RAG on the `_measurement` serializer.

The serializer grades each reading via the pure `rag_status` rule: value (`m.value`) vs the
per-reading **frozen** `target_at_capture`, with direction + at_risk_threshold from the GOVERNING
commitment (passed in as keyword args). A measurement always has a value, so `rag` is never
"unmeasured" here. These call `_measurement(...)` directly with in-memory KpiMeasurement instances
(no session needed for serialisation) — they run natively on Windows.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

import pytest

from easysynq_api.api.objectives import _measurement
from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.db.models.kpi_measurement import KpiMeasurement

pytestmark = pytest.mark.unit

HI = ObjectiveDirection.HIGHER_IS_BETTER
LO = ObjectiveDirection.LOWER_IS_BETTER


def _m(*, value: str, target: str, unit: str = "%") -> KpiMeasurement:
    """An in-memory KpiMeasurement — only the fields the serializer reads are set."""
    return KpiMeasurement(
        id=uuid.uuid4(),
        objective_id=uuid.uuid4(),
        record_id=uuid.uuid4(),
        period=datetime.date(2026, 1, 1),
        value=Decimal(value),
        target_at_capture=Decimal(target),
        unit=unit,
        source="manual",
        created_at=datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC),
    )


# --- HIGHER_IS_BETTER x green / amber / red ---
def test_higher_is_better_green_at_or_above_target() -> None:
    out = _measurement(_m(value="100", target="98"), direction=HI, at_risk_threshold=Decimal("95"))
    assert out["rag"] == "green"


def test_higher_is_better_amber_between_threshold_and_target() -> None:
    out = _measurement(_m(value="96", target="98"), direction=HI, at_risk_threshold=Decimal("95"))
    assert out["rag"] == "amber"


def test_higher_is_better_red_below_threshold() -> None:
    out = _measurement(_m(value="90", target="98"), direction=HI, at_risk_threshold=Decimal("95"))
    assert out["rag"] == "red"


# --- LOWER_IS_BETTER x green / amber / red ---
def test_lower_is_better_green_at_or_below_target() -> None:
    out = _measurement(_m(value="2", target="3"), direction=LO, at_risk_threshold=Decimal("5"))
    assert out["rag"] == "green"


def test_lower_is_better_amber_between_target_and_threshold() -> None:
    out = _measurement(_m(value="4", target="3"), direction=LO, at_risk_threshold=Decimal("5"))
    assert out["rag"] == "amber"


def test_lower_is_better_red_above_threshold() -> None:
    out = _measurement(_m(value="6", target="3"), direction=LO, at_risk_threshold=Decimal("5"))
    assert out["rag"] == "red"


# --- None threshold → green/red only (no amber band) ---
def test_none_threshold_higher_is_better_green_at_or_above() -> None:
    out = _measurement(_m(value="98", target="98"), direction=HI, at_risk_threshold=None)
    assert out["rag"] == "green"


def test_none_threshold_higher_is_better_red_below_target() -> None:
    out = _measurement(_m(value="97", target="98"), direction=HI, at_risk_threshold=None)
    assert out["rag"] == "red"


def test_none_threshold_lower_is_better_green_at_or_below() -> None:
    out = _measurement(_m(value="3", target="3"), direction=LO, at_risk_threshold=None)
    assert out["rag"] == "green"


def test_none_threshold_lower_is_better_red_above_target() -> None:
    out = _measurement(_m(value="4", target="3"), direction=LO, at_risk_threshold=None)
    assert out["rag"] == "red"


# --- the per-reading verdict uses the FROZEN target_at_capture, not a passed-in target ---
def test_rag_grades_against_frozen_target_at_capture() -> None:
    # A reading frozen at target 90 + value 92 is green even though the live commitment may now
    # target 100 (the serializer reads only m.target_at_capture for the target).
    out = _measurement(_m(value="92", target="90"), direction=HI, at_risk_threshold=Decimal("88"))
    assert out["rag"] == "green"


# --- the other fields are byte-unchanged (regression) ---
def test_other_fields_unchanged() -> None:
    m = _m(value="96", target="98")
    out = _measurement(m, direction=HI, at_risk_threshold=Decimal("95"))
    assert out == {
        "id": str(m.id),
        "objective_id": str(m.objective_id),
        "record_id": str(m.record_id),
        "period": "2026-01-01",
        "value": "96",
        "target_at_capture": "98",
        "unit": "%",
        "source": "manual",
        "created_at": "2026-01-01T12:00:00+00:00",
        "rag": "amber",
    }


def test_objective_id_none_serialises_to_none() -> None:
    m = _m(value="100", target="98")
    m.objective_id = None
    out = _measurement(m, direction=HI, at_risk_threshold=Decimal("95"))
    assert out["objective_id"] is None
    assert out["rag"] == "green"
