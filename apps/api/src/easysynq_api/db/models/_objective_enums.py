"""Objective-family enums (S-obj-1). ``create_type=False`` — the 0049 migration owns CREATE TYPE;
the migration sources its CREATE-TYPE tuple from ``OBJECTIVE_DIRECTION_VALUES`` (the 0010 rule)."""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ObjectiveDirection(enum.Enum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


OBJECTIVE_DIRECTION_VALUES = tuple(_vals(ObjectiveDirection))

objective_direction_enum = SAEnum(
    ObjectiveDirection,
    name="objective_direction",
    values_callable=_vals,
    create_type=False,
)
