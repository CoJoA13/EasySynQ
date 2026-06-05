"""The pure CAPA ``close_state`` lifecycle FSM (slice S-capa-1; doc 10 §6, doc 14 §14).

No I/O — fully unit-testable in isolation (the ``domain.audits.fsm`` / ``domain.vault.lifecycle``
precedent). The service layer loads the CAPA ``FOR UPDATE``, calls :func:`transition_allowed`,
appends the stage block, flips ``close_state``, and audits the move in one transaction.

Unlike the linear audit FSM, the CAPA lifecycle has two non-linear edges (doc 10 §6.1):
- ``Verify → ActionPlan`` — the effectiveness loop (re-plan when verification fails). The service
  bumps ``cycle_marker`` on this edge — wired in S-capa-3, not S-capa-1.
- ``… → Rejected`` — a CAPA can be rejected from any non-terminal working stage.

The full canonical map is defined here for forward-compat (the audit-FSM pattern: the whole chain is
declared, but S-capa-1's SERVICE only wires ``Raised → Containment`` via ``capa.update``; later
slices wire RootCause/ActionPlan/Implement/Verify/Close/Reject behind their own permission gates).
"""

from __future__ import annotations

from ...db.models._capa_enums import CapaCloseState

_S = CapaCloseState

# current → the set of legal next states (doc 10 §6.1). Closed / Rejected are terminal (empty set).
CAPA_TRANSITIONS: dict[CapaCloseState, frozenset[CapaCloseState]] = {
    _S.Raised: frozenset({_S.Containment, _S.Rejected}),
    _S.Containment: frozenset({_S.RootCause, _S.Rejected}),
    _S.RootCause: frozenset({_S.ActionPlan, _S.Rejected}),
    _S.ActionPlan: frozenset({_S.Implement, _S.Rejected}),
    _S.Implement: frozenset({_S.Verify, _S.Rejected}),
    # Verify → Closed (the M4 closure gate, S-capa-3) OR Verify → ActionPlan (the effectiveness
    # loop, bumps cycle_marker in S-capa-3).
    _S.Verify: frozenset({_S.Closed, _S.ActionPlan}),
    _S.Closed: frozenset(),
    _S.Rejected: frozenset(),
}


def allowed_targets(current: CapaCloseState) -> frozenset[CapaCloseState]:
    """The set of legal next states after ``current`` (empty when ``current`` is terminal)."""
    return CAPA_TRANSITIONS.get(current, frozenset())


def transition_allowed(current: CapaCloseState, target: CapaCloseState) -> bool:
    """True iff ``current → target`` is a legal CAPA lifecycle step."""
    return target in allowed_targets(current)


def is_terminal(state: CapaCloseState) -> bool:
    """True iff ``state`` is terminal (Closed or Rejected) — no outgoing transitions."""
    return not CAPA_TRANSITIONS.get(state, frozenset())
