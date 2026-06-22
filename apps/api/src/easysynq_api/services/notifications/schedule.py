"""Digest scheduling (spec §5.1). Pure: compute the next daily-digest send time for a user from
their digest_hour + timezone, returned as UTC. Set on notification.digest_due_at at enqueue for
daily-mode rows; the hourly sweep sends once now >= digest_due_at."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from .preferences import EffectivePrefs


def next_digest_at(eff: EffectivePrefs, now: datetime.datetime) -> datetime.datetime:
    tz = ZoneInfo(eff.timezone)
    local = now.astimezone(tz)
    candidate = local.replace(hour=eff.digest_hour, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate = candidate + datetime.timedelta(days=1)
    return candidate.astimezone(datetime.UTC)
