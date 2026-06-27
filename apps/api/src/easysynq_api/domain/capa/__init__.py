"""Pure CAPA-family domain logic (no I/O) — the lifecycle FSM (S-capa-1) + the severity-aware SoD-4
predicate + the M4 closure gate (S-capa-3)."""

from __future__ import annotations

from .closure import (
    VERIFIER_DECISIONS,
    VERIFIER_EFFECTIVE,
    VERIFIER_NOT_EFFECTIVE,
    ClosureOutcome,
    adjudicate_capa_closure,
)
from .fsm import CAPA_TRANSITIONS, allowed_targets, is_terminal, transition_allowed
from .sod import capa_self_verify_blocked, derive_implementer_ids
from .targets import CAPA_TARGET_DAYS, default_target_date

__all__ = [
    "CAPA_TARGET_DAYS",
    "CAPA_TRANSITIONS",
    "VERIFIER_DECISIONS",
    "VERIFIER_EFFECTIVE",
    "VERIFIER_NOT_EFFECTIVE",
    "ClosureOutcome",
    "adjudicate_capa_closure",
    "allowed_targets",
    "capa_self_verify_blocked",
    "default_target_date",
    "derive_implementer_ids",
    "is_terminal",
    "transition_allowed",
]
