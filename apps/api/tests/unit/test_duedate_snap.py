"""S-duedate-snap (R55): unit tests for the pure ``snap_to_working_day`` helper (no DB).

The helper snaps a UTC ``due_at`` FORWARD to the next working day (evaluated in the calendar's tz),
preserving local time-of-day; a working-day due_at is returned unchanged. The returned instant's
cal.tz date is ALWAYS a working day (re-checked against a midnight-crossing DST gap), EXCEPT on the
pathological all-holiday exhaustion fail-safe (which returns the input unchanged). Idempotent;
monotonic-forward.

All correctness lives here (the harness cannot false-PASS pure date math); the integration tests
prove only the DB→helper wiring.
"""

import datetime
import zoneinfo

from easysynq_api.services.notifications.timer import (
    DEFAULT_CALENDAR,
    Calendar,
    is_working_day,
    snap_to_working_day,
)

UTC = datetime.UTC
_D = datetime.date

# 2026-06-24 is a Wednesday → 26 Fri, 27 Sat, 28 Sun, 29 Mon, 30 Tue (the timer-sweep anchor).
MON_FRI = Calendar(
    working_weekdays=frozenset({1, 2, 3, 4, 5}), holidays=frozenset(), tz=zoneinfo.ZoneInfo("UTC")
)


def _dt(y, m, d, hh=0, mm=0, tz=UTC):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=tz)


# ---------------------------------------------------------------------------
# Core forward-snap, time-of-day preservation, unchanged-on-working-day
# ---------------------------------------------------------------------------


def test_saturday_snaps_to_monday_same_time():
    due = _dt(2026, 6, 27, 14, 30)  # Saturday 14:30 UTC
    out = snap_to_working_day(due, MON_FRI)
    assert out == _dt(2026, 6, 29, 14, 30)  # Monday 14:30 UTC, time-of-day preserved


def test_sunday_snaps_to_monday_same_time():
    out = snap_to_working_day(_dt(2026, 6, 28, 9, 0), MON_FRI)
    assert out == _dt(2026, 6, 29, 9, 0)


def test_working_day_returned_unchanged_identity():
    due = _dt(2026, 6, 26, 9, 0)  # Friday — already a working day
    out = snap_to_working_day(due, MON_FRI)
    assert out is due  # exact instant, no tz round-trip


def test_default_calendar_mon_fri_utc_snaps_weekend():
    out = snap_to_working_day(_dt(2026, 6, 27, 8, 0), DEFAULT_CALENDAR)
    assert out == _dt(2026, 6, 29, 8, 0)


# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------


def test_weekday_holiday_snaps_forward():
    cal = Calendar({1, 2, 3, 4, 5}, frozenset({_D(2026, 6, 26)}), zoneinfo.ZoneInfo("UTC"))
    # Friday is a holiday → Fri due skips Fri(holiday)+Sat+Sun → Monday.
    out = snap_to_working_day(_dt(2026, 6, 26, 10, 0), cal)
    assert out == _dt(2026, 6, 29, 10, 0)


def test_friday_due_with_monday_holiday_skips_to_tuesday():
    cal = Calendar({1, 2, 3, 4, 5}, frozenset({_D(2026, 6, 29)}), zoneinfo.ZoneInfo("UTC"))
    # Saturday due → Sun(no), Mon(holiday), Tue(yes).
    out = snap_to_working_day(_dt(2026, 6, 27, 12, 0), cal)
    assert out == _dt(2026, 6, 30, 12, 0)


# ---------------------------------------------------------------------------
# Idempotency + monotonic
# ---------------------------------------------------------------------------


def test_idempotent():
    for due in (
        _dt(2026, 6, 27, 14, 30),  # Sat
        _dt(2026, 6, 28, 0, 0),  # Sun
        _dt(2026, 6, 26, 9, 0),  # Fri (no-op)
    ):
        once = snap_to_working_day(due, MON_FRI)
        assert snap_to_working_day(once, MON_FRI) == once


def test_monotonic_forward():
    for due in (
        _dt(2026, 6, 27, 14, 30),
        _dt(2026, 6, 28, 23, 59),
        _dt(2026, 6, 26, 0, 0),
        _dt(2026, 6, 29, 6, 0),
    ):
        assert snap_to_working_day(due, MON_FRI) >= due


# ---------------------------------------------------------------------------
# tz boundary — BOTH signs (TZ-2): the snap evaluates the DATE in cal.tz, not UTC.
# ---------------------------------------------------------------------------


def test_eastward_tz_local_saturday_snaps():
    # Asia/Tokyo (UTC+9, no DST): Fri 23:00 UTC == Sat 08:00 Tokyo → local date Saturday → snaps.
    tokyo = zoneinfo.ZoneInfo("Asia/Tokyo")
    cal = Calendar({1, 2, 3, 4, 5}, frozenset(), tokyo)
    due = _dt(2026, 6, 26, 23, 0)  # Fri 23:00 UTC = Sat 08:00 Tokyo
    out = snap_to_working_day(due, cal)
    local = out.astimezone(tokyo)
    assert local.date() == _D(2026, 6, 29)  # Monday in Tokyo
    assert local.hour == 8 and local.minute == 0  # time-of-day preserved in cal.tz
    assert is_working_day(local.date(), cal)


def test_westward_tz_local_friday_does_not_snap():
    # America/Bogota (UTC-5, no DST): Sat 01:00 UTC == Fri 20:00 Bogota → Friday local → no snap.
    cal = Calendar({1, 2, 3, 4, 5}, frozenset(), zoneinfo.ZoneInfo("America/Bogota"))
    due = _dt(2026, 6, 27, 1, 0)  # Sat 01:00 UTC = Fri 20:00 Bogota
    assert snap_to_working_day(due, cal) is due  # unchanged


# ---------------------------------------------------------------------------
# DST midnight-gap (IDEM-1): the §3 re-check must reject a candidate the gap pushed onto a
# non-working day. Mon-Fri/UTC cannot catch this. (Asserts the OUTCOME, not the exact instant.)
# ---------------------------------------------------------------------------


def test_dst_midnight_gap_result_is_working_and_idempotent():
    nuuk = zoneinfo.ZoneInfo("America/Nuuk")
    # Working: Mon,Tue,Wed,Thu,Sat (Fri + Sun non-working). Around Nuuk's spring-forward, a 23:30
    # wall time on the Saturday candidate normalizes across midnight onto a non-working Sunday — the
    # re-check must keep walking. We assert the guarantee, not the exact instant.
    cal = Calendar(frozenset({1, 2, 3, 4, 6}), frozenset(), nuuk)
    due = datetime.datetime(2024, 3, 29, 23, 30, tzinfo=nuuk)  # a Friday (non-working) in Nuuk
    out = snap_to_working_day(due, cal)
    assert is_working_day(out.astimezone(nuuk).date(), cal)  # POST-CONDITION: a working day
    assert snap_to_working_day(out, cal) == out  # idempotent
    assert out >= due.astimezone(UTC)  # monotonic


# ---------------------------------------------------------------------------
# Fail-safe: a calendar with no reachable working day → return the input UNCHANGED (NOT a sentinel).
# ---------------------------------------------------------------------------


def test_all_holiday_calendar_returns_input_unchanged():
    due = _dt(2026, 6, 27, 10, 0)  # a Saturday
    # Every day a holiday across the FULL forward bound (366+7) from the due → the walk exhausts.
    span = frozenset(due.date() + datetime.timedelta(days=i) for i in range(-5, 400))
    cal = Calendar({1, 2, 3, 4, 5}, span, zoneinfo.ZoneInfo("UTC"))
    out = snap_to_working_day(due, cal)
    assert out == due  # fail-safe: unchanged (do NOT assert is_working_day here — there is none)


# ---------------------------------------------------------------------------
# D-5: the MR_ACTION date builder (mgmt_review/spawn._action_due_at) builds at midnight in the
# CALENDAR's tz (NOT the env easysynq_org_timezone) and snaps — the divergent-tz lock. The
# review.py / cadence.py date builds use the identical inline combine(date, midnight, cal.tz)+snap
# pattern (e2e-covered by the PERIODIC_REVIEW integration test).
# ---------------------------------------------------------------------------


def test_action_due_at_builds_in_calendar_tz_not_env():
    from easysynq_api.services.mgmt_review.spawn import _action_due_at

    tokyo = zoneinfo.ZoneInfo("Asia/Tokyo")
    cal = Calendar({1, 2, 3, 4, 5}, frozenset(), tokyo)
    # An operator-set MONDAY (a working day) must NOT be pushed: built at Monday-midnight Tokyo and
    # left unchanged (regardless of the env tz). Pre-D-5 this built at env-tz midnight.
    out = _action_due_at(_D(2026, 6, 29), cal)  # Monday
    assert out == datetime.datetime(2026, 6, 29, 0, 0, tzinfo=tokyo).astimezone(UTC)
    assert is_working_day(out.astimezone(tokyo).date(), cal)


def test_action_due_at_weekend_snaps_and_none_passthrough():
    from easysynq_api.services.mgmt_review.spawn import _action_due_at

    cal = Calendar({1, 2, 3, 4, 5}, frozenset(), zoneinfo.ZoneInfo("UTC"))
    out = _action_due_at(_D(2026, 6, 27), cal)  # Saturday → Monday
    assert out == _dt(2026, 6, 29, 0, 0)
    assert _action_due_at(None, cal) is None  # open-ended action stays undated
