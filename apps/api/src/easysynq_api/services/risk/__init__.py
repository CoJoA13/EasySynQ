"""Risk & Opportunity register service (clause 6.1, S-risk-1)."""

from __future__ import annotations

from .lifecycle import publish_register, start_register_revision
from .queries import governing_register
from .service import (
    add_risk_row,
    find_head,
    get_risk,
    list_risks,
    resolve_or_create_head,
    update_risk_row,
)

__all__ = [
    "add_risk_row",
    "find_head",
    "get_risk",
    "governing_register",
    "list_risks",
    "publish_register",
    "resolve_or_create_head",
    "start_register_revision",
    "update_risk_row",
]
