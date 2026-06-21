# apps/api/tests/unit/test_interested_party_summary.py
"""Pure clause-4.2 interested-parties register summary projection (S-interested-parties-2) —
``summarize_register`` over a frozen GOVERNING snapshot.

The summary is the read-of-record the ``GET /interested-parties/summary`` read serves (and
the MR 9.3.2(b) input freezes into the WORM minutes, as the 4.2 half). Like the context register and
unlike risk, clause 4.2 has NO graded axis — the projection is purely categorical counts (party_type
spine / influence axis / status) + an open-parties ``active`` headline + a clock-free
``never_reviewed`` freshness signal. Every count is a plain int so the dict survives
``rfc8785.dumps`` if a consumer freezes it."""

from __future__ import annotations

from typing import Any

from easysynq_api.db.models._interested_party_enums import (
    INTERESTED_PARTY_INFLUENCE_VALUES,
    INTERESTED_PARTY_STATUS_VALUES,
    INTERESTED_PARTY_TYPE_VALUES,
)
from easysynq_api.domain.interested_parties.register_content import build_register
from easysynq_api.domain.interested_parties.summary import summarize_register


def _row(
    rid: str,
    *,
    party_type: str = "customer",
    influence: str | None = None,
    status: str = "active",
    last_reviewed_at: str | None = None,
) -> dict[str, Any]:
    """A frozen-row dict in the ``lifecycle._frozen_row`` shape (only the summary-relevant leaves
    are load-bearing)."""
    return {
        "id": rid,
        "party_type": party_type,
        "party_name": "Acme",
        "needs_expectations": "x",
        "influence": influence,
        "status": status,
        "last_reviewed_at": last_reviewed_at,
        "row_version": 1,
    }


def _register(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A governing snapshot ({rows}) frozen via the real ``build_register`` (rows-only — clause 4.2
    has no scoring criteria; the publish-freeze shape)."""
    return build_register(rows=rows)


def test_empty_published_register_is_all_zeros() -> None:
    out = summarize_register(_register([]))
    assert out["total"] == 0
    assert out["active"] == 0
    assert out["never_reviewed"] == 0
    assert set(out["by_party_type"]) == set(INTERESTED_PARTY_TYPE_VALUES)
    assert set(out["by_influence"]) == set(INTERESTED_PARTY_INFLUENCE_VALUES) | {"unspecified"}
    assert set(out["by_status"]) == set(INTERESTED_PARTY_STATUS_VALUES)
    assert all(v == 0 for v in out["by_party_type"].values())
    assert all(v == 0 for v in out["by_influence"].values())
    assert all(v == 0 for v in out["by_status"].values())


def test_counts_by_party_type() -> None:
    out = summarize_register(
        _register(
            [
                _row("a", party_type="customer"),
                _row("b", party_type="customer"),
                _row("c", party_type="regulator"),
                _row("d", party_type="supplier"),
            ]
        )
    )
    assert out["total"] == 4
    assert out["by_party_type"]["customer"] == 2
    assert out["by_party_type"]["regulator"] == 1
    assert out["by_party_type"]["supplier"] == 1
    assert out["by_party_type"]["employee"] == 0


def test_counts_by_influence_with_unspecified_for_null() -> None:
    out = summarize_register(
        _register(
            [
                _row("a", influence="low"),
                _row("b", influence="medium"),
                _row("c", influence="high"),
                _row("d", influence=None),  # NULL influence → unspecified
                _row("e", influence=None),
            ]
        )
    )
    assert out["by_influence"] == {
        "low": 1,
        "medium": 1,
        "high": 1,
        "unspecified": 2,
    }


def test_by_status_and_active_headline() -> None:
    out = summarize_register(
        _register(
            [
                _row("a", status="active"),
                _row("b", status="active"),
                _row("c", status="active"),
                _row("d", status="closed"),
            ]
        )
    )
    assert out["by_status"] == {"active": 3, "closed": 1}
    # the headline equals the active count (the high_risk analogue).
    assert out["active"] == 3


def test_never_reviewed_counts_null_last_reviewed_over_all_rows() -> None:
    out = summarize_register(
        _register(
            [
                _row("a", last_reviewed_at=None),
                _row("b", last_reviewed_at=None),
                _row("c", last_reviewed_at="2026-01-01T00:00:00+00:00"),
                # a CLOSED, never-reviewed row still counts (never_reviewed is any-status).
                _row("d", status="closed", last_reviewed_at=None),
            ]
        )
    )
    assert out["total"] == 4
    assert out["never_reviewed"] == 3


def test_unknown_enum_value_falls_out_of_breakdown_but_counts_in_total() -> None:
    # a frozen-from-validated-enums row can't actually carry these, but the projection is defensive
    # (the domain/risk/summary posture): an unrecognised party_type / influence is dropped from the
    # breakdown yet still tallied in total. An unknown influence is NOT folded into unspecified
    # (only a literal NULL is) — it simply falls out.
    out = summarize_register(_register([_row("a", party_type="alien", influence="extreme")]))
    assert out["total"] == 1
    assert all(v == 0 for v in out["by_party_type"].values())
    assert out["by_influence"]["unspecified"] == 0
    assert all(v == 0 for v in out["by_influence"].values())
