"""Native-PG enum bindings for the Interested Parties register family (S-interested-parties-1; doc
14 §6, clause 4.2, R51).

The ``interested_party`` satellite rows version together under a ``kind=DOCUMENT`` ``IPR`` head (the
register-as-Document model, R49/R50/R51 — the Context register clone). Created by the Alembic
migration; referenced here with ``create_type=False``.

Enum-value canon (all lowercase — the R2/R16 lowercase precedent):
- ``interested_party_type``: ``customer``, ``regulator``, ``supplier``, ``employee``, ``owner``,
  ``community``, ``partner`` — the ISO 9001:2015 clause-4.2 spine (the relevant interested-party
  category; NOT NULL on every row). The ``classification`` analogue of clause 4.1.
- ``interested_party_influence``: ``low``, ``medium``, ``high`` — the optional relevance/influence
  axis (NULLABLE; a party may be unrated). Append-only.
- ``interested_party_status``: ``active``, ``closed`` — the lifecycle of an individual party within
  the register (a party is retired by closing it, never deleted; a new party is always ``active``).

Append-only: to change a taxonomy (e.g. add a party category) you mint NEW values, never re-letter
the existing ones — the golden test (``tests/unit/test_interested_party_register_content.py``) pins
each value tuple so an in-place edit fails CI, forcing the mint-a-new-value path.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class InterestedPartyType(enum.Enum):
    customer = "customer"
    regulator = "regulator"
    supplier = "supplier"
    employee = "employee"
    owner = "owner"
    community = "community"
    partner = "partner"


class InterestedPartyInfluence(enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class InterestedPartyStatus(enum.Enum):
    active = "active"
    closed = "closed"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


interested_party_type_enum = SAEnum(
    InterestedPartyType, name="interested_party_type", values_callable=_vals, create_type=False
)
interested_party_influence_enum = SAEnum(
    InterestedPartyInfluence,
    name="interested_party_influence",
    values_callable=_vals,
    create_type=False,
)
interested_party_status_enum = SAEnum(
    InterestedPartyStatus, name="interested_party_status", values_callable=_vals, create_type=False
)

# The canonical v1 value tuples, re-used by the migration's CREATE TYPE so the ORM and the
# hand-authored DDL never drift (the CONTEXT_CLASSIFICATION_VALUES / SCORING_METHOD_VALUES
# precedent).
INTERESTED_PARTY_TYPE_VALUES = tuple(_vals(InterestedPartyType))
INTERESTED_PARTY_INFLUENCE_VALUES = tuple(_vals(InterestedPartyInfluence))
INTERESTED_PARTY_STATUS_VALUES = tuple(_vals(InterestedPartyStatus))
