"""S-doc-filters unit proofs — the GET /documents filter grammar (no DB).

The two new opt-in boolean filters (`has_effective_version`, `managed_subtype`) added for the DCR
CREATE-implement picker. These exercise the pure grammar helpers directly (mirroring the integration
suite's HTTP-level grammar tests in tests/integration/test_documents_list.py) — they need no DB or
app fixture, so they run natively on this Windows box.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import ColumnElement

from easysynq_api.api.documents import (
    _filter_condition,
    _parse_document_filters,
    _parse_filter_bool,
)
from easysynq_api.problems import ProblemException


def _fake_request(*pairs: tuple[str, str]) -> SimpleNamespace:
    """A minimal stand-in for starlette.Request exposing only what _parse_document_filters reads:
    ``request.query_params.multi_items()`` → an iterable of (raw_key, value) tuples."""
    return SimpleNamespace(query_params=SimpleNamespace(multi_items=lambda: list(pairs)))


# --- _parse_filter_bool -------------------------------------------------------------------


def test_parse_filter_bool_true() -> None:
    assert _parse_filter_bool("has_effective_version", "true") is True


def test_parse_filter_bool_false() -> None:
    assert _parse_filter_bool("has_effective_version", "false") is False


@pytest.mark.parametrize("field", ["has_effective_version", "managed_subtype"])
def test_parse_filter_bool_rejects_non_boolean(field: str) -> None:
    with pytest.raises(ProblemException) as exc:
        _parse_filter_bool(field, "banana")
    assert exc.value.status == 422
    assert exc.value.code == "validation_error"
    assert field in exc.value.title


# --- _filter_condition: the two new boolean filters ---------------------------------------


@pytest.mark.parametrize("field", ["has_effective_version", "managed_subtype"])
def test_filter_condition_bad_boolean_value_422(field: str) -> None:
    with pytest.raises(ProblemException) as exc:
        _filter_condition(field, "eq", "banana")
    assert exc.value.status == 422
    assert exc.value.code == "validation_error"


@pytest.mark.parametrize("field", ["has_effective_version", "managed_subtype"])
@pytest.mark.parametrize("value", ["true", "false"])
def test_filter_condition_boolean_returns_column_element(field: str, value: str) -> None:
    cond = _filter_condition(field, "eq", value)
    assert isinstance(cond, ColumnElement)


# --- _parse_document_filters: grammar routing ---------------------------------------------


def test_parse_document_filters_accepts_new_pairs() -> None:
    conds = _parse_document_filters(
        _fake_request(
            ("filter[has_effective_version][eq]", "false"),
            ("filter[managed_subtype][eq]", "false"),
        )
    )
    assert len(conds) == 2
    assert all(isinstance(c, ColumnElement) for c in conds)


def test_parse_document_filters_ignores_non_filter_params() -> None:
    # limit/offset and other non-filter[...] params are passed through untouched (no condition).
    conds = _parse_document_filters(
        _fake_request(
            ("limit", "100"),
            ("filter[has_effective_version][eq]", "true"),
        )
    )
    assert len(conds) == 1


def test_parse_document_filters_unknown_filter_400() -> None:
    with pytest.raises(ProblemException) as exc:
        _parse_document_filters(_fake_request(("filter[foo][eq]", "x")))
    assert exc.value.status == 400
    assert exc.value.code == "unknown_filter"


def test_parse_document_filters_known_field_unknown_op_400() -> None:
    # has_effective_version is allow-listed only for op=eq; another op must 400 unknown_filter.
    with pytest.raises(ProblemException) as exc:
        _parse_document_filters(_fake_request(("filter[has_effective_version][gte]", "true")))
    assert exc.value.status == 400
    assert exc.value.code == "unknown_filter"
