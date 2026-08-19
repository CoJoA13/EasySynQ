"""Closed PostgreSQL enum bindings for R27 authority and execution state."""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class R27ActionKind(enum.Enum):
    REQUEST = "REQUEST"
    APPROVE = "APPROVE"
    CANCEL = "CANCEL"


class R27RequestState(enum.Enum):
    WAITING_FOR_SECOND_APPROVER = "WAITING_FOR_SECOND_APPROVER"
    WAITING_FOR_RECOVERY_GENERATION = "WAITING_FOR_RECOVERY_GENERATION"
    READY_FOR_FINALIZATION = "READY_FOR_FINALIZATION"
    FINALIZING = "FINALIZING"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"
    FAILED = "FAILED"


class R27ExecutionState(enum.Enum):
    CLAIMED = "CLAIMED"
    SOURCE_COMMITTED = "SOURCE_COMMITTED"
    PURGING = "PURGING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class R27ResultCode(enum.Enum):
    PHYSICAL_ERASED = "PHYSICAL_ERASED"
    LOGICAL_ONLY_SURVIVING_OWNER = "LOGICAL_ONLY_SURVIVING_OWNER"


class R27DerivativeKind(enum.Enum):
    PACK_ZIP = "PACK_ZIP"
    PACK_PORTFOLIO = "PACK_PORTFOLIO"
    PACK_RECORD = "PACK_RECORD"
    PACK_SHARE = "PACK_SHARE"


def _values(enum_type: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_type]


r27_action_kind_enum = SAEnum(
    R27ActionKind, name="r27_action_kind", values_callable=_values, create_type=False
)
r27_request_state_enum = SAEnum(
    R27RequestState, name="r27_request_state", values_callable=_values, create_type=False
)
r27_execution_state_enum = SAEnum(
    R27ExecutionState, name="r27_execution_state", values_callable=_values, create_type=False
)
r27_result_code_enum = SAEnum(
    R27ResultCode, name="r27_result_code", values_callable=_values, create_type=False
)
r27_derivative_kind_enum = SAEnum(
    R27DerivativeKind, name="r27_derivative_kind", values_callable=_values, create_type=False
)

R27_ENUM_VALUES = {
    "r27_action_kind": tuple(_values(R27ActionKind)),
    "r27_request_state": tuple(_values(R27RequestState)),
    "r27_execution_state": tuple(_values(R27ExecutionState)),
    "r27_result_code": tuple(_values(R27ResultCode)),
    "r27_derivative_kind": tuple(_values(R27DerivativeKind)),
}
