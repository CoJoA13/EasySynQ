"""S-rec-1 unit proofs — the records enum bindings + the additive RECORD_* audit members (no DB)."""

from __future__ import annotations

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._evidence_enums import (
    EVIDENCE_FOR_TARGET_TYPE_VALUES,
    EvidenceForTargetType,
)
from easysynq_api.db.models._record_enums import RecordDispositionState, RecordType
from easysynq_api.db.models._retention_enums import (
    DISPOSITION_ACTION_VALUES,
    RETENTION_BASIS_VALUES,
    DispositionAction,
    RetentionBasis,
)

# The 16 record types (doc 06 §2 / R16 COMPLAINT) — all accepted as flat captures in S-rec-1.
_RECORD_TYPES = {
    "AUDIT",
    "AUDIT_FINDING",
    "CAPA",
    "COMPETENCE",
    "CALIBRATION",
    "MGMT_REVIEW",
    "SUPPLIER_EVAL",
    "RELEASE",
    "KPI_READING",
    "SATISFACTION",
    "TRACEABILITY",
    "PROPERTY_EVENT",
    "CHANGE",
    "EVIDENCE",
    "FILLED_FORM",
    "COMPLAINT",
}


def test_all_16_record_types_present() -> None:
    assert {m.value for m in RecordType} == _RECORD_TYPES
    assert len(_RECORD_TYPES) == 16


def test_record_disposition_states() -> None:
    assert [m.value for m in RecordDispositionState] == [
        "ACTIVE",
        "DUE_FOR_REVIEW",
        "ON_HOLD",
        "DISPOSED",
    ]


def test_retention_basis_values() -> None:
    # The event:* labels carry the namespace verbatim from doc 06 §5.1.
    assert RETENTION_BASIS_VALUES == (
        "captured_at",
        "event:employment_end",
        "event:product_eol",
        "event:contract_end",
        "event:capa_closed",
    )
    assert RetentionBasis.CAPTURED_AT.value == "captured_at"
    assert RetentionBasis.EMPLOYMENT_END.value == "event:employment_end"


def test_disposition_action_values() -> None:
    assert DISPOSITION_ACTION_VALUES == ("DESTROY", "ARCHIVE_COLD", "TRANSFER", "RETAIN_PERMANENT")
    assert DispositionAction.RETAIN_PERMANENT.value == "RETAIN_PERMANENT"


def test_evidence_for_target_type_values() -> None:
    # The full doc-14 §5.5 set (finding/capa_stage reserved for future slices — the API accepts
    # only clause/process/document this slice).
    assert EVIDENCE_FOR_TARGET_TYPE_VALUES == (
        "finding",
        "capa_stage",
        "clause",
        "process",
        "document",
    )
    assert {EvidenceForTargetType.CLAUSE.value, EvidenceForTargetType.PROCESS.value} <= set(
        EVIDENCE_FOR_TARGET_TYPE_VALUES
    )


def test_record_event_type_members() -> None:
    # Must match the 0023 ALTER TYPE event_type ADD VALUE set (additive 0011-0022 pattern).
    expected = {
        "RECORD_CAPTURED",
        "RECORD_CORRECTED",
        "RECORD_EVIDENCE_LINKED",
        "RECORD_EVIDENCE_UNLINKED",
    }
    assert expected <= {m.value for m in EventType}


def test_record_audit_object_type_exists() -> None:
    # record events key on object_type='record' — already in the closed set (no ALTER needed).
    assert AuditObjectType.record.value == "record"


def test_structured_form_event_type_members() -> None:
    # S-rec-3 (0027 ALTER TYPE event_type ADD VALUE): FORM_SCHEMA_SET (object_type=document) +
    # CONFIG_UPDATED (object_type=config). The from-scratch upgrade rebuilds the type from these.
    assert {"FORM_SCHEMA_SET", "CONFIG_UPDATED"} <= {m.value for m in EventType}
    # config is already in the CLOSED audit_object_type set (no ALTER needed for the toggle event).
    assert AuditObjectType.config.value == "config"
