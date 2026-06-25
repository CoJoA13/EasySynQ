import datetime
import zoneinfo
from datetime import timedelta

from easysynq_api.services.notifications.timer import (
    DEFAULT_CALENDAR,
    Calendar,
    ThresholdDirection,
    TimerPolicy,
    TimerStamps,
    TimerStep,
    business_threshold,
    due_steps,
    is_working_day,
    shift_business_days,
)

UTC = datetime.UTC
DUE = datetime.datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
POLICY = TimerPolicy(
    remind_1_before=timedelta(days=3),
    remind_2_before=timedelta(days=1),
    escalate_1_after=timedelta(days=1),
)
NONE = TimerStamps(None, None, None, None)

MON_FRI = Calendar(
    working_weekdays=frozenset({1, 2, 3, 4, 5}), holidays=frozenset(), tz=zoneinfo.ZoneInfo("UTC")
)
ALL_DAYS = Calendar(
    working_weekdays=frozenset({1, 2, 3, 4, 5, 6, 7}),
    holidays=frozenset(),
    tz=zoneinfo.ZoneInfo("UTC"),
)
_D = datetime.date  # alias for the date-math tests


def _at(**kw):
    return DUE + timedelta(**kw)


# ---------------------------------------------------------------------------
# Existing due_steps tests — now with ALL_DAYS (business-day math degenerates to raw).
# ---------------------------------------------------------------------------


def test_before_remind1_nothing():
    assert due_steps(POLICY, DUE, NONE, _at(days=-4), ALL_DAYS) == []


def test_past_remind1_only():
    assert due_steps(POLICY, DUE, NONE, _at(days=-2), ALL_DAYS) == [TimerStep.REMIND_1]


def test_past_due_fires_remind1_remind2_overdue_in_order():
    assert due_steps(POLICY, DUE, NONE, _at(minutes=1), ALL_DAYS) == [
        TimerStep.REMIND_1,
        TimerStep.REMIND_2,
        TimerStep.OVERDUE,
    ]


def test_past_escalate1_fires_all_in_order():
    assert due_steps(POLICY, DUE, NONE, _at(days=2), ALL_DAYS) == [
        TimerStep.REMIND_1,
        TimerStep.REMIND_2,
        TimerStep.OVERDUE,
        TimerStep.ESCALATE_1,
    ]


def test_stamped_steps_do_not_refire():
    stamps = TimerStamps(
        remind_1_sent_at=DUE, remind_2_sent_at=DUE, overdue_notified_at=None, escalated_1_at=None
    )
    assert due_steps(POLICY, DUE, stamps, _at(days=2), ALL_DAYS) == [
        TimerStep.OVERDUE,
        TimerStep.ESCALATE_1,
    ]


def test_null_offsets_disable_those_steps_overdue_still_fires():
    pol = TimerPolicy(remind_1_before=None, remind_2_before=None, escalate_1_after=None)
    assert due_steps(pol, DUE, NONE, _at(days=2), ALL_DAYS) == [TimerStep.OVERDUE]


# ---------------------------------------------------------------------------
# Business-day helpers (S-notify-6).
# ---------------------------------------------------------------------------


def test_is_working_day_weekday_weekend_holiday():
    cal = Calendar(
        frozenset({1, 2, 3, 4, 5}), frozenset({_D(2026, 6, 24)}), zoneinfo.ZoneInfo("UTC")
    )
    assert is_working_day(_D(2026, 6, 23), cal) is True  # Tuesday
    assert is_working_day(_D(2026, 6, 27), cal) is False  # Saturday
    assert is_working_day(_D(2026, 6, 24), cal) is False  # holiday (a Wednesday)


def test_shift_business_days_before_skips_weekend():
    # 3 business days BEFORE Monday 2026-06-29 -> Wednesday 2026-06-24 (skip Sat/Sun).
    assert shift_business_days(_D(2026, 6, 29), 3, ThresholdDirection.BEFORE, MON_FRI) == _D(
        2026, 6, 24
    )


def test_shift_business_days_after_skips_weekend():
    # 1 business day AFTER Friday 2026-06-26 -> Monday 2026-06-29.
    assert shift_business_days(_D(2026, 6, 26), 1, ThresholdDirection.AFTER, MON_FRI) == _D(
        2026, 6, 29
    )


def test_shift_business_days_skips_holiday():
    cal = Calendar(
        frozenset({1, 2, 3, 4, 5}), frozenset({_D(2026, 6, 24)}), zoneinfo.ZoneInfo("UTC")
    )
    # 3 biz days before Mon 06-29 with Wed 06-24 a holiday -> Tue 06-23 (skip Sat,Sun,Wed-holiday).
    assert shift_business_days(_D(2026, 6, 29), 3, ThresholdDirection.BEFORE, cal) == _D(
        2026, 6, 23
    )


def test_shift_business_days_zero_is_identity():
    assert shift_business_days(_D(2026, 6, 29), 0, ThresholdDirection.BEFORE, MON_FRI) == _D(
        2026, 6, 29
    )


def test_business_threshold_before_preserves_time_of_day():
    due = datetime.datetime(2026, 6, 29, 14, 37, tzinfo=UTC)  # Monday
    got = business_threshold(due, timedelta(days=3), ThresholdDirection.BEFORE, MON_FRI)
    assert got == datetime.datetime(2026, 6, 24, 14, 37, tzinfo=UTC)  # prior Wednesday, same time


def test_business_threshold_after_skips_weekend():
    due = datetime.datetime(2026, 6, 26, 12, 0, tzinfo=UTC)  # Friday
    got = business_threshold(due, timedelta(days=1), ThresholdDirection.AFTER, MON_FRI)
    assert got == datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)  # Monday


def test_business_threshold_tz_changes_local_date():
    # 02:00 UTC on Mon 06-29 is 22:00 EDT on SUNDAY 06-28 -> 3 biz days before Sunday -> Wed 06-24.
    cal = Calendar(frozenset({1, 2, 3, 4, 5}), frozenset(), zoneinfo.ZoneInfo("America/New_York"))
    due = datetime.datetime(2026, 6, 29, 2, 0, tzinfo=UTC)
    got = business_threshold(due, timedelta(days=3), ThresholdDirection.BEFORE, cal)
    # 06-24 22:00 EDT == 06-25 02:00 UTC.
    assert got == datetime.datetime(2026, 6, 25, 2, 0, tzinfo=UTC)


def test_business_threshold_dst_transition_does_not_crash():
    # US spring-forward 2026-03-08; just assert it returns a sane UTC instant within a day.
    cal = Calendar(frozenset({1, 2, 3, 4, 5}), frozenset(), zoneinfo.ZoneInfo("America/New_York"))
    due = datetime.datetime(2026, 3, 10, 12, 0, tzinfo=UTC)  # Tuesday after the transition
    got = business_threshold(due, timedelta(days=1), ThresholdDirection.AFTER, cal)
    assert got.tzinfo == UTC and abs((got - due).total_seconds()) < 36 * 3600


def test_all_days_calendar_degenerates_to_raw():
    due = datetime.datetime(2026, 6, 23, 12, 0, tzinfo=UTC)  # Tuesday
    assert business_threshold(
        due, timedelta(days=3), ThresholdDirection.BEFORE, ALL_DAYS
    ) == due - timedelta(days=3)
    assert business_threshold(
        due, timedelta(days=1), ThresholdDirection.AFTER, ALL_DAYS
    ) == due + timedelta(days=1)


def test_default_calendar_is_mon_fri_utc():
    assert DEFAULT_CALENDAR.working_weekdays == frozenset({1, 2, 3, 4, 5})
    assert DEFAULT_CALENDAR.holidays == frozenset()
    assert DEFAULT_CALENDAR.tz == zoneinfo.ZoneInfo("UTC")


def test_business_threshold_sub_day_remainder_is_wall_clock():
    # Pins the documented sub-day behavior (no production caller in v1 — seeded offsets are whole
    # days): the whole-day component walks business days; the remainder is raw wall-clock.
    # 1.5 biz days BEFORE Monday 06-29 12:00 = (1 biz day back = Friday 06-26 12:00) - 12h
    # = Friday 06-26 00:00. The remainder can legitimately land on a non-working time-of-day.
    due = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)  # Monday
    got = business_threshold(due, timedelta(days=1, hours=12), ThresholdDirection.BEFORE, MON_FRI)
    assert got == datetime.datetime(2026, 6, 26, 0, 0, tzinfo=UTC)
    # AFTER: 1.5 biz days after Fri 06-26 12:00 = (1 biz day = Mon 06-29 12:00) + 12h = Tue 00:00.
    got2 = business_threshold(
        datetime.datetime(2026, 6, 26, 12, 0, tzinfo=UTC),
        timedelta(days=1, hours=12),
        ThresholdDirection.AFTER,
        MON_FRI,
    )
    assert got2 == datetime.datetime(2026, 6, 30, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# due_steps with a Mon-Fri calendar (business-day timing).
# ---------------------------------------------------------------------------


def test_due_steps_reminder_uses_business_days():
    due = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)  # Monday
    pol = TimerPolicy(
        remind_1_before=timedelta(days=3), remind_2_before=None, escalate_1_after=None
    )
    # business remind_1 threshold = prior Wednesday 06-24 12:00 (skip weekend).
    assert (
        due_steps(pol, due, NONE, datetime.datetime(2026, 6, 24, 11, 59, tzinfo=UTC), MON_FRI) == []
    )
    assert due_steps(
        pol, due, NONE, datetime.datetime(2026, 6, 24, 12, 0, tzinfo=UTC), MON_FRI
    ) == [TimerStep.REMIND_1]


def test_due_steps_escalate_waits_for_business_day_overdue_does_not():
    due = datetime.datetime(2026, 6, 26, 12, 0, tzinfo=UTC)  # Friday
    pol = TimerPolicy(
        remind_1_before=None, remind_2_before=None, escalate_1_after=timedelta(days=1)
    )
    sat = datetime.datetime(
        2026, 6, 27, 18, 0, tzinfo=UTC
    )  # Saturday: past raw due+1d, before biz Mon
    # OVERDUE fires (unshifted, D-5); ESCALATE_1 does NOT (business threshold is Monday 06-29).
    assert due_steps(pol, due, NONE, sat, MON_FRI) == [TimerStep.OVERDUE]
    mon = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    assert due_steps(pol, due, NONE, mon, MON_FRI) == [TimerStep.OVERDUE, TimerStep.ESCALATE_1]


def test_due_steps_stamp_gating_unchanged_with_calendar():
    due = datetime.datetime(2026, 6, 26, 12, 0, tzinfo=UTC)  # Friday
    pol = TimerPolicy(
        remind_1_before=None, remind_2_before=None, escalate_1_after=timedelta(days=1)
    )
    stamps = TimerStamps(None, None, None, escalated_1_at=due)  # already escalated
    mon = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    assert due_steps(pol, due, stamps, mon, MON_FRI) == [
        TimerStep.OVERDUE
    ]  # ESCALATE_1 not re-fired
