# apps/api/tests/unit/test_context_summary.py
"""Pure clause-4.1 context register summary projection (S-context-2) — ``summarize_register`` over a
frozen GOVERNING snapshot.

The summary is the controlled read-of-record the ``GET /context/summary`` read serves (and a future
MR 9.3.2(b) input would freeze into the WORM minutes). Unlike risk, clause 4.1 has NO graded axis —
the projection is purely categorical counts (classification / SWOT category / status) + an
open-issues ``active`` headline + a clock-free ``never_reviewed`` freshness signal. Every count is a
plain int so the dict survives ``rfc8785.dumps`` if a consumer freezes it."""

from __future__ import annotations

from typing import Any

from easysynq_api.db.models._context_enums import (
    CONTEXT_CATEGORY_VALUES,
    CONTEXT_CLASSIFICATION_VALUES,
    CONTEXT_ISSUE_STATUS_VALUES,
)
from easysynq_api.domain.context.register_content import build_register
from easysynq_api.domain.context.summary import summarize_register


def _row(
    rid: str,
    *,
    classification: str = "internal",
    category: str | None = None,
    status: str = "active",
    last_reviewed_at: str | None = None,
) -> dict[str, Any]:
    """A frozen-row dict in the ``services/context/lifecycle._frozen_row`` shape (only the
    summary-relevant leaves are load-bearing)."""
    return {
        "id": rid,
        "classification": classification,
        "category": category,
        "status": status,
        "description": "d",
        "last_reviewed_at": last_reviewed_at,
        "row_version": 1,
    }


def _register(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A governing snapshot ({rows}) frozen via the real ``build_register`` (rows-only — clause 4.1
    has no scoring criteria; the publish-freeze shape)."""
    return build_register(rows=rows)


def test_empty_published_register_is_all_zeros() -> None:
    out = summarize_register(_register([]))
    assert out["total"] == 0
    assert out["active"] == 0
    assert out["never_reviewed"] == 0
    assert set(out["by_classification"]) == set(CONTEXT_CLASSIFICATION_VALUES)
    assert set(out["by_category"]) == set(CONTEXT_CATEGORY_VALUES) | {"uncategorized"}
    assert set(out["by_status"]) == set(CONTEXT_ISSUE_STATUS_VALUES)
    assert all(v == 0 for v in out["by_classification"].values())
    assert all(v == 0 for v in out["by_category"].values())
    assert all(v == 0 for v in out["by_status"].values())


def test_counts_by_classification() -> None:
    out = summarize_register(
        _register(
            [
                _row("a", classification="internal"),
                _row("b", classification="internal"),
                _row("c", classification="external"),
            ]
        )
    )
    assert out["total"] == 3
    assert out["by_classification"] == {"internal": 2, "external": 1}


def test_counts_by_category_with_uncategorized_for_null() -> None:
    out = summarize_register(
        _register(
            [
                _row("a", category="strength"),
                _row("b", category="weakness"),
                _row("c", category="opportunity"),
                _row("d", category="threat"),
                _row("e", category=None),  # NULL category → uncategorized
                _row("f", category=None),
            ]
        )
    )
    assert out["by_category"] == {
        "strength": 1,
        "weakness": 1,
        "opportunity": 1,
        "threat": 1,
        "uncategorized": 2,
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
    # a frozen-from-validated-enums row can't actually carry this, but the projection is defensive
    # (the domain/risk/summary posture): an unrecognised classification is dropped from the
    # breakdown yet still tallied in total.
    out = summarize_register(_register([_row("a", classification="cosmic")]))
    assert out["total"] == 1
    assert out["by_classification"] == {"internal": 0, "external": 0}
