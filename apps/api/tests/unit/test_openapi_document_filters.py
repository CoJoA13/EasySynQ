"""Contract guards for the shared GET /documents filter grammar."""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml

_OPENAPI = pathlib.Path(__file__).resolve().parents[4] / "packages" / "contracts" / "openapi.yaml"
_FILTER_PATHS = ("/documents", "/reports/document-control")
_FILTER_NAMES = {
    "filter[clause_refs][has]",
    "filter[current_state][eq]",
    "filter[document_type][eq]",
    "filter[owner_user_id][eq]",
    "filter[classification][eq]",
    "filter[effective_from][gte]",
    "filter[effective_from][lte]",
    "filter[has_effective_version][eq]",
    "filter[managed_subtype][eq]",
    "filter[process_id][eq]",
}
_REPEATABLE_FILTERS = {"filter[clause_refs][has]"}


def _filter_parameters(path: str) -> dict[str, dict[str, Any]]:
    contract = yaml.safe_load(_OPENAPI.read_text(encoding="utf-8"))
    parameters = contract["paths"][path]["get"]["parameters"]
    return {
        parameter["name"]: parameter
        for parameter in parameters
        if parameter["name"].startswith("filter[")
    }


@pytest.mark.parametrize("path", _FILTER_PATHS)
def test_repeatable_filters_are_exploded_arrays_and_other_filters_are_scalar(path: str) -> None:
    """Generated clients must repeat the clause key while keeping single-value facets scalar."""
    parameters = _filter_parameters(path)
    assert set(parameters) == _FILTER_NAMES

    repeatable = {
        name for name, parameter in parameters.items() if parameter["schema"].get("type") == "array"
    }
    assert repeatable == _REPEATABLE_FILTERS

    clause_filter = parameters["filter[clause_refs][has]"]
    assert clause_filter["style"] == "form"
    assert clause_filter["explode"] is True
    assert clause_filter["schema"]["items"] == {"type": "string"}
    assert "logical AND" in clause_filter["description"]
    assert (
        "filter[clause_refs][has]=8.4&filter[clause_refs][has]=7.5.3"
        in clause_filter["description"]
    )


@pytest.mark.parametrize("path", _FILTER_PATHS)
def test_effective_date_bounds_publish_date_and_date_time_semantics(path: str) -> None:
    """Generated clients may send either an org-calendar date or an explicit instant."""
    parameters = _filter_parameters(path)
    expected_schema = {
        "oneOf": [
            {"type": "string", "format": "date"},
            {"type": "string", "format": "date-time"},
        ]
    }

    for name in ("filter[effective_from][gte]", "filter[effective_from][lte]"):
        parameter = parameters[name]
        assert parameter["schema"] == expected_schema
        assert "organization timezone" in parameter["description"]
        assert "midnight" in parameter["description"]


def test_shared_document_filter_serialization_shapes_match_on_both_endpoints() -> None:
    """The two endpoints share one parser, so their parameter shapes cannot drift."""
    documents = _filter_parameters("/documents")
    register = _filter_parameters("/reports/document-control")

    def serialization_shape(parameter: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": parameter["schema"],
            "style": parameter.get("style", "form"),
            "explode": parameter.get("explode"),
        }

    assert {name: serialization_shape(parameter) for name, parameter in documents.items()} == {
        name: serialization_shape(parameter) for name, parameter in register.items()
    }
