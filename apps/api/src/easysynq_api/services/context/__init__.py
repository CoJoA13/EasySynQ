"""Context register service (clause 4.1, S-context-1)."""

from __future__ import annotations

from .lifecycle import publish_register, start_context_revision
from .queries import governing_register
from .service import (
    add_context_issue,
    find_head,
    get_context_issue,
    list_context_issues,
    resolve_or_create_head,
    update_context_issue_row,
)

__all__ = [
    "add_context_issue",
    "find_head",
    "get_context_issue",
    "governing_register",
    "list_context_issues",
    "publish_register",
    "resolve_or_create_head",
    "start_context_revision",
    "update_context_issue_row",
]
