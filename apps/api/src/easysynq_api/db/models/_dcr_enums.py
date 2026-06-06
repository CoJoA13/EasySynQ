"""Native-PG enum bindings for the Document Change Request (DCR) family (slice S-dcr-1; doc 05 §5,
doc 14 §7, doc 15 §8.7, decisions-register R22/R5).

Per **R22** the DCR is a controlled **workflow object** with a *mutable* ``state`` column plus an
append-only history of stage events (``dcr_stage_event``) — NOT a ``kind=RECORD`` immutable
artifact (the ``worm_destroy_request`` mutable-state precedent, not the ``capa`` record-subtype).
Its closed form is retained as a record-like snapshot (the frozen ``dcr`` row + its stage trail),
NOT a table.

Created by the Alembic migration (0040); referenced here with ``create_type=False`` (the
``_capa_enums`` precedent). Value tuples (``*_VALUES``) are sourced from the enum members so the
DDL and the ``SAEnum`` bindings never drift.

Enum-value canon:
- ``dcr_state``: the canonical lifecycle states **verbatim** per doc 14 §7 / doc 05 §5.5 —
  Title-case
  ``Open``/``Assessed``/``Routed``/``InApproval``/``Approved``/``Implemented``/``Closed`` (+
  terminal ``Cancelled``/``Rejected``). The FSM edges live in ``domain/dcr/fsm.py``; the
  InApproval changes-requested loop targets ``Open`` (doc 15 §8.7 + owner decision — reconciling
  doc 05 §5.5's ``Routed``; recorded in decisions-register).
- ``dcr_change_type``: ``REVISE`` / ``CREATE`` / ``RETIRE`` (doc 05 §5.2, upper-case verbatim).
- ``dcr_reason_class``: the classified reason-for-change vocabulary (doc 05 §5.2, lower_snake).
- ``dcr_source_link_type``: the originating-object kind for ``source_link`` (doc 14 §7 — CAPA /
  finding / mgmt_review / risk). ``source_link_id`` is a polymorphic nullable UUID with NO FK
  (the ``signature_event.signed_object_id`` precedent): mgmt_review/risk targets do not exist in
  v1 so an FK is impossible; capa/finding already exist but stay FK-less for uniformity.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class DcrState(enum.Enum):
    # The canonical DCR lifecycle (doc 14 §7, doc 05 §5.5). Open is the intake rest-state; Closed
    # / Cancelled / Rejected are terminal. S-dcr-1's SERVICE wires only the Open intake +
    # Open→Cancelled; the full edge map is declared in domain/dcr/fsm.py for forward-compat (the
    # CapaCloseState pattern).
    Open = "Open"
    Assessed = "Assessed"
    Routed = "Routed"
    InApproval = "InApproval"
    Approved = "Approved"
    Implemented = "Implemented"
    Closed = "Closed"
    Cancelled = "Cancelled"
    Rejected = "Rejected"


class DcrChangeType(enum.Enum):
    REVISE = "REVISE"
    CREATE = "CREATE"
    RETIRE = "RETIRE"


class DcrReasonClass(enum.Enum):
    # The classified justification (doc 05 §5.2) — drives revision-history presentation + routing
    # hints.
    regulatory = "regulatory"
    audit_finding = "audit_finding"
    capa = "capa"
    process_improvement = "process_improvement"
    error_correction = "error_correction"
    periodic_review = "periodic_review"
    customer_requirement = "customer_requirement"
    other = "other"


class DcrSourceLinkType(enum.Enum):
    # The originating-object kind for source_link (doc 14 §7). capa/finding exist in v1;
    # mgmt_review/ risk are reserved forward seams (those families are not built — never written
    # until they ship).
    capa = "capa"
    finding = "finding"
    mgmt_review = "mgmt_review"
    risk = "risk"


class ImpactDimension(enum.Enum):
    # The structured impact-assessment dimensions (doc 05 §5.3, S-dcr-2) — one impact_assessment
    # row per dimension per DCR, auto-populated from the target document's where-used at assess.
    affected_processes = "affected_processes"
    dependent_documents = "dependent_documents"
    records_produced_under = "records_produced_under"
    training_awareness = "training_awareness"
    clause_coverage = "clause_coverage"
    effectivity_transition = "effectivity_transition"
    risk = "risk"


class VisualDiffStatus(enum.Enum):
    # The S-dcr-3b visual page-image diff cache result (worker-async). Pending = the task is
    # rendering/ rasterizing; Ready = page comparisons cached; Failed = a hard error; Unavailable
    # = a version is non-renderable (R26) so no page images can be produced (the text+metadata
    # diff still covers it).
    Pending = "Pending"
    Ready = "Ready"
    Failed = "Failed"
    Unavailable = "Unavailable"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


dcr_state_enum = SAEnum(DcrState, name="dcr_state", values_callable=_vals, create_type=False)
dcr_change_type_enum = SAEnum(
    DcrChangeType, name="dcr_change_type", values_callable=_vals, create_type=False
)
dcr_reason_class_enum = SAEnum(
    DcrReasonClass, name="dcr_reason_class", values_callable=_vals, create_type=False
)
dcr_source_link_type_enum = SAEnum(
    DcrSourceLinkType, name="dcr_source_link_type", values_callable=_vals, create_type=False
)
impact_dimension_enum = SAEnum(
    ImpactDimension, name="impact_dimension", values_callable=_vals, create_type=False
)
visual_diff_status_enum = SAEnum(
    VisualDiffStatus, name="visual_diff_status", values_callable=_vals, create_type=False
)

# The canonical v1 value tuples, re-used by the migration's CREATE TYPE so the ORM and the
# hand-authored DDL never drift (the _capa_enums *_VALUES precedent).
DCR_STATE_VALUES = tuple(_vals(DcrState))
DCR_CHANGE_TYPE_VALUES = tuple(_vals(DcrChangeType))
DCR_REASON_CLASS_VALUES = tuple(_vals(DcrReasonClass))
DCR_SOURCE_LINK_TYPE_VALUES = tuple(_vals(DcrSourceLinkType))
IMPACT_DIMENSION_VALUES = tuple(_vals(ImpactDimension))
VISUAL_DIFF_STATUS_VALUES = tuple(_vals(VisualDiffStatus))
