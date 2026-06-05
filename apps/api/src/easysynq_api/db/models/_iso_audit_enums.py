"""Native-PG enum bindings for the ISO internal-audit family (slice S-aud-1; doc 02 Cl 9.2,
doc 10 §5.1, doc 14 §9/§14).

Distinct from ``_audit_enums.py`` (the audit *log* — the actor/object/event vocabulary of the
tamper-evident trail). This module owns the ISO 9001 *internal-audit* lifecycle vocabulary: the
``audit`` record's state machine. ``finding_type`` / ``finding_severity`` land with S-aud-2. Created
by the Alembic migration; referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class AuditState(enum.Enum):
    # The doc 10 §5.1 / doc 14 §14 internal-audit FSM — a linear forward chain. The Closing→Closed
    # step is gated (S-aud-2: blocked while any live NC finding lacks a Closed CAPA); in S-aud-1 the
    # gate is a no-op (no findings exist yet).
    Scheduled = "Scheduled"
    Planned = "Planned"
    InProgress = "InProgress"
    FindingsDraft = "FindingsDraft"
    Reported = "Reported"
    Closing = "Closing"
    Closed = "Closed"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


audit_state_enum = SAEnum(AuditState, name="audit_state", values_callable=_vals, create_type=False)

# The canonical v1 value tuple, re-used by the migration's CREATE TYPE so the ORM and the
# hand-authored DDL never drift (the EVENT_TYPE_VALUES precedent).
AUDIT_STATE_VALUES = tuple(_vals(AuditState))
