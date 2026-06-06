"""Pure DCR (Document Change Request) domain logic (no I/O) — the lifecycle FSM (S-dcr-1)."""

from __future__ import annotations

from .fsm import DCR_TRANSITIONS, allowed_targets, is_terminal, transition_allowed

__all__ = [
    "DCR_TRANSITIONS",
    "allowed_targets",
    "is_terminal",
    "transition_allowed",
]
