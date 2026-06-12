"""build_minutes — the JSON-safe minutes dict for a Management Review's WORM source blob + snapshot.

Mirrors domain/objectives/commitment.build_commitment: every date/datetime → .isoformat(), every
Decimal/UUID inside inputs/outputs/attendees → str (the CALLER coerces nested leaves before passing
them in), so rfc8785.dumps gives exact, reproducible bytes (JCS sorts keys; non-safe leaves raise).
"""

from __future__ import annotations

import datetime
from typing import Any


def build_minutes(
    *,
    period_label: str | None,
    review_date: datetime.date | None,
    attendees: list[dict[str, Any]] | None,
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    compiled_at: datetime.datetime,
) -> dict[str, Any]:
    return {
        "period_label": period_label,
        "review_date": review_date.isoformat() if review_date is not None else None,
        "attendees": attendees or [],
        "inputs": inputs,
        "outputs": outputs,
        "compiled_at": compiled_at.isoformat(),
    }
