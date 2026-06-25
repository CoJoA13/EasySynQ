"""Pure step-math for the escalation timer_sweep (S-notify-4, doc 10 §9.5). No DB, no I/O.

Given a task's due_at, its SLA offsets, the per-step stamps, and `now`, decide which timer steps
must fire — in chronological order, never re-firing an already-stamped step."""

import datetime
import enum
import zoneinfo
from dataclasses import dataclass


class TimerStep(enum.StrEnum):
    REMIND_1 = "remind_1"
    REMIND_2 = "remind_2"
    OVERDUE = "overdue"
    ESCALATE_1 = "escalate_1"


class ThresholdDirection(enum.Enum):
    BEFORE = "before"  # reminders: N business days BEFORE due_at
    AFTER = "after"  # escalation: N business days AFTER due_at


@dataclass(frozen=True)
class Calendar:
    """A pure business-day calendar. ``working_weekdays`` uses ISO weekday ints (1=Mon..7=Sun)."""

    working_weekdays: frozenset[int]
    holidays: frozenset[datetime.date]
    tz: zoneinfo.ZoneInfo


# Fail-safe default the resolver falls back to: Mon-Fri, no holidays, UTC.
DEFAULT_CALENDAR = Calendar(
    working_weekdays=frozenset({1, 2, 3, 4, 5}),
    holidays=frozenset(),
    tz=zoneinfo.ZoneInfo("UTC"),
)


def is_working_day(d: datetime.date, cal: Calendar) -> bool:
    return d.isoweekday() in cal.working_weekdays and d not in cal.holidays


def shift_business_days(
    anchor: datetime.date, n: int, direction: ThresholdDirection, cal: Calendar
) -> datetime.date:
    """The date that is ``n`` working days before/after ``anchor`` (the anchor day is NOT counted).

    ``n <= 0`` returns ``anchor`` unchanged. The loop is bounded so a pathological all-non-working
    calendar can never spin forever (the resolver rejects an empty working set anyway)."""
    if n <= 0:
        return anchor
    step = datetime.timedelta(days=1 if direction is ThresholdDirection.AFTER else -1)
    d = anchor
    counted = 0
    for _ in range(n * 7 + 366):
        d = d + step
        if is_working_day(d, cal):
            counted += 1
            if counted == n:
                return d
    return d  # pragma: no cover — only an all-non-working calendar reaches here


def business_threshold(
    due_at: datetime.datetime,
    offset: datetime.timedelta,
    direction: ThresholdDirection,
    cal: Calendar,
) -> datetime.datetime:
    """The UTC instant ``offset`` BUSINESS days before/after ``due_at``, evaluated against ``cal``.

    The whole-day component walks working days; any sub-day remainder is applied as wall-clock.
    Preserves ``due_at``'s local (``cal.tz``) time-of-day on the shifted date. (DST-ambiguous wall
    times default to ``fold=0`` — within tolerance for a 5-minute-granularity sweep.)"""
    local = due_at.astimezone(cal.tz)
    whole = offset.days  # timedelta normalizes a positive offset: days >= 0, remainder >= 0
    remainder = offset - datetime.timedelta(days=whole)
    target_date = shift_business_days(local.date(), whole, direction, cal)
    threshold = datetime.datetime.combine(target_date, local.time(), tzinfo=cal.tz)
    threshold = (
        threshold - remainder if direction is ThresholdDirection.BEFORE else threshold + remainder
    )
    return threshold.astimezone(datetime.UTC)


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
    calendar: Calendar,
) -> list[TimerStep]:
    """Steps whose threshold has passed AND whose stamp is null, chronological. Reminder/escalate
    thresholds are BUSINESS-DAY offsets against ``calendar`` (skip weekends + holidays); OVERDUE is
    always-on at ``due_at`` with NO business-day shift (D-5 — ``due_at`` itself is raw wall-clock,
    snapping it is the upstream R39 residual). Reminders/escalate stay gated by a configured
    (non-null) offset."""
    out: list[TimerStep] = []
    if (
        policy.remind_1_before is not None
        and stamps.remind_1_sent_at is None
        and now
        >= business_threshold(due_at, policy.remind_1_before, ThresholdDirection.BEFORE, calendar)
    ):
        out.append(TimerStep.REMIND_1)
    if (
        policy.remind_2_before is not None
        and stamps.remind_2_sent_at is None
        and now
        >= business_threshold(due_at, policy.remind_2_before, ThresholdDirection.BEFORE, calendar)
    ):
        out.append(TimerStep.REMIND_2)
    if stamps.overdue_notified_at is None and now >= due_at:
        out.append(TimerStep.OVERDUE)
    if (
        policy.escalate_1_after is not None
        and stamps.escalated_1_at is None
        and now
        >= business_threshold(due_at, policy.escalate_1_after, ThresholdDirection.AFTER, calendar)
    ):
        out.append(TimerStep.ESCALATE_1)
    return out
