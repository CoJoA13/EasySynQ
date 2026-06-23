import datetime

from easysynq_api.db.models._notification_enums import NotificationDigestMode
from easysynq_api.db.models.notification import NotificationPreference
from easysynq_api.services.notifications.classes import NotificationClass
from easysynq_api.services.notifications.preferences import effective_preferences


def test_none_pref_uses_all_code_defaults():
    eff = effective_preferences(None)
    assert eff.email_enabled is True
    assert eff.modes[NotificationClass.ACTION_REQUIRED] is NotificationDigestMode.DAILY
    assert eff.modes[NotificationClass.CRITICAL] is NotificationDigestMode.IMMEDIATE
    assert eff.digest_hour == 8
    assert eff.timezone == "UTC"
    assert eff.quiet_start is None and eff.quiet_end is None


def test_column_override_wins_over_default():
    pref = NotificationPreference(
        user_id=__import__("uuid").uuid4(),
        email_enabled=True,
        digest_mode_action_required=NotificationDigestMode.IMMEDIATE,
        digest_hour=6,
        timezone="America/New_York",
        quiet_start=datetime.time(22, 0),
        quiet_end=datetime.time(6, 0),
    )
    eff = effective_preferences(pref)
    assert eff.modes[NotificationClass.ACTION_REQUIRED] is NotificationDigestMode.IMMEDIATE
    # an unset class column still resolves to its code default
    assert eff.modes[NotificationClass.AWARENESS] is NotificationDigestMode.DAILY
    assert eff.digest_hour == 6
    assert eff.timezone == "America/New_York"
    assert eff.quiet_start == datetime.time(22, 0)
