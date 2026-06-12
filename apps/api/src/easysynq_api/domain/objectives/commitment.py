"""The versioned Quality-Objective commitment (S-obj-3, clause 6.2).

``build_commitment`` produces the canonical dict that is BOTH the version's WORM source blob
(``rfc8785.dumps`` — JCS) AND the ``metadata_snapshot.objective_commitment`` fold, so the bytes and
the snapshot can never diverge (the S-rec-3 invariant). Decimals serialize as STRINGS (never float)
so the WORM bytes are exact + reproducible. ``current_value`` is the operational rollup OUTSIDE the
version and is deliberately NOT part of the commitment.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from ...db.models._objective_enums import ObjectiveDirection


def build_commitment(
    *,
    target_value: Decimal,
    unit: str,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    at_risk_threshold: Decimal | None,
    baseline_value: Decimal | None,
    policy_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "target_value": str(target_value),
        "unit": unit,
        "direction": direction.value,
        "due_date": due_date.isoformat(),
        "at_risk_threshold": str(at_risk_threshold) if at_risk_threshold is not None else None,
        "baseline_value": str(baseline_value) if baseline_value is not None else None,
        "policy_id": str(policy_id) if policy_id is not None else None,
    }
