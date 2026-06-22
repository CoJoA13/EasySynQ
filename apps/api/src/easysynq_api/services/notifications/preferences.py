"""Effective notification preferences (spec §4). Resolves the per-class email cadence (a NULL
column ⇒ the code default), the digest hour/timezone, and quiet hours into one frozen view used by
the engine and the GET response. A missing NotificationPreference row ⇒ all defaults."""

from __future__ import annotations

import dataclasses
import datetime

from ...db.models._notification_enums import NotificationDigestMode
from ...db.models.notification import NotificationPreference
from .classes import NotificationClass, default_mode

_COLUMN_FOR_CLASS: dict[NotificationClass, str] = {
    NotificationClass.ACTION_REQUIRED: "digest_mode_action_required",
    NotificationClass.AWARENESS: "digest_mode_awareness",
    NotificationClass.CRITICAL: "digest_mode_critical",
    NotificationClass.ADMIN_OPS: "digest_mode_admin_ops",
}


@dataclasses.dataclass(frozen=True)
class EffectivePrefs:
    email_enabled: bool
    modes: dict[NotificationClass, NotificationDigestMode]
    digest_hour: int
    timezone: str
    quiet_start: datetime.time | None
    quiet_end: datetime.time | None


def effective_preferences(pref: NotificationPreference | None) -> EffectivePrefs:
    modes: dict[NotificationClass, NotificationDigestMode] = {}
    for klass, column in _COLUMN_FOR_CLASS.items():
        value = getattr(pref, column, None) if pref is not None else None
        modes[klass] = value if value is not None else default_mode(klass)
    return EffectivePrefs(
        email_enabled=pref.email_enabled if pref is not None else True,
        modes=modes,
        digest_hour=pref.digest_hour if pref is not None else 8,
        timezone=pref.timezone if pref is not None else "UTC",
        quiet_start=pref.quiet_start if pref is not None else None,
        quiet_end=pref.quiet_end if pref is not None else None,
    )
