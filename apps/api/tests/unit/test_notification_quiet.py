import datetime
from zoneinfo import ZoneInfo

from easysynq_api.db.models._notification_enums import NotificationDigestMode
from easysynq_api.services.notifications.classes import NotificationClass
from easysynq_api.services.notifications.preferences import EffectivePrefs
from easysynq_api.services.notifications.quiet import (
    in_quiet_window,
    should_pierce,
    window_end,
)

UTC = datetime.UTC


def _eff(start, end, tz="UTC"):
    return EffectivePrefs(
        email_enabled=True,
        modes={c: NotificationDigestMode.IMMEDIATE for c in NotificationClass},
        digest_hour=8,
        timezone=tz,
        quiet_start=start,
        quiet_end=end,
    )


def test_no_window_when_unset():
    assert (
        in_quiet_window(_eff(None, None), datetime.datetime(2026, 6, 22, 23, 0, tzinfo=UTC))
        is False
    )


def test_wrap_around_window():
    eff = _eff(datetime.time(22, 0), datetime.time(6, 0))
    assert in_quiet_window(eff, datetime.datetime(2026, 6, 22, 23, 0, tzinfo=UTC)) is True
    assert in_quiet_window(eff, datetime.datetime(2026, 6, 22, 3, 0, tzinfo=UTC)) is True
    assert in_quiet_window(eff, datetime.datetime(2026, 6, 22, 12, 0, tzinfo=UTC)) is False
    # boundaries: start inclusive, end exclusive
    assert in_quiet_window(eff, datetime.datetime(2026, 6, 22, 22, 0, tzinfo=UTC)) is True
    assert in_quiet_window(eff, datetime.datetime(2026, 6, 22, 6, 0, tzinfo=UTC)) is False


def test_same_day_window():
    eff = _eff(datetime.time(1, 0), datetime.time(5, 0))
    assert in_quiet_window(eff, datetime.datetime(2026, 6, 22, 2, 0, tzinfo=UTC)) is True
    assert in_quiet_window(eff, datetime.datetime(2026, 6, 22, 6, 0, tzinfo=UTC)) is False


def test_window_end_wraps_to_next_day():
    eff = _eff(datetime.time(22, 0), datetime.time(6, 0))
    end = window_end(eff, datetime.datetime(2026, 6, 22, 23, 0, tzinfo=UTC))
    assert end == datetime.datetime(2026, 6, 23, 6, 0, tzinfo=UTC)


def test_window_end_respects_timezone():
    eff = _eff(datetime.time(22, 0), datetime.time(6, 0), tz="America/New_York")
    # 23:00 ET == 03:00 UTC next day; next 06:00 ET == 10:00 UTC
    now = datetime.datetime(2026, 6, 23, 3, 0, tzinfo=UTC)
    assert window_end(eff, now) == datetime.datetime(
        2026, 6, 23, 6, 0, tzinfo=ZoneInfo("America/New_York")
    ).astimezone(UTC)


def test_pierce():
    assert should_pierce(NotificationClass.CRITICAL, True) is True
    assert should_pierce(NotificationClass.CRITICAL, False) is False
    assert should_pierce(NotificationClass.ACTION_REQUIRED, True) is False
