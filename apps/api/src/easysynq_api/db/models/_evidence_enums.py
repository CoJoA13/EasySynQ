"""Native-PG enum binding for the evidence-for link target (slice S-rec-1, doc 06 §6, doc 14 §5.5).

``evidence_for_link`` is the audited M:N edge that promotes a Record as *evidence for* a clause /
process / document / finding / CAPA stage (the traceability chain
REQUIREMENT→PROCESS→DOCUMENT→RECORD→EVIDENCE). The target is **polymorphic** — ``target_type`` +
``target_id`` with no FK (the ``signature_event.signed_object_type`` precedent, doc 14 §8) — the
set spans tables that exist today (clause/process/document) *and* future ones
(finding/capa_stage, the CAPA/audit slices) — exactly why ``signed_object_type`` already declares
``capa_stage`` with no table yet. The enum carries the full doc-14 §5.5 set so adding finding/CAPA
linking later needs no ``ALTER TYPE``; the S-rec-1 API accepts only clause/process/document (it
validates the target exists). Created by the Alembic migration; ``create_type=False`` here.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class EvidenceForTargetType(enum.Enum):
    FINDING = "finding"  # reserved — the audit/finding slice (no table yet)
    CAPA_STAGE = "capa_stage"  # reserved — the CAPA slice (no table yet)
    CLAUSE = "clause"
    PROCESS = "process"
    DOCUMENT = "document"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


evidence_for_target_type_enum = SAEnum(
    EvidenceForTargetType,
    name="evidence_for_target_type",
    values_callable=_vals,
    create_type=False,
)

EVIDENCE_FOR_TARGET_TYPE_VALUES = tuple(_vals(EvidenceForTargetType))
