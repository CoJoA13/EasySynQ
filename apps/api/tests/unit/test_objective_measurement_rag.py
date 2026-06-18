# apps/api/tests/unit/test_objective_measurement_rag.py
"""S-obj-freeze — per-reading RAG on the `_measurement` serializer.

The serializer grades each reading via the pure `rag_status` rule entirely off the reading's own
**frozen** basis: value (`m.value`) vs `target_at_capture`, graded under `direction_at_capture` +
`at_risk_threshold_at_capture` (all snapshotted at capture). A later commitment revision can no
longer re-grade a historical reading. A measurement always has a value, so `rag` is never
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


def _m(
    *,
    value: str,
    target: str,
    direction: ObjectiveDirection = HI,
    threshold: str | None = "95",
    unit: str = "%",
) -> KpiMeasurement:
    """An in-memory KpiMeasurement — only the fields the serializer reads are set. The grading basis
    (direction + threshold) is frozen on the row, mirroring production capture."""
    return KpiMeasurement(
        id=uuid.uuid4(),
        objective_id=uuid.uuid4(),
        record_id=uuid.uuid4(),
        period=datetime.date(2026, 1, 1),
        value=Decimal(value),
        target_at_capture=Decimal(target),
        direction_at_capture=direction,
        at_risk_threshold_at_capture=Decimal(threshold) if threshold is not None else None,
        unit=unit,
        source="manual",
        created_at=datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC),
    )


# --- HIGHER_IS_BETTER x green / amber / red ---
def test_higher_is_better_green_at_or_above_target() -> None:
    assert (
        _measurement(_m(value="100", target="98", direction=HI, threshold="95"))["rag"] == "green"
    )


def test_higher_is_better_amber_between_threshold_and_target() -> None:
    assert _measurement(_m(value="96", target="98", direction=HI, threshold="95"))["rag"] == "amber"


def test_higher_is_better_red_below_threshold() -> None:
    assert _measurement(_m(value="90", target="98", direction=HI, threshold="95"))["rag"] == "red"


# --- LOWER_IS_BETTER x green / amber / red ---
def test_lower_is_better_green_at_or_below_target() -> None:
    assert _measurement(_m(value="2", target="3", direction=LO, threshold="5"))["rag"] == "green"


def test_lower_is_better_amber_between_target_and_threshold() -> None:
    assert _measurement(_m(value="4", target="3", direction=LO, threshold="5"))["rag"] == "amber"


def test_lower_is_better_red_above_threshold() -> None:
    assert _measurement(_m(value="6", target="3", direction=LO, threshold="5"))["rag"] == "red"


# --- None threshold → green/red only (no amber band) ---
def test_none_threshold_higher_is_better_green_at_or_above() -> None:
    assert _measurement(_m(value="98", target="98", direction=HI, threshold=None))["rag"] == "green"


def test_none_threshold_higher_is_better_red_below_target() -> None:
    assert _measurement(_m(value="97", target="98", direction=HI, threshold=None))["rag"] == "red"


def test_none_threshold_lower_is_better_green_at_or_below() -> None:
    assert _measurement(_m(value="3", target="3", direction=LO, threshold=None))["rag"] == "green"


def test_none_threshold_lower_is_better_red_above_target() -> None:
    assert _measurement(_m(value="4", target="3", direction=LO, threshold=None))["rag"] == "red"


# --- the per-reading verdict uses the FROZEN target_at_capture, not a live target ---
def test_rag_grades_against_frozen_target_at_capture() -> None:
    # A reading frozen at target 90 + value 92 is green even though the live commitment may now
    # target 100 (the serializer reads only m.target_at_capture for the target).
    assert _measurement(_m(value="92", target="90", direction=HI, threshold="88"))["rag"] == "green"


# --- the per-reading verdict uses the FROZEN direction_at_capture (the S-obj-freeze fix) ---
def test_rag_reads_frozen_direction_not_live() -> None:
    # Same value + target, opposite FROZEN direction → opposite verdict: proves the serializer
    # grades off the per-reading direction_at_capture. A HIGHER→LOWER commitment flip can no longer
    # re-grade this reading because its direction is snapshotted on the row.
    hi = _measurement(_m(value="95", target="98", direction=HI, threshold=None))
    lo = _measurement(_m(value="95", target="98", direction=LO, threshold=None))
    assert hi["rag"] == "red"  # 95 < 98 under HIGHER (no amber band)
    assert lo["rag"] == "green"  # 95 <= 98 under LOWER


# --- the per-reading verdict uses the FROZEN at_risk_threshold_at_capture (S-obj-freeze) ---
def test_rag_reads_frozen_threshold_not_live() -> None:
    # Same value + target + direction, different FROZEN amber band → different verdict: proves the
    # serializer grades off the per-reading at_risk_threshold_at_capture, not the live band.
    wide = _measurement(_m(value="96", target="98", direction=HI, threshold="95"))
    narrow = _measurement(_m(value="96", target="98", direction=HI, threshold="97"))
    assert wide["rag"] == "amber"  # 95 <= 96 < 98
    assert narrow["rag"] == "red"  # 96 < 97 amber floor → below the band


# --- the other fields are byte-unchanged; the frozen basis stays INTERNAL (not exposed) ---
def test_other_fields_unchanged() -> None:
    m = _m(value="96", target="98", direction=HI, threshold="95")
    out = _measurement(m)
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
    m = _m(value="100", target="98", direction=HI, threshold="95")
    m.objective_id = None
    out = _measurement(m)
    assert out["objective_id"] is None
    assert out["rag"] == "green"
