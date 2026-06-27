from easysynq_api.services.notifications.classes import NotificationClass, class_of
from easysynq_api.services.notifications.constants import EVENT_CAPA_OVERDUE, VARIABLE_WHITELIST


def test_capa_overdue_event_key():
    assert EVENT_CAPA_OVERDUE == "capa.overdue"


def test_capa_overdue_is_whitelisted_and_critical():
    wl = VARIABLE_WHITELIST[EVENT_CAPA_OVERDUE]
    assert {"subject.identifier", "subject.title", "target_completion_date", "deep_link"} <= wl
    assert class_of(EVENT_CAPA_OVERDUE) is NotificationClass.CRITICAL
