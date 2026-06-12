"""Management Review enums (S-mr-1, clause 9.3). ``create_type=False`` — the 0050 migration owns
CREATE TYPE; the migration sources its CREATE-TYPE tuple from the ``*_VALUES`` constants (the 0010 rule)."""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ReviewInputType(enum.Enum):
    PRIOR_ACTIONS = "PRIOR_ACTIONS"                    # 9.3.2(a)
    CONTEXT_CHANGES = "CONTEXT_CHANGES"                # 9.3.2(b) — gap (no source)
    CUSTOMER_SATISFACTION = "CUSTOMER_SATISFACTION"    # 9.3.2(c1) — gap
    OBJECTIVES_STATUS = "OBJECTIVES_STATUS"            # 9.3.2(c2)
    PROCESS_PERFORMANCE = "PROCESS_PERFORMANCE"        # 9.3.2(c3)
    NONCONFORMITIES_CAPA = "NONCONFORMITIES_CAPA"      # 9.3.2(c4)
    MONITORING_RESULTS = "MONITORING_RESULTS"          # 9.3.2(c5)
    AUDIT_RESULTS = "AUDIT_RESULTS"                    # 9.3.2(c6)
    SUPPLIER_PERFORMANCE = "SUPPLIER_PERFORMANCE"      # 9.3.2(c7) — gap
    RESOURCE_ADEQUACY = "RESOURCE_ADEQUACY"            # 9.3.2(d) — gap
    RISK_OPPORTUNITY_ACTIONS = "RISK_OPPORTUNITY_ACTIONS"  # 9.3.2(e) — gap
    IMPROVEMENT_OPPORTUNITIES = "IMPROVEMENT_OPPORTUNITIES"  # 9.3.2(f) — gap


class ReviewOutputType(enum.Enum):
    DECISION = "DECISION"        # recorded, untracked
    ACTION = "ACTION"            # owner + due → spawns an MR_ACTION task
    IMPROVEMENT = "IMPROVEMENT"  # reserved — tagged for the deferred initiative family


class ManagementReviewCloseState(enum.Enum):
    ActionsTracked = "ActionsTracked"  # set at release; output actions in flight
    Closed = "Closed"                  # all actions done (the close gate passed)


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


REVIEW_INPUT_TYPE_VALUES = tuple(_vals(ReviewInputType))
REVIEW_OUTPUT_TYPE_VALUES = tuple(_vals(ReviewOutputType))
MANAGEMENT_REVIEW_CLOSE_STATE_VALUES = tuple(_vals(ManagementReviewCloseState))

review_input_type_enum = SAEnum(
    ReviewInputType, name="review_input_type", values_callable=_vals, create_type=False
)
review_output_type_enum = SAEnum(
    ReviewOutputType, name="review_output_type", values_callable=_vals, create_type=False
)
management_review_close_state_enum = SAEnum(
    ManagementReviewCloseState,
    name="management_review_close_state",
    values_callable=_vals,
    create_type=False,
)
