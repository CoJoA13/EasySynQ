# apps/api/tests/unit/test_objective_commitment.py
import datetime
import uuid
from decimal import Decimal

import pytest
import rfc8785

from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.domain.objectives.commitment import build_commitment

pytestmark = pytest.mark.unit

HI = ObjectiveDirection.HIGHER_IS_BETTER
LO = ObjectiveDirection.LOWER_IS_BETTER


def test_build_commitment_all_fields_are_json_strings() -> None:
    c = build_commitment(
        target_value=Decimal("98.5"),
        unit="%",
        direction=HI,
        due_date=datetime.date(2026, 12, 31),
        at_risk_threshold=Decimal("95"),
        baseline_value=Decimal("90"),
        policy_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )
    assert c == {
        "target_value": "98.5",
        "unit": "%",
        "direction": "HIGHER_IS_BETTER",
        "due_date": "2026-12-31",
        "at_risk_threshold": "95",
        "baseline_value": "90",
        "policy_id": "11111111-1111-1111-1111-111111111111",
    }


def test_build_commitment_nullable_fields_are_none() -> None:
    c = build_commitment(
        target_value=Decimal("5"),
        unit="count",
        direction=LO,
        due_date=datetime.date(2026, 6, 30),
        at_risk_threshold=None,
        baseline_value=None,
        policy_id=None,
    )
    assert c["at_risk_threshold"] is None
    assert c["baseline_value"] is None
    assert c["policy_id"] is None


def test_build_commitment_is_rfc8785_serializable_and_deterministic() -> None:
    # The WORM source blob is rfc8785.dumps(commitment); it must serialize and be byte-stable.
    c = build_commitment(
        target_value=Decimal("98"),
        unit="%",
        direction=HI,
        due_date=datetime.date(2026, 12, 31),
        at_risk_threshold=None,
        baseline_value=None,
        policy_id=None,
    )
    assert rfc8785.dumps(c) == rfc8785.dumps(c)
    # decimals are strings, never floats (exact, reproducible bytes)
    assert b"98.0" not in rfc8785.dumps(c)
