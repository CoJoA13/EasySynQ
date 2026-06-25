"""Pure, stdlib-only strict calendar parsers (S-notify-7). Shared by the FAIL-SAFE resolver
(``resolve_working_calendar`` degrades on None) and the FAIL-LOUD editor (422s on None), so the two
can never drift. No DB, no I/O (the ``timer.py`` precedent)."""

from __future__ import annotations

import datetime
import zoneinfo


def parse_working_days(value: object) -> frozenset[int] | None:
    """A JSONB ``working_days`` value → a frozenset of ISO weekdays, else None if broken.

    A NON-EMPTY JSON array whose every element is a real int 1..7 — NOT a bool (``True``/``False``
    are ``int`` subclasses → ``int(True)==1``) and NOT a float (``int(1.9)==1``) and NOT a JSON
    string (``"67"`` is iterable → would wrongly become ``{6,7}``). Duplicates are deduped +
    ACCEPTED (``[1,1,2,7] → {1,2,7}``). None ⇒ broken (resolver → Mon-Fri; editor → 422s)."""
    if not isinstance(value, list) or not value:
        return None
    out: set[int] = set()
    for x in value:
        if isinstance(x, bool) or not isinstance(x, int) or not (1 <= x <= 7):
            return None
        out.add(x)
    return frozenset(out)


def parse_holiday(value: object) -> datetime.date | None:
    """A single holiday entry → a date, else None. Preserves the resolver's ``str()`` coercion so an
    int entry like ``20260101`` still parses (keeps ``resolve_working_calendar`` byte-identical)."""
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def is_valid_timezone(value: str) -> bool:
    """True iff ``zoneinfo.ZoneInfo(value)`` succeeds (the resolver's tz check)."""
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True
