import pytest

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.db.models._objective_enums import (
    OBJECTIVE_DIRECTION_VALUES,
    ObjectiveDirection,
)

pytestmark = pytest.mark.unit


def test_direction_values_round_trip() -> None:
    assert OBJECTIVE_DIRECTION_VALUES == ("HIGHER_IS_BETTER", "LOWER_IS_BETTER")
    assert {d.value for d in ObjectiveDirection} == set(OBJECTIVE_DIRECTION_VALUES)


def test_new_objective_event_types_present() -> None:
    for name in (
        "OBJECTIVE_MEASUREMENT_RECORDED",
        "OBJECTIVE_PLAN_ADDED",
        "OBJECTIVE_PLAN_REMOVED",
    ):
        assert hasattr(EventType, name)
        assert getattr(EventType, name).value in EVENT_TYPE_VALUES
