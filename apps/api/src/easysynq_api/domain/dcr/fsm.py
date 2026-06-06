"""The pure DCR ``state`` lifecycle FSM (slice S-dcr-1; doc 05 §5.5, doc 15 §8.7, doc 14 §7).

No I/O — fully unit-testable in isolation (the ``domain.capa.fsm`` precedent). The service layer
loads the DCR ``FOR UPDATE``, calls :func:`transition_allowed`, appends the ``dcr_stage_event``,
flips ``state``, and audits the move in one transaction.

Canonical lifecycle ``Open → Assessed → Routed → InApproval → Approved → Implemented → Closed``
with terminal ``Cancelled``/``Rejected`` (doc 14 §7, R22). Two reconciliation calls baked in:
- **InApproval changes-requested loops to ``Open``** (re-assess + re-route), per doc 15 §8.7's
  state diagram and the owner's decision — reconciling doc 05 §5.5's ``Routed`` (a substantively
  changed draft re-derives its impact assessment + approver routing; recorded in
  decisions-register).
- **``Cancelled`` is reachable only while not yet approved** — from ``{Open, Assessed, Routed}``
  (doc 15 POST /dcrs/{id}/cancel "while not implemented"); there is **no** ``Approved →
  Cancelled`` edge (past the cancel window). ``InApproval`` exits via ``Rejected`` (decline) or
  the changes-requested loop, not Cancel.

The full map is declared here for forward-compat (the CAPA-FSM pattern); S-dcr-1's SERVICE wires
only the ``Open`` intake (genesis) + ``Open → Cancelled``. Later slices wire Assessed (S-dcr-2),
Routed/InApproval (S-dcr-4), Approved/Implemented/Closed (S-dcr-5) behind their own permission
gates.
"""

from __future__ import annotations

from ...db.models._dcr_enums import DcrState

_S = DcrState

# current → the set of legal next states (doc 05 §5.5 + doc 15 §8.7). Closed / Cancelled /
# Rejected are terminal (empty set).
DCR_TRANSITIONS: dict[DcrState, frozenset[DcrState]] = {
    _S.Open: frozenset({_S.Assessed, _S.Cancelled}),
    _S.Assessed: frozenset({_S.Routed, _S.Cancelled}),
    _S.Routed: frozenset({_S.InApproval, _S.Cancelled}),
    # Approved (all required approvals signed) OR Rejected (declined) OR Open (changes-requested
    # loop: re-assess + re-route per doc 15 §8.7 / owner decision).
    _S.InApproval: frozenset({_S.Approved, _S.Rejected, _S.Open}),
    # No Approved → Cancelled (past the cancel window; both docs agree the only out-edge is
    # Implemented).
    _S.Approved: frozenset({_S.Implemented}),
    _S.Implemented: frozenset({_S.Closed}),
    _S.Closed: frozenset(),
    _S.Cancelled: frozenset(),
    _S.Rejected: frozenset(),
}


def allowed_targets(current: DcrState) -> frozenset[DcrState]:
    """The set of legal next states after ``current`` (empty when ``current`` is terminal)."""
    return DCR_TRANSITIONS.get(current, frozenset())


def transition_allowed(current: DcrState, target: DcrState) -> bool:
    """True iff ``current → target`` is a legal DCR lifecycle step."""
    return target in allowed_targets(current)


def is_terminal(state: DcrState) -> bool:
    """True iff ``state`` is terminal (Closed / Cancelled / Rejected) — no outgoing transitions."""
    return not DCR_TRANSITIONS.get(state, frozenset())
