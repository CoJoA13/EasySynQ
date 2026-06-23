"""Event-class taxonomy (spec §2, R54). A pure code map: event_key → class, plus the per-class
default email cadence. NULL preference columns resolve to default_mode(klass). The class set is
fixed in code for v1; a new event maps with one line below."""

from __future__ import annotations

import enum
import logging

from ...db.models._notification_enums import NotificationDigestMode

logger = logging.getLogger("easysynq.notifications.classes")


class NotificationClass(enum.Enum):
    ACTION_REQUIRED = "action_required"
    AWARENESS = "awareness"
    CRITICAL = "critical"
    ADMIN_OPS = "admin_ops"


_EVENT_CLASS: dict[str, NotificationClass] = {
    # action_required
    "task.assigned": NotificationClass.ACTION_REQUIRED,
    "task.due_soon": NotificationClass.ACTION_REQUIRED,
    "doc.review_requested": NotificationClass.ACTION_REQUIRED,
    "doc.changes_requested": NotificationClass.ACTION_REQUIRED,
    "review.due": NotificationClass.ACTION_REQUIRED,
    "finding.assigned": NotificationClass.ACTION_REQUIRED,
    "mr.input_requested": NotificationClass.ACTION_REQUIRED,
    "mr.scheduled": NotificationClass.ACTION_REQUIRED,
    "dcr.raised": NotificationClass.ACTION_REQUIRED,
    "dcr.accepted": NotificationClass.ACTION_REQUIRED,
    # awareness
    "doc.approved": NotificationClass.AWARENESS,
    "doc.released": NotificationClass.AWARENESS,
    "capa.stage_changed": NotificationClass.AWARENESS,
    "audit.scheduled": NotificationClass.AWARENESS,
    "audit.report_issued": NotificationClass.AWARENESS,
    "guest.access_expiring": NotificationClass.AWARENESS,
    # critical (pierce set)
    "task.overdue": NotificationClass.CRITICAL,
    "task.escalated": NotificationClass.CRITICAL,
    "capa.overdue": NotificationClass.CRITICAL,
    "integrity.alarm": NotificationClass.CRITICAL,
    # admin_ops
    "system.backup_failed": NotificationClass.ADMIN_OPS,
    "system.email_delivery_failed": NotificationClass.ADMIN_OPS,
}

_CLASS_DEFAULT_MODE: dict[NotificationClass, NotificationDigestMode] = {
    NotificationClass.ACTION_REQUIRED: NotificationDigestMode.DAILY,
    NotificationClass.AWARENESS: NotificationDigestMode.DAILY,
    NotificationClass.CRITICAL: NotificationDigestMode.IMMEDIATE,
    NotificationClass.ADMIN_OPS: NotificationDigestMode.IMMEDIATE,
}


def class_of(event_key: str) -> NotificationClass:
    klass = _EVENT_CLASS.get(event_key)
    if klass is None:
        logger.warning("notification.unknown_event_class", extra={"event_key": event_key})
        return NotificationClass.ACTION_REQUIRED
    return klass


def default_mode(klass: NotificationClass) -> NotificationDigestMode:
    return _CLASS_DEFAULT_MODE[klass]
