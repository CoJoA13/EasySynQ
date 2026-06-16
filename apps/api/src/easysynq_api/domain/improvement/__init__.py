"""Pure Improvement-Initiative domain logic (no I/O) — the clause-10.3 lifecycle FSM
(S-improvement-1)."""

from __future__ import annotations

from .fsm import IMPROVEMENT_TRANSITIONS, allowed_targets, is_terminal, transition_allowed

__all__ = [
    "IMPROVEMENT_TRANSITIONS",
    "allowed_targets",
    "is_terminal",
    "transition_allowed",
]
