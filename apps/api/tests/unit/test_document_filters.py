"""S-web-2 unit proofs — the GET /documents filter builder (`_filter_condition`) is pure (no DB),
so its allow-listed (field, op) handling + the 422 rejections are unit-testable. The integration
behaviour (the SQL actually filtering rows) is proven in tests/integration/test_documents_list.py.
"""

from __future__ import annotations

import datetime
import zoneinfo

import pytest
from sqlalchemy import ColumnElement

from easysynq_api.api.documents import _filter_condition
from easysynq_api.problems import ProblemException
from easysynq_api.services.common.org_clock import using_org_tz
from easysynq_api.services.vault.document_filters import parse_effective_from_bound


def test_effective_from_gte_builds_a_condition() -> None:
    cond = _filter_condition("effective_from", "gte", "2026-01-01T00:00:00+00:00")
    assert isinstance(cond, ColumnElement)


def test_effective_from_lte_accepts_bare_date() -> None:
    # The client may send a bare ISO date (a relative bucket → organization calendar date).
    cond = _filter_condition("effective_from", "lte", "2026-06-01")
    assert isinstance(cond, ColumnElement)


def test_effective_from_bare_date_lte_includes_the_full_org_day() -> None:
    """A date-only upper bound is local end-of-day, not the day's starting midnight."""
    org_tz = zoneinfo.ZoneInfo("Pacific/Kiritimati")
    with using_org_tz(org_tz):
        cond = _filter_condition("effective_from", "lte", "2026-06-20")

    compiled = cond.compile()
    assert "document_version.effective_from <=" in str(compiled)
    assert (
        datetime.datetime(
            2026,
            6,
            20,
            23,
            59,
            59,
            999999,
            tzinfo=org_tz,
        )
        in compiled.params.values()
    )


def test_effective_from_date_time_lte_remains_an_inclusive_instant() -> None:
    bound = datetime.datetime(2026, 6, 20, 12, 30, tzinfo=datetime.UTC)
    cond = _filter_condition("effective_from", "lte", bound.isoformat())

    compiled = cond.compile()
    assert "document_version.effective_from <=" in str(compiled)
    assert bound in compiled.params.values()


def test_effective_from_bare_date_is_org_local_midnight() -> None:
    """UTC+14 makes the intended local date land on the prior UTC calendar date."""
    org_tz = zoneinfo.ZoneInfo("Pacific/Kiritimati")
    with using_org_tz(org_tz):
        bound = parse_effective_from_bound("2026-06-20")

    assert bound == datetime.datetime(2026, 6, 20, tzinfo=org_tz)
    assert bound.astimezone(datetime.UTC) == datetime.datetime(2026, 6, 19, 10, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "2026-06-20T00:00:00+09:00",
            datetime.datetime(2026, 6, 20, tzinfo=datetime.timezone(datetime.timedelta(hours=9))),
        ),
        (
            "2026-06-20T00:00:00",
            datetime.datetime(2026, 6, 20, tzinfo=datetime.UTC),
        ),
    ],
)
def test_effective_from_date_time_compatibility(value: str, expected: datetime.datetime) -> None:
    """Offset-aware instants stay intact; legacy offset-less date-times remain UTC."""
    with using_org_tz(zoneinfo.ZoneInfo("Pacific/Kiritimati")):
        assert parse_effective_from_bound(value) == expected


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


def test_clause_refs_rollup_pattern_is_dot_anchored_and_escaped() -> None:
    """S-clause-rollup: the subtree prefix is '.'-anchored (a bare LIKE '8%' would make clause 1
    match 10 — the handoff's named trap), the exact-number arm survives, and a user-supplied LIKE
    metacharacter is escaped rather than widening the match."""
    sql = str(
        _filter_condition("clause_refs", "has", "8").compile(compile_kwargs={"literal_binds": True})
    )
    assert "'8.' || '%'" in sql  # the dot rides INSIDE the literal prefix, before the wildcard
    assert "ESCAPE" in sql  # autoescape active
    assert "= '8'" in sql  # the exact arm survives

    hostile = str(
        _filter_condition("clause_refs", "has", "8%").compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    assert "'8/%.' || '%'" in hostile  # the user's % is escaped literal, not a wildcard
