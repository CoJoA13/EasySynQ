"""Pure clause-6.1 register summary projection (S-risk-2) — a GOVERNING register snapshot →
JSON-safe summary dict, the read-of-record for the controlled consumers.

``summarize_register`` projects the frozen ``{rows, criteria}`` snapshot (``build_register``,
resolved via ``services/risk/queries.governing_register``) into the counts the Management-Review
input-(e) compiler freezes into the WORM minutes and the doc-13 high-risk dashboard reads. It is
**pure** (no I/O): the caller resolves ``governing`` and passes the
snapshot dict, so the same projection serves the MR compiler and any future dashboard read.
``None`` (pre-first-release — no published register) is the CALLER's concern (a gap row in the MR
compiler; a ``published:false`` default for a dashboard), never this helper's.

Each frozen row's BAND grades against the GOVERNING version's per-method **frozen** criteria
(``resolve_criteria``), never a live module constant (R49 L2 derive-and-freeze) — so a code-level
band-threshold edit can never re-grade an already-published register's summary. Every leaf is a JSON
primitive (int/str/nested-dict-of-those) so the summary survives ``rfc8785.dumps`` at the minutes
freeze (a Decimal/UUID/datetime leaf would TypeError there — the ``domain/mgmt_review/inputs``
discipline).
"""

from __future__ import annotations

from typing import Any

from easysynq_api.db.models._risk_enums import RiskOpportunityType, ScoringMethod
from easysynq_api.domain.risk.register_content import resolve_criteria
from easysynq_api.domain.risk.rules import RiskBand, risk_band


def summarize_register(register: dict[str, Any]) -> dict[str, Any]:
    """Project a GOVERNING register snapshot (``{rows, criteria}``) →
    ``{total, by_band, high_risk, by_type, effectiveness}``.

    - ``by_band`` tallies every ``RiskBand`` value; ``high_risk`` is the ``danger``-tone
      (Critical and High) count — the doc-13 high-risk set (spec §4).
    - ``by_type`` tallies ``risk`` vs ``opportunity``.
    - ``effectiveness`` is over TREATED rows (a non-empty ``treatment`` — an action was taken):
      ``recorded`` = treated ∧ a non-empty ``effectiveness``; ``pending`` = treated ∧ no
      effectiveness (``recorded + pending == treated``) — the clause 9.3.2(e) "effectiveness of
      actions taken" metric.

    The caller passes a non-``None`` snapshot (``None`` is the pre-first-release gap, handled at the
    call site). An empty published register (``rows == []``) summarizes to all-zeros."""
    rows = register.get("rows", []) or []
    by_band: dict[str, int] = {band.value: 0 for band in RiskBand}
    by_type: dict[str, int] = {t.value: 0 for t in RiskOpportunityType}
    treated = 0
    recorded = 0
    for row in rows:
        method = ScoringMethod(row["scoring_method"])
        band = risk_band(int(row["risk_rating"]), resolve_criteria(register, method))
        by_band[band.value] += 1
        row_type = row.get("type")
        if row_type in by_type:
            by_type[row_type] += 1
        if (row.get("treatment") or "").strip():
            treated += 1
            if (row.get("effectiveness") or "").strip():
                recorded += 1
    return {
        "total": len(rows),
        "by_band": by_band,
        "high_risk": by_band[RiskBand.critical.value] + by_band[RiskBand.high.value],
        "by_type": by_type,
        "effectiveness": {
            "treated": treated,
            "recorded": recorded,
            "pending": treated - recorded,
        },
    }
