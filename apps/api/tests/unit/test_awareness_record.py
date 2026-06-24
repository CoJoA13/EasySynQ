import pytest
from sqlalchemy.exc import DBAPIError

from easysynq_api.services.notifications.awareness import _is_serialization_error


class _Orig:
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        self.pgcode = sqlstate


@pytest.mark.unit
@pytest.mark.parametrize("code", ["40001", "40P01", "23505"])
def test_is_serialization_error_true_for_retryable_states(code: str) -> None:
    exc = DBAPIError("stmt", {}, _Orig(code))
    assert _is_serialization_error(exc) is True


@pytest.mark.unit
def test_is_serialization_error_false_for_other_states() -> None:
    exc = DBAPIError("stmt", {}, _Orig("42703"))  # undefined_column
    assert _is_serialization_error(exc) is False
