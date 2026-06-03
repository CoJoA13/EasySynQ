"""Native-PG enum bindings for retention-policy-as-data (slice S-rec-1, doc 06 §5.1, doc 14 §10).

``retention_policy`` graduates from the S3 stub (id/org/name only) to the real doc-06 §5.1 schema:
a reusable schedule with a ``basis`` (when the clock starts), a ``duration`` (ISO-8601 /
``PERMANENT``), and a ``disposition_action`` (what happens at end-of-retention). The two closed sets
below are native PG enums (the universal project choice — every closed set is a native enum). The
``retention_basis`` labels carry the ``event:`` namespace from the spec verbatim (legal enum labels
— the value, not the member name). Created by the Alembic migration; ``create_type=False`` here.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class RetentionBasis(enum.Enum):
    """When a record's retention clock starts (doc 06 §5.1)."""

    CAPTURED_AT = "captured_at"
    EMPLOYMENT_END = "event:employment_end"
    PRODUCT_EOL = "event:product_eol"
    CONTRACT_END = "event:contract_end"
    CAPA_CLOSED = "event:capa_closed"


class DispositionAction(enum.Enum):
    """What happens to a record at end-of-retention (doc 06 §5.1)."""

    DESTROY = "DESTROY"
    ARCHIVE_COLD = "ARCHIVE_COLD"
    TRANSFER = "TRANSFER"
    RETAIN_PERMANENT = "RETAIN_PERMANENT"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


retention_basis_enum = SAEnum(
    RetentionBasis, name="retention_basis", values_callable=_vals, create_type=False
)
disposition_action_enum = SAEnum(
    DispositionAction, name="disposition_action", values_callable=_vals, create_type=False
)

# Re-used by the migration's enum-create step so the ORM and the hand-authored DDL never drift.
RETENTION_BASIS_VALUES = tuple(_vals(RetentionBasis))
DISPOSITION_ACTION_VALUES = tuple(_vals(DispositionAction))
