"""Native-PG enum bindings for the ``record`` subtype (slice S5, doc 14 §5.5).

``record`` is a shared-PK subtype of ``documented_information`` (``record.id`` IS the base row's
id). S5 brings the table forward so the schema is final and ``signature_event.signed_object_type =
'record'`` has a real target; record capture/disposition flows + the satellite tables
(``evidence_blob`` / ``form_template`` / …) land with the records slice (doc 06). ``record_type``
includes ``COMPLAINT`` (register R16). Created by the Alembic migration; ``create_type=False`` here.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class RecordType(enum.Enum):
    AUDIT = "AUDIT"
    AUDIT_FINDING = "AUDIT_FINDING"
    CAPA = "CAPA"
    COMPETENCE = "COMPETENCE"
    CALIBRATION = "CALIBRATION"
    MGMT_REVIEW = "MGMT_REVIEW"
    SUPPLIER_EVAL = "SUPPLIER_EVAL"
    RELEASE = "RELEASE"
    KPI_READING = "KPI_READING"
    SATISFACTION = "SATISFACTION"
    TRACEABILITY = "TRACEABILITY"
    PROPERTY_EVENT = "PROPERTY_EVENT"
    CHANGE = "CHANGE"
    EVIDENCE = "EVIDENCE"
    FILLED_FORM = "FILLED_FORM"
    COMPLAINT = "COMPLAINT"


class RecordDispositionState(enum.Enum):
    ACTIVE = "ACTIVE"
    DUE_FOR_REVIEW = "DUE_FOR_REVIEW"
    ON_HOLD = "ON_HOLD"
    DISPOSED = "DISPOSED"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


record_type_enum = SAEnum(RecordType, name="record_type", values_callable=_vals, create_type=False)
record_disposition_state_enum = SAEnum(
    RecordDispositionState,
    name="record_disposition_state",
    values_callable=_vals,
    create_type=False,
)
