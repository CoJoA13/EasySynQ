"""Stored five-field schedules use organization civil time, independently of Beat lifetime."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


@pytest.mark.parametrize(
    "cron,last,now,tz,expected",
    [
        ("30 2 * * *", "2026-03-07T07:30Z", "2026-03-08T06:59Z", "America/New_York", False),
        ("30 2 * * *", "2026-03-07T07:30Z", "2026-03-08T07:00Z", "America/New_York", True),
        ("30 2 * * *", "2026-03-08T07:00Z", "2026-03-08T07:01Z", "America/New_York", False),
        ("30 1 * * *", "2026-10-31T05:30Z", "2026-11-01T05:30Z", "America/New_York", True),
        ("30 1 * * *", "2026-11-01T05:30Z", "2026-11-01T06:30Z", "America/New_York", False),
        ("30 1 * * *", "2026-10-31T05:30Z", "2026-11-01T06:30Z", "America/New_York", True),
        ("30 1 * * *", "2026-10-31T05:30Z", "2026-11-01T06:15Z", "America/New_York", True),
        ("0,30 1 * * *", "2026-11-01T06:15Z", "2026-11-01T06:30Z", "America/New_York", False),
        ("0 2 * * *", "2026-10-31T06:00Z", "2026-11-01T06:15Z", "America/New_York", False),
        ("45 1 * * *", "2026-04-03T14:45Z", "2026-04-04T15:05Z", "Australia/Lord_Howe", True),
        ("45 1 * * *", "2026-04-04T15:05Z", "2026-04-04T15:15Z", "Australia/Lord_Howe", False),
        ("* * * * *", "2026-11-01T05:59Z", "2026-11-01T06:30Z", "America/New_York", False),
        ("* * * * *", "2026-11-01T05:59Z", "2026-11-01T07:00Z", "America/New_York", True),
        ("15 2 * * *", "2026-10-02T15:45Z", "2026-10-03T15:30Z", "Australia/Lord_Howe", True),
        ("0 2 * * *", "2026-09-28T02:00Z", "2026-10-01T04:00Z", "UTC", True),
        ("0 2 * * *", "2026-10-01T04:00Z", "2026-10-01T04:01Z", "UTC", False),
        ("0 2 * * *", "2026-10-01T04:00Z", "2026-10-01T03:00Z", "UTC", False),
        ("0 2 * * *", "2026-10-01T04:00Z", "2026-10-01T05:00Z", "UTC", False),
        ("* * * * *", "2026-10-01T02:00Z", "2026-10-01T02:02Z", "UTC", True),
        ("0 2 * * *", "2026-10-01T08:59Z", "2026-10-01T09:00Z", "America/Los_Angeles", True),
        ("0 2 * * *", "2026-10-01T08:59Z", "2026-10-01T09:00Z", "UTC", False),
        ("0 2 1 * mon", "2026-10-04T02:00Z", "2026-10-05T02:00Z", "UTC", True),
        ("0 2 1 * mon", "2026-09-30T02:00Z", "2026-10-01T02:00Z", "UTC", True),
        ("0 2 * * mon", "2026-09-30T02:00Z", "2026-10-01T02:00Z", "UTC", False),
        ("0 2 31 feb mon", "2026-02-01T02:00Z", "2026-02-02T02:00Z", "UTC", True),
        ("0 2 * * 7", "2026-10-03T02:00Z", "2026-10-04T02:00Z", "UTC", True),
        ("0 2 * * SUN", "2026-10-03T02:00Z", "2026-10-04T02:00Z", "UTC", True),
        ("*/15 1-3 * jan,oct mon-fri", "2026-10-01T02:14Z", "2026-10-01T02:15Z", "UTC", True),
        ("0 2 29 feb *", "2026-03-01T00:00Z", "2027-03-01T00:00Z", "UTC", False),
    ],
)
def test_calendar_due(cron: str, last: str, now: str, tz: str, expected: bool) -> None:
    from easysynq_api.services.backup.schedule import resolve_schedule

    instant = datetime.fromisoformat(now)
    zone = ZoneInfo(tz)
    schedule = resolve_schedule(cron, now=instant, tz=zone)
    # Reconstructing the object simulates process recreation; persisted time owns the decision.
    assert schedule.due(datetime.fromisoformat(last), instant, zone) is expected
    assert (
        resolve_schedule(cron, now=instant, tz=zone).due(
            datetime.fromisoformat(last), instant, zone
        )
        is expected
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        5,
        "bad",
        "0 2 31 feb *",
        "60 2 * * *",
        "0 2 * * 8",
        "0 2 * * fri-mon",
        "0 2 * * 1-3junk",
        "*/0 * * * *",
        "0 2 * * mon/2",
    ],
)
def test_malformed_or_impossible_schedule_warns_and_falls_back(
    value: object, caplog: pytest.LogCaptureFixture
) -> None:
    from easysynq_api.services.backup.schedule import resolve_schedule

    now = datetime.fromisoformat("2026-10-01T02:00Z")
    schedule = resolve_schedule(value, now=now, tz=ZoneInfo("UTC"))
    assert schedule.expression == "0 2 * * *"
    assert schedule.due(datetime.fromisoformat("2026-09-30T02:00Z"), now, ZoneInfo("UTC"))
    assert "invalid backup cron" in caplog.text


def test_beat_dispatches_on_minute_boundaries() -> None:
    from celery.schedules import crontab

    from easysynq_api.tasks.app import app

    assert app.conf.beat_schedule["backup-nightly"]["schedule"] == crontab(minute="*")


def test_backup_task_opts_into_due_policy_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    from easysynq_api.tasks import backup

    calls = []

    async def sweep(*, only_due: bool = False):
        calls.append(only_due)
        return {"backups": []}

    monkeypatch.setattr(backup, "run_scheduled_backups", sweep)
    assert backup.backup_run() == {"backups": []}
    assert calls == [True]
