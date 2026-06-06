"""The pure CAPA M4 closure gate (slice S-capa-3; doc 10 §6.4, success metric M3).

The ``Verify → Closed`` guard, in ONE evaluation: **root_cause present AND ≥1 corrective action
implemented-with-evidence AND effectiveness_evidence present AND verifier decision = effective**
(doc 10 §6.4). The three branches:

- ``decision == not_effective`` → **LOOP**: the corrective action did not prevent recurrence, so the
  CAPA cycles back to RootCause (``cycle_marker++``) to re-propose + re-approve a revised plan.
- ``decision == effective`` AND all evidence clauses satisfied → **CLOSE** (Verify → Closed).
- ``decision == effective`` BUT an evidence clause is missing → **INCOMPLETE** (the QM links the
  missing evidence then re-closes; a forgotten link must NOT discard a recorded effective
  verification, so this is a 409, not the effectiveness loop).

Pure (no I/O), the ``self_disposition_blocked`` precedent: the service derives the three booleans
(cycle-scoping rules below) + the verifier decision, then calls :func:`adjudicate_capa_closure`.

**Cycle scope (deliberately asymmetric).** ``has_implemented_with_evidence`` and
``has_effectiveness_evidence`` are scoped to the CURRENT cycle (``capa.cycle_marker``): each loop
iteration stands on its own freshly-implemented + freshly-verified evidence. ``has_root_cause``
is **cycle-agnostic**: the loop lands back at RootCause carrying the established RCA forward (v1 has
no re-RCA path — the loop re-plans against the same root cause), so the root cause is a property of
the CAPA, not of a cycle. A cycle-1 close rests on the cycle-0 RootCause analysis.
"""

from __future__ import annotations

import enum

VERIFIER_EFFECTIVE = "effective"
VERIFIER_NOT_EFFECTIVE = "not_effective"
VERIFIER_DECISIONS = frozenset({VERIFIER_EFFECTIVE, VERIFIER_NOT_EFFECTIVE})


class ClosureOutcome(enum.Enum):
    CLOSE = "close"  # effective + all evidence present → Verify → Closed
    LOOP = "loop"  # not_effective → Verify → RootCause (cycle_marker++)
    INCOMPLETE = "incomplete"  # effective but an evidence clause is missing → 409 (no loop)


def adjudicate_capa_closure(
    *,
    decision: str,
    has_root_cause: bool,
    has_implemented_with_evidence: bool,
    has_effectiveness_evidence: bool,
) -> tuple[ClosureOutcome, list[str]]:
    """Adjudicate the M4 gate. Returns ``(outcome, missing_clauses)`` — ``missing_clauses`` is the
    list of unmet evidence clauses for an INCOMPLETE effective verification (empty otherwise). The
    caller guarantees ``decision ∈ VERIFIER_DECISIONS`` (the Verify stage stored a validated value);
    any non-``effective`` decision routes to the effectiveness loop.

    Reachability of the INCOMPLETE clauses at a real ``/close`` (close_state==Verify): root_cause
    is FSM-guaranteed (a RootCause stage was traversed to reach Verify), so that clause is a
    defensive backstop; ``implemented_action_with_evidence`` and ``effectiveness_evidence`` are
    genuinely reachable as False — the Implement / Verify stages exist but their evidence-for links
    are added separately + optionally, so a caller can reach close without them."""
    if decision != VERIFIER_EFFECTIVE:
        return ClosureOutcome.LOOP, []
    missing: list[str] = []
    if not has_root_cause:
        missing.append("root_cause")
    if not has_implemented_with_evidence:
        missing.append("implemented_action_with_evidence")
    if not has_effectiveness_evidence:
        missing.append("effectiveness_evidence")
    if missing:
        return ClosureOutcome.INCOMPLETE, missing
    return ClosureOutcome.CLOSE, []
