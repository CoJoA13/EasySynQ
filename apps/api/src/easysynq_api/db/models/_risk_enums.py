"""Native-PG enum bindings for the Risk & Opportunity register family (S-risk-1; doc 14 §6,
R18/R49).

The ``risk_opportunity`` satellite rows version together under a ``kind=DOCUMENT`` ``RSK`` head (the
register-as-Document model, R49). Created by the Alembic migration; referenced here with
``create_type=False``.

Enum-value canon (all lowercase — the R2/R16 lowercase precedent):
- ``risk_opportunity_type``: ``risk``, ``opportunity``.
- ``scoring_method``: ``5x5_matrix`` — the SOLE v1 value. To change the matrix or the band
  thresholds you mint a **new** value (e.g. ``5x5_matrix_v2``), append-only: existing rows keep
  their
  ``scoring_method`` → their frozen criteria → are **never** silently re-graded (R49
  derive-and-freeze).
  The golden test (``tests/unit/test_risk_rules.py``) pins each value's criteria byte-shape so an
  in-place edit fails CI, forcing the mint-a-new-value path.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class RiskOpportunityType(enum.Enum):
    risk = "risk"
    opportunity = "opportunity"


class ScoringMethod(enum.Enum):
    # The sole v1 value (a forward-compatible, append-only enum — the supplier.status precedent).
    MATRIX_5X5 = "5x5_matrix"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


risk_opportunity_type_enum = SAEnum(
    RiskOpportunityType, name="risk_opportunity_type", values_callable=_vals, create_type=False
)
scoring_method_enum = SAEnum(
    ScoringMethod, name="scoring_method", values_callable=_vals, create_type=False
)

# The canonical v1 value tuples, re-used by the migration's CREATE TYPE so the ORM and the
# hand-authored DDL never drift (the AUDIT_STATE_VALUES / CAPA_SOURCE_VALUES precedent).
RISK_OPPORTUNITY_TYPE_VALUES = tuple(_vals(RiskOpportunityType))
SCORING_METHOD_VALUES = tuple(_vals(ScoringMethod))
