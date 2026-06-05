"""The pure internal-audit lifecycle FSM (slice S-aud-1; doc 10 §5.1, doc 14 §14).

A linear, forward-only chain — no I/O, fully unit-testable in isolation (the lifecycle.py /
disposition.py domain-function precedent). The service layer loads the audit ``FOR UPDATE``, calls
:func:`transition_allowed`, runs the Closing→Closed gate (a no-op until S-aud-2 adds findings), then
persists + audits the move in one transaction.
"""

from __future__ import annotations

from ...db.models._iso_audit_enums import AuditState

# current → the single legal next state (doc 14 §14 order). Closed is terminal (absent as a key).
AUDIT_TRANSITIONS: dict[AuditState, AuditState] = {
    AuditState.Scheduled: AuditState.Planned,
    AuditState.Planned: AuditState.InProgress,
    AuditState.InProgress: AuditState.FindingsDraft,
    AuditState.FindingsDraft: AuditState.Reported,
    AuditState.Reported: AuditState.Closing,
    AuditState.Closing: AuditState.Closed,
}


def next_state(current: AuditState) -> AuditState | None:
    """The single legal forward state after ``current``, or ``None`` if ``current`` is terminal."""
    return AUDIT_TRANSITIONS.get(current)


def transition_allowed(current: AuditState, target: AuditState) -> bool:
    """True iff ``current → target`` is the one legal forward step (no skips, no rewind)."""
    return AUDIT_TRANSITIONS.get(current) is target
