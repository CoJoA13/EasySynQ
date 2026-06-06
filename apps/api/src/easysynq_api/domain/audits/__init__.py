"""Pure audit-family domain logic (no I/O) — the lifecycle FSM (S-aud-1) + the finding close-gate
predicate (S-aud-2)."""

from .findings import finding_blocks_close
from .fsm import AUDIT_TRANSITIONS, next_state, transition_allowed

__all__ = ["AUDIT_TRANSITIONS", "finding_blocks_close", "next_state", "transition_allowed"]
