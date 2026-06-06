"""Pure DCR (Document Change Request) domain logic (no I/O) — the lifecycle FSM (S-dcr-1)."""

from __future__ import annotations

from .fsm import DCR_TRANSITIONS, allowed_targets, is_terminal, transition_allowed
from .obsoletion import ObsoletionReason, ObsoletionSafety, evaluate_obsoletion
from .where_used import bucket_links

__all__ = [
    "DCR_TRANSITIONS",
    "ObsoletionReason",
    "ObsoletionSafety",
    "allowed_targets",
    "bucket_links",
    "evaluate_obsoletion",
    "is_terminal",
    "transition_allowed",
]
