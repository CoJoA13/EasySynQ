"""Native-PG enum bindings for the Improvement Initiatives family (slice S-improvement-1; doc 02
Cl 10.3, doc 14 §9, decisions-register R46).

Per **R46** an improvement initiative is an own-table mutable-state **workflow object** (the DCR /
**R22** doctrine), NOT a ``kind=RECORD`` immutable artifact and NOT a ``documented_information``
subtype: the mutable ``stage`` is the headline, the append-only
``improvement_initiative_stage_event`` trail is the immutable history. Because an initiative id is
NOT a record id, its events key on a
fresh ``audit_object_type='improvement_initiative'`` (the ``ncr``/``dcr`` own-table precedent).
Created by the Alembic migration (0052); referenced here with ``create_type=False``.

Enum-value canon:
- ``improvement_stage`` = ``Open``, ``InProgress``, ``Completed``, ``Closed``, ``Cancelled`` —
  title-case, mirroring ``dcr_state``.
- ``improvement_source`` = ``OFI``, ``review``, ``manual`` — ``OFI`` is upper to match
  ``FindingType.OFI``; lowercase ``review``/``manual`` extend the R2/R16 lowercase-token precedent.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ImprovementStage(enum.Enum):
    # The doc 02 Cl 10.3 continual-improvement lifecycle (the mutable headline). The full edge map
    # lives in domain/improvement/fsm.py for forward-compat. Closed / Cancelled are terminal.
    Open = "Open"
    InProgress = "InProgress"
    Completed = "Completed"
    Closed = "Closed"
    Cancelled = "Cancelled"


class ImprovementSource(enum.Enum):
    # Where an initiative was raised from. OFI = a one-click raise off an OBSERVATION/OFI finding
    # (source_link_id = the finding.id); review = an MR output spawn (source_link_id = the
    # review_output.id); manual = a standalone raise (source_link_id NULL). The spawn paths land in
    # slice 2 (S-improvement-2); the columns ship here so slice 2 is zero-migration.
    OFI = "OFI"
    review = "review"
    manual = "manual"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


improvement_stage_enum = SAEnum(
    ImprovementStage, name="improvement_stage", values_callable=_vals, create_type=False
)
improvement_source_enum = SAEnum(
    ImprovementSource, name="improvement_source", values_callable=_vals, create_type=False
)

# The canonical value tuples, re-used by the migration's CREATE TYPE so the ORM and the
# hand-authored DDL never drift (the 0010 rule / the _capa_enums precedent).
IMPROVEMENT_STAGE_VALUES = tuple(_vals(ImprovementStage))
IMPROVEMENT_SOURCE_VALUES = tuple(_vals(ImprovementSource))
