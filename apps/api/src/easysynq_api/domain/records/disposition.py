"""The pure disposition state-machine transition table (slice S-rec-2, doc 06 §5.3).

The allowed ``RecordDispositionState`` transitions, verbatim from the doc 06 §5.3 diagram:

    ACTIVE          → DUE_FOR_REVIEW   (retention reached; or a manual early review)
    DUE_FOR_REVIEW  → DISPOSED         (disposition approved & executed)
    DUE_FOR_REVIEW  → ACTIVE           (retention extended / re-anchored)
    ACTIVE          → ON_HOLD          (legal_hold set)
    DUE_FOR_REVIEW  → ON_HOLD          (legal_hold set)
    ON_HOLD         → ACTIVE           (hold released — the next sweep re-evaluates expiry)

``DISPOSED`` is terminal. Legal-hold place/release drive the ON_HOLD edges via the dedicated service
functions; the ``PATCH /records/{id}/disposition`` verb drives the ACTIVE↔DUE_FOR_REVIEW↔DISPOSED
edges. This module is pure (no DB/IO) so the legality table is unit-testable in isolation.
"""

from __future__ import annotations

import uuid

from easysynq_api.db.models._record_enums import RecordDispositionState as S

_ALLOWED: frozenset[tuple[S, S]] = frozenset(
    {
        (S.ACTIVE, S.DUE_FOR_REVIEW),
        (S.DUE_FOR_REVIEW, S.DISPOSED),
        (S.DUE_FOR_REVIEW, S.ACTIVE),
        (S.ACTIVE, S.ON_HOLD),
        (S.DUE_FOR_REVIEW, S.ON_HOLD),
        (S.ON_HOLD, S.ACTIVE),
    }
)


def legal_disposition_transition(
    from_state: RecordDispositionState, to_state: RecordDispositionState
) -> bool:
    """``True`` iff ``from_state → to_state`` is an allowed disposition transition (doc 06 §5.3)."""
    return (from_state, to_state) in _ALLOWED


def self_disposition_blocked(
    actor_id: uuid.UUID, captured_by: uuid.UUID, *, allow_self_disposition: bool
) -> bool:
    """SoD-6 (creator≠disposer, doc 07 §7): ``True`` iff the actor executing a record's disposition
    is the record's own capturer AND the org has NOT relaxed the rule. Pure (no DB) so it is
    unit-testable; the caller restricts it to the human DISPOSED edge (it does NOT gate
    DUE_FOR_REVIEW / ACTIVE re-anchor, nor the Beat sweep's system disposals, nor the R27
    legal-order hatch — which enforces the stronger dual-control requester != approver). Overridable
    per the SoD-2/4/5 small-org class: ``allow_self_disposition`` defaults OFF (strict)."""
    return (not allow_self_disposition) and actor_id == captured_by


# Re-export the enum alias name used in the signature above (keeps the table terse with ``S``).
RecordDispositionState = S
