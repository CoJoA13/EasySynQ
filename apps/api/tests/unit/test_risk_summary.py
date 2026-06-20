# apps/api/tests/unit/test_risk_summary.py
"""Pure clause-6.1 register summary projection (S-risk-2) — ``summarize_register`` over a frozen
GOVERNING snapshot.

The summary is the controlled read-of-record the MR input-(e) compiler freezes into the WORM minutes
and the doc-13 high-risk dashboard reads. Each row's band grades against the snapshot's FROZEN
per-method criteria (``resolve_criteria``), never live code — so the L2 derive-and-freeze guarantee
holds at the summary level too (a custom frozen criteria re-bands without any code change)."""

from __future__ import annotations

from typing import Any

from easysynq_api.db.models._risk_enums import ScoringMethod
from easysynq_api.domain.risk.register_content import build_register, criteria_for_methods
from easysynq_api.domain.risk.summary import summarize_register

_M5 = ScoringMethod.MATRIX_5X5


def _row(
    rid: str,
    rating: int,
    *,
    type: str = "risk",
    treatment: str | None = None,
    effectiveness: str | None = None,
    method: ScoringMethod = _M5,
) -> dict[str, Any]:
    """A frozen-row dict in the ``lifecycle._frozen_row`` shape (only the summary-relevant leaves
    are load-bearing; ``risk_rating`` is read directly, not re-derived here)."""
    return {
        "id": rid,
        "type": type,
        "description": "d",
        "process_id": None,
        "clause_id": None,
        "likelihood": 1,
        "severity": rating,
        "risk_rating": rating,
        "scoring_method": method.value,
        "treatment": treatment,
        "effectiveness": effectiveness,
        "row_version": 1,
    }


def _register(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A governing snapshot ({rows, criteria}) frozen via the real ``build_register`` + the v1
    default criteria for the 5x5 method (the publish-freeze shape)."""
    return build_register(rows=rows, criteria=criteria_for_methods({_M5}))


def test_empty_published_register_is_all_zeros() -> None:
    out = summarize_register(_register([]))
    assert out["total"] == 0
    assert out["high_risk"] == 0
    assert set(out["by_band"]) == {"critical", "high", "medium", "low", "unscored"}
    assert all(v == 0 for v in out["by_band"].values())
    assert out["by_type"] == {"risk": 0, "opportunity": 0}
    assert out["effectiveness"] == {"treated": 0, "recorded": 0, "pending": 0}


def test_counts_by_band_and_high_risk_danger_tone() -> None:
    # default criteria: critical ≥20, high ≥12, medium ≥6, low ≥1.
    rows = [
        _row("a", 20),  # critical
        _row("b", 16),  # high
        _row("c", 12),  # high
        _row("d", 10),  # medium
        _row("e", 6),  # medium
        _row("f", 5),  # low
        _row("g", 1),  # low
    ]
    out = summarize_register(_register(rows))
    assert out["total"] == 7
    assert out["by_band"] == {
        "critical": 1,
        "high": 2,
        "medium": 2,
        "low": 2,
        "unscored": 0,
    }
    # the doc-13 high-risk set is the danger-tone (Critical and High) count.
    assert out["high_risk"] == 3


def test_effectiveness_is_over_treated_rows() -> None:
    rows = [
        _row("a", 20, treatment="mitigate", effectiveness="verified"),  # treated + recorded
        _row("b", 12, treatment="plan"),  # treated, pending
        _row("c", 6, treatment="   "),  # whitespace treatment → NOT treated
        _row("d", 6, effectiveness="x"),  # effectiveness without a treatment → NOT treated
    ]
    out = summarize_register(_register(rows))
    assert out["effectiveness"] == {"treated": 2, "recorded": 1, "pending": 1}
    # recorded + pending == treated (the invariant the metric guarantees).
    eff = out["effectiveness"]
    assert eff["recorded"] + eff["pending"] == eff["treated"]


def test_by_type_tallies_risk_and_opportunity() -> None:
    rows = [
        _row("a", 20, type="risk"),
        _row("b", 12, type="opportunity"),
        _row("c", 6, type="opportunity"),
    ]
    out = summarize_register(_register(rows))
    assert out["by_type"] == {"risk": 1, "opportunity": 2}


def test_band_grades_against_frozen_criteria_not_live_code() -> None:
    # A snapshot whose FROZEN criteria differ from the v1 default (critical ≥10, not ≥20): a
    # rating-12 row bands CRITICAL here, yet would band HIGH under the live default — proving the
    # summary grades against the snapshot's frozen criteria, never a live module constant (R49 L2).
    frozen = {
        "rows": [_row("a", 12)],
        "criteria": {
            "5x5_matrix": {
                "method": "5x5_matrix",
                "max_rating": 25,
                "bands": [
                    {"band": "critical", "min": 10},
                    {"band": "high", "min": 6},
                    {"band": "medium", "min": 3},
                    {"band": "low", "min": 1},
                ],
            }
        },
    }
    out = summarize_register(frozen)
    assert out["by_band"]["critical"] == 1
    assert out["high_risk"] == 1


def test_missing_frozen_criteria_falls_back_to_default() -> None:
    # A row whose method has NO frozen criteria entry (an empty criteria map) → resolve_criteria
    # falls back to the v1 default, so a rating-20 row still bands critical (the forward-compat path
    # for a method minted in an open revision the prior snapshot never carried).
    out = summarize_register({"rows": [_row("a", 20)], "criteria": {}})
    assert out["by_band"]["critical"] == 1
    assert out["high_risk"] == 1
