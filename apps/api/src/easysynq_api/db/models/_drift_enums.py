"""Native-PG enum bindings for the drift-detection family (S-drift-2, doc 05 §9.1-§9.2, R11).

``drift_scan_kind`` starts with MIRROR (D2+D3); S-drift-3's D1 blob re-hash adds BLOB_REHASH via
``ALTER TYPE … ADD VALUE`` (the event_type additive precedent). Created by the 0046 migration;
referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class DriftScanKind(enum.Enum):
    MIRROR = "MIRROR"
    # S-drift-3: the D1 blob integrity re-hash (doc 03 §8.2, doc 05 §9.1 row D1). Added via
    # ``ALTER TYPE drift_scan_kind ADD VALUE`` in 0047 (the additive pattern; a from-scratch
    # ``upgrade head`` rebuilds the type from DRIFT_SCAN_KIND_VALUES, so the member lives here too).
    BLOB_REHASH = "BLOB_REHASH"


class DriftScanStatus(enum.Enum):
    CLEAN = "CLEAN"
    DIVERGENT = "DIVERGENT"
    FAILED = "FAILED"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


drift_scan_kind_enum = SAEnum(
    DriftScanKind, name="drift_scan_kind", values_callable=_vals, create_type=False
)
drift_scan_status_enum = SAEnum(
    DriftScanStatus, name="drift_scan_status", values_callable=_vals, create_type=False
)

# Re-used by the 0046 CREATE TYPE (and any future from-scratch ``upgrade head`` rebuild — the
# EVENT_TYPE_VALUES precedent) so the ORM and the hand-authored DDL never drift.
DRIFT_SCAN_KIND_VALUES = tuple(_vals(DriftScanKind))
DRIFT_SCAN_STATUS_VALUES = tuple(_vals(DriftScanStatus))
