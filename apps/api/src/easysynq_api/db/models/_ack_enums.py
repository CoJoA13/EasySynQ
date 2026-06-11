"""Native-PG enum bindings for the distribution/acknowledgement cluster (slice S-ack-1).

``distribution_target_type`` carries all four doc-14 §5.6 kinds (R43: enum-4-accept-2 — the API
refuses ``process``/``folder`` until owner-assignment binding lands). ``ack_created_reason`` is the
doc-17 A9-resolution discriminator (release vs R15 target-entry). Created by migration 0048;
referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class DistributionTargetType(enum.Enum):
    user = "user"
    org_role = "org_role"
    process = "process"  # reserved — owner-assignment track (R43)
    folder = "folder"  # reserved — owner-assignment track (R43)


class AckCreatedReason(enum.Enum):
    release = "release"
    target_entry = "target_entry"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


distribution_target_type_enum = SAEnum(
    DistributionTargetType,
    name="distribution_target_type",
    values_callable=_vals,
    create_type=False,
)
ack_created_reason_enum = SAEnum(
    AckCreatedReason,
    name="ack_created_reason",
    values_callable=_vals,
    create_type=False,
)

# Canonical value tuples — migration 0048 sources its CREATE TYPE from these (the 0010 rule).
DISTRIBUTION_TARGET_TYPE_VALUES = tuple(_vals(DistributionTargetType))
ACK_CREATED_REASON_VALUES = tuple(_vals(AckCreatedReason))
