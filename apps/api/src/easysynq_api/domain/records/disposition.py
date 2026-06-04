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


# Re-export the enum alias name used in the signature above (keeps the table terse with ``S``).
RecordDispositionState = S
