"""Pure vault domain helpers — identifier/revision-label formatting + the lifecycle FSM (no I/O)."""

from .identifier import format_identifier, revision_label
from .lifecycle import Action, IllegalTransition, Transition, allowed_actions, apply_transition

__all__ = [
    "Action",
    "IllegalTransition",
    "Transition",
    "allowed_actions",
    "apply_transition",
    "format_identifier",
    "revision_label",
]
