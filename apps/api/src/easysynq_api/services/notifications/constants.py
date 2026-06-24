"""Canonical notification event keys + per-event variable whitelists (TEXT, not PG enums — spec §3.1
so new events need no migration). The renderer only substitutes whitelisted slots."""

from __future__ import annotations

EVENT_TASK_ASSIGNED = "task.assigned"
EVENT_TASK_DUE_SOON = "task.due_soon"
EVENT_TASK_OVERDUE = "task.overdue"
EVENT_TASK_ESCALATED = "task.escalated"
EVENT_EMAIL_DELIVERY_FAILED = "system.email_delivery_failed"
EVENT_DIGEST_DAILY = "digest.daily"
EVENT_DOC_RELEASED = "doc.released"

SUBJECT_SYSTEM = "SYSTEM"

# Shared variable set for all task-lifecycle events (assigned / due-soon / overdue / escalated).
_TASK_EVENT_VARS: frozenset[str] = frozenset(
    {
        "recipient.first_name",
        "subject.identifier",
        "subject.title",
        "subject.kind",
        "task.action_expected",
        "task.due_at",
        "deep_link",
        "prefs_link",
    }
)

# Per-event allowed template variables. system.email_delivery_failed is OPERATIONAL-ONLY — it MUST
# NOT carry subject.title/identifier (admins hold no document.read; spec §5/§6, refute L3-1).
VARIABLE_WHITELIST: dict[str, frozenset[str]] = {
    EVENT_TASK_ASSIGNED: _TASK_EVENT_VARS,
    EVENT_TASK_DUE_SOON: _TASK_EVENT_VARS,
    EVENT_TASK_OVERDUE: _TASK_EVENT_VARS,
    EVENT_TASK_ESCALATED: _TASK_EVENT_VARS,
    EVENT_EMAIL_DELIVERY_FAILED: frozenset(
        {"recipient_email", "attempts", "last_error", "notification_id", "created_at"}
    ),
    EVENT_DIGEST_DAILY: frozenset({"recipient.first_name", "item_count", "items", "prefs_link"}),
    EVENT_DOC_RELEASED: frozenset(
        {
            "recipient.first_name",
            "subject.identifier",
            "subject.title",
            "subject.kind",
            "version.label",
            "deep_link",
            "prefs_link",
        }
    ),
}
