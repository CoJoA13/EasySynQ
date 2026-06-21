"""Native-PG enum bindings for the Context register family (S-context-1; doc 14 §6, clause 4.1,
R50).

The ``context_issue`` satellite rows version together under a ``kind=DOCUMENT`` ``CTX`` head (the
register-as-Document model, R49/R50). Created by the Alembic migration; referenced here with
``create_type=False``.

Enum-value canon (all lowercase — the R2/R16 lowercase precedent):
- ``context_classification``: ``internal``, ``external`` — the ISO 9001:2015 clause-4.1 spine (the
  standard mandates external/internal issues; NOT NULL on every row).
- ``context_category``: ``strength``, ``weakness``, ``opportunity``, ``threat`` — the optional SWOT
  framing (NULLABLE; an issue can be unclassified-by-SWOT). Append-only: to change the taxonomy
  (e.g. add PESTLE) you mint NEW values, never re-letter the existing ones — the golden test
  (``tests/unit/test_context_register_content.py``) pins each value tuple so an in-place edit fails
  CI, forcing the mint-a-new-value path.
- ``context_issue_status``: ``active``, ``closed`` — the lifecycle of an individual issue within the
  register (an issue is retired by closing it, never deleted; a new issue is always ``active``).
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ContextClassification(enum.Enum):
    internal = "internal"
    external = "external"


class ContextCategory(enum.Enum):
    strength = "strength"
    weakness = "weakness"
    opportunity = "opportunity"
    threat = "threat"


class ContextIssueStatus(enum.Enum):
    active = "active"
    closed = "closed"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


context_classification_enum = SAEnum(
    ContextClassification, name="context_classification", values_callable=_vals, create_type=False
)
context_category_enum = SAEnum(
    ContextCategory, name="context_category", values_callable=_vals, create_type=False
)
context_issue_status_enum = SAEnum(
    ContextIssueStatus, name="context_issue_status", values_callable=_vals, create_type=False
)

# The canonical v1 value tuples, re-used by the migration's CREATE TYPE so the ORM and the
# hand-authored DDL never drift (the RISK_OPPORTUNITY_TYPE_VALUES / SCORING_METHOD_VALUES
# precedent).
CONTEXT_CLASSIFICATION_VALUES = tuple(_vals(ContextClassification))
CONTEXT_CATEGORY_VALUES = tuple(_vals(ContextCategory))
CONTEXT_ISSUE_STATUS_VALUES = tuple(_vals(ContextIssueStatus))
