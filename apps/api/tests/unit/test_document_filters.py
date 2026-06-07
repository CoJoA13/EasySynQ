"""S-web-2 unit proofs — the GET /documents filter builder (`_filter_condition`) is pure (no DB),
so its allow-listed (field, op) handling + the 422 rejections are unit-testable. The integration
behaviour (the SQL actually filtering rows) is proven in tests/integration/test_documents_list.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import ColumnElement

from easysynq_api.api.documents import _filter_condition
from easysynq_api.problems import ProblemException


def test_effective_from_gte_builds_a_condition() -> None:
    cond = _filter_condition("effective_from", "gte", "2026-01-01T00:00:00+00:00")
    assert isinstance(cond, ColumnElement)


def test_effective_from_lte_accepts_bare_date() -> None:
    # The client may send a bare ISO date (a relative bucket → date); it is treated as UTC midnight.
    cond = _filter_condition("effective_from", "lte", "2026-06-01")
    assert isinstance(cond, ColumnElement)


def test_effective_from_bad_value_422() -> None:
    with pytest.raises(ProblemException) as ei:
        _filter_condition("effective_from", "gte", "not-a-date")
    assert ei.value.status == 422
    assert ei.value.code == "validation_error"


def test_current_state_bad_value_422() -> None:
    with pytest.raises(ProblemException) as ei:
        _filter_condition("current_state", "eq", "Bogus")
    assert ei.value.status == 422


def test_classification_bad_value_422() -> None:
    with pytest.raises(ProblemException) as ei:
        _filter_condition("classification", "eq", "Bogus")
    assert ei.value.status == 422


def test_owner_user_id_bad_uuid_422() -> None:
    with pytest.raises(ProblemException) as ei:
        _filter_condition("owner_user_id", "eq", "not-a-uuid")
    assert ei.value.status == 422


def test_clause_refs_builds_a_condition() -> None:
    assert isinstance(_filter_condition("clause_refs", "has", "8.4"), ColumnElement)
