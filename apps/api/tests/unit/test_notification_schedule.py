import datetime
from zoneinfo import ZoneInfo

from easysynq_api.db.models._notification_enums import NotificationDigestMode
from easysynq_api.services.notifications.classes import NotificationClass
from easysynq_api.services.notifications.preferences import EffectivePrefs
from easysynq_api.services.notifications.schedule import next_digest_at

UTC = datetime.UTC


def _eff(hour, tz="UTC"):
    return EffectivePrefs(
        email_enabled=True,
        modes={c: NotificationDigestMode.DAILY for c in NotificationClass},
        digest_hour=hour,
        timezone=tz,
        quiet_start=None,
        quiet_end=None,
    )


def test_later_today_when_before_hour():
    now = datetime.datetime(2026, 6, 22, 6, 0, tzinfo=UTC)
    assert next_digest_at(_eff(8), now) == datetime.datetime(2026, 6, 22, 8, 0, tzinfo=UTC)


def test_tomorrow_when_past_hour():
    now = datetime.datetime(2026, 6, 22, 9, 0, tzinfo=UTC)
    assert next_digest_at(_eff(8), now) == datetime.datetime(2026, 6, 23, 8, 0, tzinfo=UTC)


def test_exactly_at_hour_rolls_to_tomorrow():
    now = datetime.datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    assert next_digest_at(_eff(8), now) == datetime.datetime(2026, 6, 23, 8, 0, tzinfo=UTC)


def test_timezone_aware():
    # 08:00 America/New_York on 2026-06-22 == 12:00 UTC (EDT, UTC-4)
    now = datetime.datetime(2026, 6, 22, 6, 0, tzinfo=UTC)
    got = next_digest_at(_eff(8, "America/New_York"), now)
    assert got == datetime.datetime(
        2026, 6, 22, 8, 0, tzinfo=ZoneInfo("America/New_York")
    ).astimezone(UTC)
