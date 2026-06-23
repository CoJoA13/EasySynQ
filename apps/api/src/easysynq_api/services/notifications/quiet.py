"""Quiet hours + the org-gated escalation pierce (spec §6). Pure: gate the IMMEDIATE email path —
an immediate email whose send-time falls in the recipient's quiet window is held to window_end
(unless a critical-class event pierces, when the org flag is on). Digests are user-timed and not
gated here."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from .classes import NotificationClass
from .preferences import EffectivePrefs


def _local(eff: EffectivePrefs, now: datetime.datetime) -> datetime.datetime:
    return now.astimezone(ZoneInfo(eff.timezone))


def in_quiet_window(eff: EffectivePrefs, now: datetime.datetime) -> bool:
    if eff.quiet_start is None or eff.quiet_end is None:
        return False
    t = _local(eff, now).time()
    start, end = eff.quiet_start, eff.quiet_end
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # wrap-around past midnight


def window_end(eff: EffectivePrefs, now: datetime.datetime) -> datetime.datetime:
    """The next occurrence of quiet_end (in the user's tz) at or after `now`, as UTC."""
    if eff.quiet_end is None:
        msg = "quiet_end must not be None"
        raise ValueError(msg)
    local = _local(eff, now)
    candidate = local.replace(
        hour=eff.quiet_end.hour, minute=eff.quiet_end.minute, second=0, microsecond=0
    )
    if candidate <= local:
        candidate = candidate + datetime.timedelta(days=1)
    return candidate.astimezone(datetime.UTC)


def should_pierce(klass: NotificationClass, org_pierce_enabled: bool) -> bool:
    return klass is NotificationClass.CRITICAL and org_pierce_enabled
