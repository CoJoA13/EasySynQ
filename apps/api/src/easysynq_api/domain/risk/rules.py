"""Pure clause-6.1 risk scoring (S-risk-1, R18/R49) — no I/O, total, deterministic.

``risk_rating`` is ``likelihood x severity`` (STORED, re-derived on every write). The displayed BAND
is graded against a ``criteria`` dict the caller supplies — at the live read, the GOVERNING
version's
**frozen** criteria (``services/risk/queries`` via ``register_content.resolve_criteria``), never a
live module constant (R49 derive-and-freeze). ``default_criteria`` is the v1 code default, pinned by
``tests/unit/test_risk_rules.py`` so an in-place band-threshold edit fails CI — forcing the
mint-a-new-``scoring_method``-value path.
"""

from __future__ import annotations

import enum
from typing import Any

from easysynq_api.db.models._risk_enums import ScoringMethod


class RiskBand(enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    unscored = "unscored"  # forward-compat: a not-yet-scored row (v1 always derives a rating)


# Sort/RAG rank — danger-first (Critical & High share the danger tone; Critical sorts above High by
# the underlying risk_rating). Mirrors the objectives RAG_SEVERITY precedent.
BAND_RANK: dict[RiskBand, int] = {
    RiskBand.critical: 0,
    RiskBand.high: 1,
    RiskBand.medium: 2,
    RiskBand.low: 3,
    RiskBand.unscored: 4,
}

# Band → RAG tone (the objectives green/amber/red/unmeasured vocabulary; Critical+High = danger).
BAND_TONE: dict[RiskBand, str] = {
    RiskBand.critical: "danger",
    RiskBand.high: "danger",
    RiskBand.medium: "warning",
    RiskBand.low: "success",
    RiskBand.unscored: "neutral",
}


def risk_rating(likelihood: int, severity: int, scoring_method: ScoringMethod) -> int:
    """The stored numeric rating. v1: ``likelihood x severity`` for the 5x5 matrix (∈ 1..25)."""
    if scoring_method is ScoringMethod.MATRIX_5X5:
        if not (1 <= likelihood <= 5 and 1 <= severity <= 5):
            raise ValueError("likelihood and severity must each be in 1..5 for 5x5_matrix")
        return likelihood * severity
    raise ValueError(f"unknown scoring_method: {scoring_method!r}")


def default_criteria(scoring_method: ScoringMethod) -> dict[str, Any]:
    """The v1 code-default scoring criteria for a method — frozen into the version snapshot at
    publish
    and golden-pinned. The band boundaries use **lower thresholds** (a band claims every rating ≥
    its
    ``min``), so the function is TOTAL over 1..max_rating regardless of the unreachable 5x5 gaps
    (7/11/13/14/17-19/21-24). Bands are ordered descending by ``min`` (the read scans top-down)."""
    if scoring_method is ScoringMethod.MATRIX_5X5:
        return {
            "method": "5x5_matrix",
            "max_rating": 25,
            "bands": [
                {"band": "critical", "min": 20},
                {"band": "high", "min": 12},
                {"band": "medium", "min": 6},
                {"band": "low", "min": 1},
            ],
        }
    raise ValueError(f"unknown scoring_method: {scoring_method!r}")


def risk_band(rating: int, criteria: dict[str, Any]) -> RiskBand:
    """The RAG band for a stored rating, graded against the supplied (frozen) criteria. Total: a
    rating below the lowest band ``min`` (or ≤ 0) is ``unscored``."""
    for entry in criteria["bands"]:  # ordered descending by min
        if rating >= int(entry["min"]):
            return RiskBand(entry["band"])
    return RiskBand.unscored
