"""S9c unit proofs — the process-IA enum bindings + the additive audit members (no DB)."""

from __future__ import annotations

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._process_enums import (
    PROCESS_STATE_VALUES,
    SUPPLIER_STATUS_VALUES,
    ProcessState,
    SupplierStatus,
)


def test_process_state_values() -> None:
    assert PROCESS_STATE_VALUES == ("SEED", "ACTIVE")
    assert [m.value for m in ProcessState] == ["SEED", "ACTIVE"]


def test_supplier_status_values() -> None:
    assert SUPPLIER_STATUS_VALUES == ("ACTIVE", "UNDER_EVALUATION", "INACTIVE")
    assert [m.value for m in SupplierStatus] == ["ACTIVE", "UNDER_EVALUATION", "INACTIVE"]


def test_process_audit_object_type_member() -> None:
    # process/edge events key on object_type='process'; the value must match the 0019 ALTER TYPE.
    assert AuditObjectType.process.value == "process"


def test_process_event_type_members() -> None:
    expected = {
        "PROCESS_CREATED",
        "PROCESS_UPDATED",
        "PROCESS_STATE_CHANGED",
        "PROCESS_EDGE_ADDED",
        "PROCESS_EDGE_REMOVED",
        "PROCESS_LINKED",
        "PROCESS_UNLINKED",
    }
    present = {m.value for m in EventType}
    assert expected <= present
