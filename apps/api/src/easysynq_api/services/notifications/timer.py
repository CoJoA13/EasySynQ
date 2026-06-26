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
    ESCALATE_2 = "escalate_2"


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

# Far-future sentinel for shift_business_days when its bounded search exhausts (a pathological
# sparse-workweek + long-holiday-span calendar). Year 9999 (NOT date.max) so combining it with a tz
# offset in business_threshold can't overflow datetime; the resulting threshold never trips.
_UNREACHABLE_DATE = datetime.date(9999, 1, 1)


def is_working_day(d: datetime.date, cal: Calendar) -> bool:
    return d.isoweekday() in cal.working_weekdays and d not in cal.holidays


def shift_business_days(
    anchor: datetime.date, n: int, direction: ThresholdDirection, cal: Calendar
) -> datetime.date:
    """The date that is ``n`` working days before/after ``anchor`` (the anchor day is NOT counted).

    ``n <= 0`` returns ``anchor`` unchanged. The loop is bounded so a pathological calendar (sparse
    workweek + a holiday span longer than the window) can never spin forever; if it exhausts before
    counting ``n`` working days, return ``_UNREACHABLE_DATE`` — a FAIL-SAFE far-future sentinel:
    ``business_threshold`` turns it into a far-future instant, so the step's ``now >= threshold``
    never trips and the timer never fires EARLY (better a missed reminder/escalation than one sent
    before ``n`` business days actually elapsed). The resolver rejects an empty working set, so this
    is an extreme edge. (A year-9999 sentinel, NOT ``date.max`` — combining ``date.max`` with a tz
    offset can overflow ``datetime`` in ``business_threshold``.)"""
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
    return _UNREACHABLE_DATE  # fail-safe: never resolve to an arbitrary (possibly non-working) date


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


def snap_to_working_day(due_at: datetime.datetime, cal: Calendar) -> datetime.datetime:
    """Snap ``due_at`` FORWARD to the next working day (S-duedate-snap, R55).

    Returns ``due_at`` unchanged if its date (in ``cal.tz``) is already a working day; otherwise the
    next working day forward at the same local time-of-day, **expressed in ``cal.tz``**.
    Working-day-ness is evaluated in ``cal.tz`` — the exact frame the timer
    (``business_threshold``/``is_working_day``) uses — so a snapped ``due_at`` makes OVERDUE
    (``now >= due_at``) and every offset land on a working day with no special-casing in
    ``due_steps`` (doc 10 §9.5).

    ⚠ The snapped result is returned in ``cal.tz`` (NOT UTC) so an IN-TRANSACTION render —
    ``notifications/render._fmt_date`` formats ``value.date()`` without re-converting — shows the
    snapped working DATE, not a tz-shifted one (an east-of-UTC calendar's local Monday midnight is
    the previous UTC day). The INSTANT is identical either way, so persistence (timestamptz → UTC)
    and every ``now``/offset comparison are unaffected — only the in-memory ``.date()`` aligns.
    (Codex #291 P2.)

    Forward-only (never shortens the SLA window); idempotent. The returned instant's ``cal.tz`` date
    is ALWAYS a working day: a nonexistent (spring-forward gap) wall time can normalize ACROSS
    midnight onto an adjacent day, so each candidate is RE-CHECKED on the resolved instant and the
    walk continues if it landed on a non-working day. Fail-safe: a pathological calendar with no
    reachable working day (the resolver rejects an empty working set, so this needs a holiday span
    longer than the bound) returns ``due_at`` UNCHANGED — NOT a future sentinel (which would
    make the task never overdue, fail-OPEN)."""
    local = due_at.astimezone(cal.tz)
    if is_working_day(local.date(), cal):
        return due_at
    d = local.date()
    for _ in range(366 + 7):  # bounded; matches shift_business_days' exhaustion guard
        d = d + datetime.timedelta(days=1)
        if not is_working_day(d, cal):
            continue
        # Resolve the candidate's true instant (a spring-forward gap can push the reconstructed wall
        # time across midnight), then express it back in cal.tz: re-check on the resolved date and
        # RETURN in cal.tz so an in-transaction render shows the working-calendar date (Codex P2).
        cand = (
            datetime.datetime.combine(d, local.time(), tzinfo=cal.tz)
            .astimezone(datetime.UTC)
            .astimezone(cal.tz)
        )
        if is_working_day(cand.date(), cal):
            return cand
    return (
        due_at  # fail-safe: keep the original instant (never a never-overdue far-future sentinel)
    )


@dataclass(frozen=True)
class TimerPolicy:
    remind_1_before: datetime.timedelta | None
    remind_2_before: datetime.timedelta | None
    escalate_1_after: datetime.timedelta | None
    # = None so existing constructors stay valid; production sites (escalation.py) wire it by
    # keyword. A forgotten future construction site would silently disable tier-2 — covered by the
    # integration wiring test (test_escalate_2_to_top_management).
    escalate_2_after: datetime.timedelta | None = None


@dataclass(frozen=True)
class TimerStamps:
    remind_1_sent_at: datetime.datetime | None
    remind_2_sent_at: datetime.datetime | None
    overdue_notified_at: datetime.datetime | None
    escalated_1_at: datetime.datetime | None
    escalated_2_at: datetime.datetime | None = None


def due_steps(
    policy: TimerPolicy,
    due_at: datetime.datetime,
    stamps: TimerStamps,
    now: datetime.datetime,
    calendar: Calendar,
) -> list[TimerStep]:
    """Steps whose threshold has passed AND whose stamp is null, chronological. Reminder/escalate
    thresholds are BUSINESS-DAY offsets against ``calendar`` (skip weekends + holidays); OVERDUE is
    always-on at ``due_at`` with NO business-day shift (D-5 — ``due_at`` itself is snapped to a
    working day at materialize, R55). Reminders/escalate stay gated by a configured (non-null)
    offset AND only fire when ``now`` is itself a working day — so a sweep DELAYED past the
    threshold into a non-working day (worker down / template missing over a weekend) defers the
    ping to the next working day (doc 10 §9.5: timers do not fire on non-working days). OVERDUE
    is ALSO gated on ``now_is_working`` (S-orgtz-unify, closing R55 D-5's weekend-pierce
    exemption) — a weekend/holiday overdue notice defers to the next working day (doc 10 §9.5).
    Steps in chronological order: REMIND_1 → REMIND_2 → OVERDUE → ESCALATE_1 then ESCALATE_2
    (a second, later escalation tier — S-escalate2)."""
    out: list[TimerStep] = []
    now_is_working = is_working_day(now.astimezone(calendar.tz).date(), calendar)
    if (
        policy.remind_1_before is not None
        and stamps.remind_1_sent_at is None
        and now_is_working
        and now
        >= business_threshold(due_at, policy.remind_1_before, ThresholdDirection.BEFORE, calendar)
    ):
        out.append(TimerStep.REMIND_1)
    if (
        policy.remind_2_before is not None
        and stamps.remind_2_sent_at is None
        and now_is_working
        and now
        >= business_threshold(due_at, policy.remind_2_before, ThresholdDirection.BEFORE, calendar)
    ):
        out.append(TimerStep.REMIND_2)
    if stamps.overdue_notified_at is None and now_is_working and now >= due_at:
        out.append(TimerStep.OVERDUE)
    if (
        policy.escalate_1_after is not None
        and stamps.escalated_1_at is None
        and now_is_working
        and now
        >= business_threshold(due_at, policy.escalate_1_after, ThresholdDirection.AFTER, calendar)
    ):
        out.append(TimerStep.ESCALATE_1)
    if (
        policy.escalate_2_after is not None
        and stamps.escalated_2_at is None
        and now_is_working
        and now
        >= business_threshold(due_at, policy.escalate_2_after, ThresholdDirection.AFTER, calendar)
    ):
        out.append(TimerStep.ESCALATE_2)
    return out
