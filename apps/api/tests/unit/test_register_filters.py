"""Unit edges of the register filter parser (services/common/register_filters).

The integration suite proves the important behaviour — that a row older than the scan window is
retrievable by narrowing. These cover the parser's edges cheaply.
"""

from __future__ import annotations

import datetime

import pytest
from starlette.datastructures import QueryParams

from easysynq_api.db.models._capa_enums import NcSeverity
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.problems import ProblemException
from easysynq_api.services.common.register_filters import (
    RegisterFilter,
    parse_date_boundary,
    parse_enum,
    parse_register_filters,
    parse_uuid,
)

ALLOWED = {
    "severity": RegisterFilter(
        column=Capa.severity, ops=frozenset({"eq"}), parse=parse_enum(NcSeverity)
    ),
    "process_id": RegisterFilter(column=Capa.process_id, ops=frozenset({"eq"}), parse=parse_uuid),
    "created_at": RegisterFilter(
        column=DocumentedInformation.created_at,
        ops=frozenset({"gte", "lte"}),
        parse=parse_date_boundary,
    ),
}


class _Request:
    """The one attribute the parser touches."""

    def __init__(self, query: str) -> None:
        self.query_params = QueryParams(query)


def _parse(query: str) -> list[object]:
    return list(parse_register_filters(_Request(query), ALLOWED))  # type: ignore[arg-type]


def test_a_non_filter_parameter_is_ignored() -> None:
    """`limit`, `q` and friends share the query string and must pass straight through."""
    assert _parse("limit=50&q=widget") == []


def test_repeated_keys_are_all_applied() -> None:
    """A range needs both bounds; dropping one would silently widen the window."""
    assert len(_parse("filter[created_at][gte]=2026-01-01&filter[created_at][lte]=2026-02-01")) == 2


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ProblemException) as excinfo:
        _parse("filter[nonsense][eq]=x")
    assert excinfo.value.code == "unknown_filter"


def test_an_operator_the_field_does_not_declare_is_refused() -> None:
    """`severity` accepts eq only — a gte would otherwise silently do nothing."""
    with pytest.raises(ProblemException) as excinfo:
        _parse("filter[severity][gte]=Minor")
    assert excinfo.value.code == "unknown_filter"


@pytest.mark.parametrize(
    "query",
    [
        "filter[severity][eq]=Nonexistent",
        "filter[process_id][eq]=not-a-uuid",
        "filter[created_at][gte]=not-a-date",
    ],
)
def test_an_unparseable_value_is_a_validation_error(query: str) -> None:
    with pytest.raises(ProblemException) as excinfo:
        _parse(query)
    assert excinfo.value.status == 422


def test_a_bare_date_becomes_utc_midnight() -> None:
    """A naive value compared against a timezone-aware column would raise inside the driver."""
    parsed = parse_date_boundary("2026-03-05")
    assert parsed == datetime.datetime(2026, 3, 5, tzinfo=datetime.UTC)
    assert parse_date_boundary("2026-03-05T08:30:00").tzinfo is datetime.UTC
