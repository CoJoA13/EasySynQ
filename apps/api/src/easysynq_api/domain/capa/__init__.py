"""Pure CAPA-family domain logic (no I/O) — the lifecycle FSM (slice S-capa-1)."""

from __future__ import annotations

from .fsm import CAPA_TRANSITIONS, allowed_targets, is_terminal, transition_allowed

__all__ = ["CAPA_TRANSITIONS", "allowed_targets", "is_terminal", "transition_allowed"]
