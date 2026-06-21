"""Pure clause-4.1 context register summary projection (S-context-2) — a GOVERNING register
snapshot → JSON-safe summary dict, the read-of-record for the controlled consumers.

``summarize_register`` projects the frozen ``{rows}`` snapshot (``build_register``, resolved via
``services/context/queries.governing_register``) into the counts the ``GET /context/summary`` read
(the future Home/dashboard tile + the Context SPA, S-context-fe) serves and any future
Management-Review 9.3.2(b) input would freeze into the WORM minutes. It is **pure** (no I/O): the
caller resolves ``governing`` and passes the snapshot dict, so the same projection serves every
consumer. ``None`` (pre-first-release — no published register) is the CALLER's concern (a
``published:false`` default for the endpoint), never this helper's.

Unlike the risk register, clause 4.1 has **no computed/graded axis** (``classification`` /
``category`` / ``status`` are categorical user inputs, not a derived band) — so there is no
``criteria`` resolve, the projection is purely categorical counts. Every leaf is a JSON primitive
(int) so the summary survives ``rfc8785.dumps`` if a consumer freezes it (the
``domain/mgmt_review/inputs`` / ``domain/risk/summary`` discipline)."""

from __future__ import annotations

from typing import Any

from easysynq_api.db.models._context_enums import (
    ContextCategory,
    ContextClassification,
    ContextIssueStatus,
)

_UNCATEGORIZED = "uncategorized"


def summarize_register(register: dict[str, Any]) -> dict[str, Any]:
    """Project a GOVERNING context register snapshot (``{rows}``) →
    ``{total, by_classification, by_category, by_status, active, never_reviewed}``.

    - ``by_classification`` tallies every ``ContextClassification`` value (internal/external — the
      ISO 9001 clause-4.1 spine).
    - ``by_category`` tallies every ``ContextCategory`` value (the optional SWOT framing) plus an
      ``uncategorized`` bucket for rows with a NULL category.
    - ``by_status`` tallies ``active`` vs ``closed``; ``active`` is the open-issues headline (the
      ``high_risk`` analogue).
    - ``never_reviewed`` is the count of rows (any status) with no ``last_reviewed_at`` — a
      clock-free freshness signal (a time-based "stale" version would need a policy threshold + now,
      deferred).

    The caller passes a non-``None`` snapshot (``None`` is the pre-first-release gap, handled at the
    call site as ``published:false``). An empty published register (``rows == []``) summarizes to
    all-zeros. Unknown enum values (impossible on a frozen-from-validated-enums row) fall out of the
    breakdowns but still count toward ``total`` — the ``domain/risk/summary`` defensive posture."""
    rows = register.get("rows", []) or []
    by_classification: dict[str, int] = {c.value: 0 for c in ContextClassification}
    by_category: dict[str, int] = {c.value: 0 for c in ContextCategory}
    by_category[_UNCATEGORIZED] = 0
    by_status: dict[str, int] = {s.value: 0 for s in ContextIssueStatus}
    never_reviewed = 0
    for row in rows:
        classification = row.get("classification")
        if classification in by_classification:
            by_classification[classification] += 1
        category = row.get("category")
        if category is None:
            by_category[_UNCATEGORIZED] += 1
        elif category in by_category:
            by_category[category] += 1
        status = row.get("status")
        if status in by_status:
            by_status[status] += 1
        if row.get("last_reviewed_at") is None:
            never_reviewed += 1
    return {
        "total": len(rows),
        "by_classification": by_classification,
        "by_category": by_category,
        "by_status": by_status,
        "active": by_status[ContextIssueStatus.active.value],
        "never_reviewed": never_reviewed,
    }
