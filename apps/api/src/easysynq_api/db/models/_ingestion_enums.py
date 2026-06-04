"""Native-PG enum bindings for the ingestion engine (slice S-ing-1, doc 09 §3, doc 14 §13).

``import_run_status`` is the import-run state machine (doc 09 §3 / doc 14 §13 §546). S-ing-1 ships
only the run-lifecycle states it can actually reach: ``Created`` → ``Scanning`` → ``Scanned`` (the
"inventory complete, no further stage implemented yet" resting checkpoint) plus the ``Failed`` /
``Cancelled`` terminals. Later ingestion slices ADD VALUE their stages (``Extracting`` …
``Committing`` … ``Completed``/``PartiallyCommitted``) via ``ALTER TYPE … ADD VALUE`` — the
project's
additive-enum pattern (the ``event_type`` 0011-0028 precedent), which keeps each slice's migration
churn-free and dodges speculative state names. Created by the Alembic migration;
``create_type=False``
here (the ORM and the hand-authored DDL never drift because both source
``IMPORT_RUN_STATUS_VALUES``).
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ImportRunStatus(enum.Enum):
    """The import-run state machine (doc 09 §3). S-ing-1 subset; later slices ADD VALUE."""

    CREATED = "Created"
    SCANNING = "Scanning"
    SCANNED = "Scanned"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


import_run_status_enum = SAEnum(
    ImportRunStatus, name="import_run_status", values_callable=_vals, create_type=False
)

# Re-used by the migration's enum-create step so the ORM and the hand-authored DDL never drift.
IMPORT_RUN_STATUS_VALUES = tuple(_vals(ImportRunStatus))
