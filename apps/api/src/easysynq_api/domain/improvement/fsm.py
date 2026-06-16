"""The pure Improvement-Initiative ``stage`` lifecycle FSM (slice S-improvement-1; doc 02 Cl 10.3,
doc 14 §9, decisions-register R46).

No I/O — fully unit-testable in isolation (the ``domain.dcr.fsm`` / ``domain.capa.fsm`` precedent).
The service layer loads the initiative ``FOR UPDATE``, calls :func:`transition_allowed`, appends the
``improvement_initiative_stage_event``, flips ``stage`` (+ sets ``closed_at`` on a terminal move),
and audits the move in one transaction.

Canonical lifecycle (R46 §F2 — the **simple stage-completion close**, unsigned)::

    Open        → {InProgress, Cancelled}
    InProgress  → {Completed, Cancelled}
    Completed   → {Closed}
    Closed      → {}            # terminal
    Cancelled   → {}            # terminal

- **Genesis** = ``Open`` (a ``stage_event`` with ``from_state=NULL, to_state=Open`` written at the
  raise/create).
- **``Cancelled``** is reachable only from the pre-completion states ``{Open, InProgress}`` (the DCR
  "Cancelled only from pre-approval states" posture) — a ``Completed`` initiative is ``Closed``,
  never cancelled.
- **``Closed``** is the terminal "filed" state; its transition ``payload`` MAY carry a free-text
  realized-benefit / outcome note — the lightweight 10.3 continual-improvement evidence, frozen into
  the sealed (REVOKE-immutable) ``stage_event``. This is not a signed gate and not recomputed.
- **Unsigned** in v1.x: clause 10.3 mandates no per-initiative sign-off; ``SignatureMeaning`` stays
  closed (R2). No not-effective loop (that is CAPA's 10.2 job).
"""

from __future__ import annotations

from ...db.models._improvement_enums import ImprovementStage

_S = ImprovementStage

# current → the set of legal next states (R46 §4). Closed / Cancelled are terminal (empty set).
IMPROVEMENT_TRANSITIONS: dict[ImprovementStage, frozenset[ImprovementStage]] = {
    _S.Open: frozenset({_S.InProgress, _S.Cancelled}),
    _S.InProgress: frozenset({_S.Completed, _S.Cancelled}),
    # A Completed initiative is filed (Closed), never cancelled (past the cancel window).
    _S.Completed: frozenset({_S.Closed}),
    _S.Closed: frozenset(),
    _S.Cancelled: frozenset(),
}


def allowed_targets(current: ImprovementStage) -> frozenset[ImprovementStage]:
    """The set of legal next states after ``current`` (empty when ``current`` is terminal)."""
    return IMPROVEMENT_TRANSITIONS.get(current, frozenset())


def transition_allowed(current: ImprovementStage, target: ImprovementStage) -> bool:
    """True iff ``current → target`` is a legal improvement-initiative lifecycle step."""
    return target in allowed_targets(current)


def is_terminal(state: ImprovementStage) -> bool:
    """True iff ``state`` is terminal (Closed / Cancelled) — no outgoing transitions."""
    return not IMPROVEMENT_TRANSITIONS.get(state, frozenset())
