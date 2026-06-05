"""Pure audit-family domain logic (no I/O) — the lifecycle FSM (slice S-aud-1)."""

from .fsm import AUDIT_TRANSITIONS, next_state, transition_allowed

__all__ = ["AUDIT_TRANSITIONS", "next_state", "transition_allowed"]
