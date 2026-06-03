"""Native-PG enum bindings for the clause/IA cluster (slice S9, doc 02 §3.2, doc 14 §4).

``pdca_phase`` tags each clause with its Plan-Do-Check-Act phase (the temporal axis of doc 02 §3:
PLAN = clauses 4,5,6 + clause-7 resourcing; DO = clause-7 operating + 8; CHECK = 9; ACT = 10 —
clause 7 is deliberately *split*, so the phase rides on the clause, not the top-level number).
Created by the Alembic migration; referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class PdcaPhase(enum.Enum):
    PLAN = "PLAN"
    DO = "DO"
    CHECK = "CHECK"
    ACT = "ACT"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


pdca_phase_enum = SAEnum(PdcaPhase, name="pdca_phase", values_callable=_vals, create_type=False)

PDCA_PHASE_VALUES = tuple(_vals(PdcaPhase))
