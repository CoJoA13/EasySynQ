"""Pure clause-4.2 interested-parties register summary projection (S-interested-parties-2) — a
GOVERNING register snapshot → JSON-safe summary dict, the read-of-record for the controlled
consumers.

``summarize_register`` projects the frozen ``{rows}`` snapshot (``build_register``, resolved via
``services/interested_parties/queries.governing_register``) into the counts the
``GET /interested-parties/summary`` read (the future Home/dashboard tile + the Interested-Parties
SPA, S-interested-parties-fe) serves and the Management-Review 9.3.2(b) input freezes into the WORM
minutes (the 4.2 half, alongside the 4.1 context summary). It is **pure** (no I/O): the caller
resolves ``governing`` and passes the snapshot dict, so the same projection serves every consumer.
``None`` (pre-first-release — no published register) is the CALLER's concern (a ``published:false``
default for the endpoint), never this helper's.

Like the context register and unlike risk, clause 4.2 has **no computed/graded axis**
(``party_type`` / ``influence`` / ``status`` are categorical user inputs, not a derived band) — so
there is no ``criteria`` resolve, the projection is purely categorical counts. Every leaf is a JSON
primitive (int) so the summary survives ``rfc8785.dumps`` if a consumer freezes it (the
``domain/mgmt_review/inputs`` / ``domain/risk/summary`` discipline)."""

from __future__ import annotations

from typing import Any

from easysynq_api.db.models._interested_party_enums import (
    InterestedPartyInfluence,
    InterestedPartyStatus,
    InterestedPartyType,
)

_UNSPECIFIED = "unspecified"


def summarize_register(register: dict[str, Any]) -> dict[str, Any]:
    """Project a GOVERNING interested-parties register snapshot (``{rows}``) →
    ``{total, by_party_type, by_influence, by_status, active, never_reviewed}``.

    - ``by_party_type`` tallies every ``InterestedPartyType`` value (customer/regulator/supplier/
      employee/owner/community/partner — the ISO 9001 clause-4.2 spine).
    - ``by_influence`` tallies every ``InterestedPartyInfluence`` value (the relevance axis)
      plus an ``unspecified`` bucket for rows with a NULL influence.
    - ``by_status`` tallies ``active`` vs ``closed``; ``active`` is the open-parties headline (the
      ``high_risk`` analogue).
    - ``never_reviewed`` is the count of rows (any status) with no ``last_reviewed_at`` — a
      clock-free freshness signal (a time-based "stale" version would need a policy threshold + now,
      deferred).

    The caller passes a non-``None`` snapshot (``None`` is the pre-first-release gap, handled at the
    call site as ``published:false``). An empty published register (``rows == []``) summarizes to
    all-zeros. Unknown enum values (impossible on a frozen-from-validated-enums row) fall out of the
    breakdowns but still count toward ``total`` — the ``domain/risk/summary`` defensive posture."""
    rows = register.get("rows", []) or []
    by_party_type: dict[str, int] = {t.value: 0 for t in InterestedPartyType}
    by_influence: dict[str, int] = {i.value: 0 for i in InterestedPartyInfluence}
    by_influence[_UNSPECIFIED] = 0
    by_status: dict[str, int] = {s.value: 0 for s in InterestedPartyStatus}
    never_reviewed = 0
    for row in rows:
        party_type = row.get("party_type")
        if party_type in by_party_type:
            by_party_type[party_type] += 1
        influence = row.get("influence")
        if influence is None:
            by_influence[_UNSPECIFIED] += 1
        elif influence in by_influence:
            by_influence[influence] += 1
        status = row.get("status")
        if status in by_status:
            by_status[status] += 1
        if row.get("last_reviewed_at") is None:
            never_reviewed += 1
    return {
        "total": len(rows),
        "by_party_type": by_party_type,
        "by_influence": by_influence,
        "by_status": by_status,
        "active": by_status[InterestedPartyStatus.active.value],
        "never_reviewed": never_reviewed,
    }
