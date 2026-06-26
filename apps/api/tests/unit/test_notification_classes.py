from easysynq_api.db.models._notification_enums import NotificationDigestMode
from easysynq_api.services.notifications.classes import (
    NotificationClass,
    class_of,
    default_mode,
)


def test_task_assigned_is_action_required():
    assert class_of("task.assigned") is NotificationClass.ACTION_REQUIRED


def test_due_final_is_action_required():
    assert class_of("task.due_final") is NotificationClass.ACTION_REQUIRED


def test_overdue_is_critical():
    assert class_of("task.overdue") is NotificationClass.CRITICAL
    assert class_of("capa.overdue") is NotificationClass.CRITICAL
    assert class_of("integrity.alarm") is NotificationClass.CRITICAL


def test_awareness_events():
    assert class_of("doc.released") is NotificationClass.AWARENESS


def test_system_failed_is_admin_ops():
    assert class_of("system.email_delivery_failed") is NotificationClass.ADMIN_OPS


def test_unknown_event_falls_back_to_action_required():
    assert class_of("totally.unknown") is NotificationClass.ACTION_REQUIRED


def test_escalated_final_is_critical():
    assert class_of("task.escalated_final") is NotificationClass.CRITICAL


def test_escalated_final_in_variable_whitelist():
    from easysynq_api.services.notifications.constants import (
        EVENT_TASK_ESCALATED_FINAL,
        VARIABLE_WHITELIST,
    )

    assert EVENT_TASK_ESCALATED_FINAL == "task.escalated_final"
    assert "task.due_at" in VARIABLE_WHITELIST[EVENT_TASK_ESCALATED_FINAL]


def test_default_modes():
    assert default_mode(NotificationClass.ACTION_REQUIRED) is NotificationDigestMode.DAILY
    assert default_mode(NotificationClass.AWARENESS) is NotificationDigestMode.DAILY
    assert default_mode(NotificationClass.CRITICAL) is NotificationDigestMode.IMMEDIATE
    assert default_mode(NotificationClass.ADMIN_OPS) is NotificationDigestMode.IMMEDIATE
