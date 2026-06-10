"""S-drift-3: the two additive enum members exist in the ORM (the migration's *_VALUES source)."""

from __future__ import annotations

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.db.models._drift_enums import DRIFT_SCAN_KIND_VALUES, DriftScanKind


def test_blob_rehash_kind_member() -> None:
    assert DriftScanKind.BLOB_REHASH.value == "BLOB_REHASH"
    assert "BLOB_REHASH" in DRIFT_SCAN_KIND_VALUES


def test_blob_integrity_failed_event_member() -> None:
    assert EventType.BLOB_INTEGRITY_FAILED.value == "BLOB_INTEGRITY_FAILED"
    assert "BLOB_INTEGRITY_FAILED" in EVENT_TYPE_VALUES
