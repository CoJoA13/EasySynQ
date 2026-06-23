"""Pure step-math for the escalation timer_sweep (S-notify-4, doc 10 §9.5). No DB, no I/O.

Given a task's due_at, its SLA offsets, the per-step stamps, and `now`, decide which timer steps
must fire — in chronological order, never re-firing an already-stamped step."""

import datetime
import enum
from dataclasses import dataclass


class TimerStep(enum.StrEnum):
    REMIND_1 = "remind_1"
    REMIND_2 = "remind_2"
    OVERDUE = "overdue"
    ESCALATE_1 = "escalate_1"


@dataclass(frozen=True)
class TimerPolicy:
    remind_1_before: datetime.timedelta | None
    remind_2_before: datetime.timedelta | None
    escalate_1_after: datetime.timedelta | None


@dataclass(frozen=True)
class TimerStamps:
    remind_1_sent_at: datetime.datetime | None
    remind_2_sent_at: datetime.datetime | None
    overdue_notified_at: datetime.datetime | None
    escalated_1_at: datetime.datetime | None


def due_steps(
    policy: TimerPolicy,
    due_at: datetime.datetime,
    stamps: TimerStamps,
    now: datetime.datetime,
) -> list[TimerStep]:
    """Steps whose threshold has passed AND whose stamp is null, chronological. OVERDUE is always-on
    (at due_at); reminders/escalate are gated by a configured (non-null) offset."""
    out: list[TimerStep] = []
    if (
        policy.remind_1_before is not None
        and stamps.remind_1_sent_at is None
        and now >= due_at - policy.remind_1_before
    ):
        out.append(TimerStep.REMIND_1)
    if (
        policy.remind_2_before is not None
        and stamps.remind_2_sent_at is None
        and now >= due_at - policy.remind_2_before
    ):
        out.append(TimerStep.REMIND_2)
    if stamps.overdue_notified_at is None and now >= due_at:
        out.append(TimerStep.OVERDUE)
    if (
        policy.escalate_1_after is not None
        and stamps.escalated_1_at is None
        and now >= due_at + policy.escalate_1_after
    ):
        out.append(TimerStep.ESCALATE_1)
    return out
