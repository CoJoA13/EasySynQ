"""Risk & Opportunity register service (clause 6.1, S-risk-1)."""

from __future__ import annotations

from .service import (
    add_risk_row,
    get_risk,
    list_risks,
    resolve_or_create_head,
    update_risk_row,
)

__all__ = [
    "add_risk_row",
    "get_risk",
    "list_risks",
    "resolve_or_create_head",
    "update_risk_row",
]
