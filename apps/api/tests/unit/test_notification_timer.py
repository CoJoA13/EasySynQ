import datetime
from datetime import timedelta

from easysynq_api.services.notifications.timer import (
    TimerPolicy,
    TimerStamps,
    TimerStep,
    due_steps,
)

UTC = datetime.UTC
DUE = datetime.datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
POLICY = TimerPolicy(
    remind_1_before=timedelta(days=3),
    remind_2_before=timedelta(days=1),
    escalate_1_after=timedelta(days=1),
)
NONE = TimerStamps(None, None, None, None)


def _at(**kw):
    return DUE + timedelta(**kw)


def test_before_remind1_nothing():
    assert due_steps(POLICY, DUE, NONE, _at(days=-4)) == []


def test_past_remind1_only():
    assert due_steps(POLICY, DUE, NONE, _at(days=-2)) == [TimerStep.REMIND_1]


def test_past_due_fires_remind1_remind2_overdue_in_order():
    assert due_steps(POLICY, DUE, NONE, _at(minutes=1)) == [
        TimerStep.REMIND_1,
        TimerStep.REMIND_2,
        TimerStep.OVERDUE,
    ]


def test_past_escalate1_fires_all_in_order():
    assert due_steps(POLICY, DUE, NONE, _at(days=2)) == [
        TimerStep.REMIND_1,
        TimerStep.REMIND_2,
        TimerStep.OVERDUE,
        TimerStep.ESCALATE_1,
    ]


def test_stamped_steps_do_not_refire():
    stamps = TimerStamps(
        remind_1_sent_at=DUE, remind_2_sent_at=DUE, overdue_notified_at=None, escalated_1_at=None
    )
    assert due_steps(POLICY, DUE, stamps, _at(days=2)) == [TimerStep.OVERDUE, TimerStep.ESCALATE_1]


def test_null_offsets_disable_those_steps_overdue_still_fires():
    pol = TimerPolicy(remind_1_before=None, remind_2_before=None, escalate_1_after=None)
    assert due_steps(pol, DUE, NONE, _at(days=2)) == [TimerStep.OVERDUE]
