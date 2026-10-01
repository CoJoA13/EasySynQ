"""Five-field backup cron on the organization's civil clock.

Persist actual UTC attempt times. Only cron arithmetic projects local fields onto a uniform
timeline: crossing a spring gap catches up at the first valid instant; a repeated hour does not
replay already-consumed civil minutes. Missed occurrences collapse into one attempt.
"""

from __future__ import annotations

import calendar
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from celery.schedules import crontab

DEFAULT_CRON = "0 2 * * *"
logger = logging.getLogger("easysynq.backup")
_ATOM = r"(?:[0-9]{1,2}|[a-z]{3})"
_PART = re.compile(rf"(?P<base>\*|{_ATOM}(?:-{_ATOM})?)(?:/(?P<step>[0-9]{{1,2}}))?")
_MONTHS = {name.lower(): index for index, name in enumerate(calendar.month_abbr) if name}
_DAYS = {
    name: index for index, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))
}
Fields = tuple[frozenset[int], ...]


def _field(token: str, low: int, high: int, names: dict[str, int]) -> frozenset[int]:
    values: set[int] = set()

    def number(value: str) -> int:
        result = names[value] if value in names else int(value)
        if not low <= result <= high:
            raise ValueError("cron value outside field range")
        return result

    for part in token.split(","):
        match = _PART.fullmatch(part)
        if match is None:
            raise ValueError("invalid cron field")
        base, step_text = match.group("base", "step")
        step = int(step_text) if step_text else 1
        if not step or (step_text and base != "*" and "-" not in base):
            raise ValueError("cron steps require a wildcard or range")
        if base == "*":
            first, last = low, high
        elif "-" in base:
            left, right = base.split("-")
            first, last = number(left), number(right)
        else:
            first = last = number(base)
        if first > last:
            raise ValueError("cron range must ascend")
        values.update(range(first, last + 1, step))
    return frozenset(values)


def _civil(value: datetime, tz: ZoneInfo) -> datetime:
    local = value.astimezone(tz)
    if local.fold:
        # During the second traversal, the whole first repeated interval has already elapsed.
        # Its exclusive end is the civil high-water mark, even if this worker was down then.
        # Projecting only the current repeated minute would miss catch-up, then replay slots.
        first_offset, second_offset = local.replace(fold=0).utcoffset(), local.utcoffset()
        if first_offset is None or second_offset is None:
            raise ValueError("organization timezone must supply UTC offsets")
        low = int(value.timestamp())
        high = low + int((first_offset - second_offset).total_seconds()) + 1
        # TZif transitions have integral-second precision; this also handles non-hour folds.
        while low + 1 < high:
            middle = (low + high) // 2
            if datetime.fromtimestamp(middle, UTC).astimezone(tz).fold:
                low = middle
            else:
                high = middle
        end = datetime.fromtimestamp(high, UTC).astimezone(tz)
        return end.replace(tzinfo=UTC, fold=0) - timedelta(microseconds=1)
    return local.replace(tzinfo=UTC, fold=0)


def _cron(fields: Fields, now: datetime) -> crontab:
    return crontab(
        minute=fields[0],
        hour=fields[1],
        day_of_month=fields[2],
        month_of_year=fields[3],
        day_of_week=fields[4],
        nowfun=lambda: now,
    )


@dataclass(frozen=True)
class BackupSchedule:
    expression: str
    alternatives: tuple[Fields, ...]

    def due(self, last: datetime, now: datetime, tz: ZoneInfo) -> bool:
        if now.astimezone(UTC) <= last.astimezone(UTC):
            return False
        civil_now, civil_last = _civil(now, tz), _civil(last, tz)
        return any(
            _cron(fields, civil_now).remaining_estimate(civil_last).total_seconds() <= 0
            for fields in self.alternatives
        )

    def reusable(self, previous: datetime, started: datetime, now: datetime, tz: ZoneInfo) -> bool:
        return previous <= started <= now and not self.due(started, now, tz)


def _parse(value: object, now: datetime, tz: ZoneInfo) -> BackupSchedule:
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("cron must be a bounded string")
    tokens = value.lower().split()
    if len(tokens) != 5:
        raise ValueError("cron requires five fields")
    fields = (
        _field(tokens[0], 0, 59, {}),
        _field(tokens[1], 0, 23, {}),
        _field(tokens[2], 1, 31, {}),
        _field(tokens[3], 1, 12, _MONTHS),
        frozenset(day % 7 for day in _field(tokens[4], 0, 7, _DAYS)),
    )
    # Conventional cron: either restricted day field may match. A wildcard in either makes
    # the day constraints conjunctive. Celery alone always intersects these fields.
    candidates = [fields]
    if "*" not in tokens[2] and "*" not in tokens[4]:
        candidates = [
            (*fields[:4], frozenset(range(7))),
            (fields[0], fields[1], frozenset(range(1, 32)), fields[3], fields[4]),
        ]
    alternatives = []
    civil_now = _civil(now, tz)
    for candidate in candidates:
        try:
            _cron(candidate, civil_now).remaining_estimate(civil_now)
        except RuntimeError:
            # e.g. February 31: an OR schedule may still have a valid weekday branch.
            continue
        alternatives.append(candidate)
    if not alternatives:
        raise ValueError("cron has no possible calendar date")
    return BackupSchedule(" ".join(tokens), tuple(alternatives))


def resolve_schedule(value: object, *, now: datetime, tz: ZoneInfo) -> BackupSchedule:
    """Resolve once after the policy claim; malformed configuration cannot starve a sweep."""
    try:
        return _parse(value, now, tz)
    except (ValueError, TypeError, RuntimeError):
        logger.warning("invalid backup cron; using daily 02:00 in the organization timezone")
        return _parse(DEFAULT_CRON, now, tz)
