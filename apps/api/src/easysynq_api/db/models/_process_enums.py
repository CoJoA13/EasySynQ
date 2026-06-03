"""Native-PG enum bindings for the process-IA cluster (slice S9c, doc 02 §3.3, doc 14 §4/§6).

``process_state`` tags a process node ``SEED`` (scaffolded by the wizard / API, awaiting Mara's
confirmation) or ``ACTIVE`` (a confirmed Clause 4.4 process). ``supplier_status`` is a minimal
lifecycle for the outsourced-process supplier (doc 14 §6 leaves the values unspecified — the v1
forward-compat choice). ``process.pdca_phase`` reuses the ``pdca_phase`` enum from the clause
cluster (``_clause_enums.py``, owned by 0017). Created by the migration; ``create_type=False`` here.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ProcessState(enum.Enum):
    SEED = "SEED"
    ACTIVE = "ACTIVE"


class SupplierStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    UNDER_EVALUATION = "UNDER_EVALUATION"
    INACTIVE = "INACTIVE"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


process_state_enum = SAEnum(
    ProcessState, name="process_state", values_callable=_vals, create_type=False
)
supplier_status_enum = SAEnum(
    SupplierStatus, name="supplier_status", values_callable=_vals, create_type=False
)

PROCESS_STATE_VALUES = tuple(_vals(ProcessState))
SUPPLIER_STATUS_VALUES = tuple(_vals(SupplierStatus))
