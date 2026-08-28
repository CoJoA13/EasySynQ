"""Shared response-serialization helpers (audit U27).

``iso`` was defined three times with an identical body — twice in ``api/`` serializers and once in
a notifications service. One definition means one place to change if the wire format for a
nullable timestamp ever needs to move (a ``Z`` suffix, second precision, anything).
"""

from __future__ import annotations

import datetime


def iso(value: datetime.datetime | None) -> str | None:
    """A nullable timestamp as its ISO-8601 string — ``None`` passes through untouched."""
    return value.isoformat() if value is not None else None
