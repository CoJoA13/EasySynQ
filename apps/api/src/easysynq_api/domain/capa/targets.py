"""Pure CAPA target-completion-date defaults (S-capa-overdue). Severity → calendar-day offset; a
code constant for v1 (an admin-editable offset table is a deferred residual)."""

from __future__ import annotations

import datetime

from ...db.models._capa_enums import NcSeverity

CAPA_TARGET_DAYS: dict[NcSeverity, int] = {
    NcSeverity.Critical: 30,
    NcSeverity.Major: 60,
    NcSeverity.Minor: 90,
}


def default_target_date(severity: NcSeverity, raised_on: datetime.date) -> datetime.date:
    """The default target-completion date = raise date + the severity's calendar-day offset."""
    return raised_on + datetime.timedelta(days=CAPA_TARGET_DAYS[severity])
