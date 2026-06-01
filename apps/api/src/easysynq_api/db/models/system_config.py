"""Per-install configuration, including the first-run ``setup_state`` one-way latch.

The QMS surface is locked (``/api/v1/*`` → 423/403 setup_incomplete) until the
latch reaches ``OPERATIONAL`` (doc 08 / slice S8). ``canonical_serialize_version``
pins the audit hash-chain serializer so verify-chain stays reproducible (R12, D-4).
"""

from __future__ import annotations

import datetime
import enum
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, false, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class SetupState(enum.Enum):
    UNINITIALIZED = "UNINITIALIZED"
    IN_SETUP = "IN_SETUP"
    OPERATIONAL = "OPERATIONAL"


# The PG ENUM type is created by the Alembic migration; the model references it
# without trying to create it (create_type=False).
setup_state_enum = SAEnum(
    SetupState,
    name="setup_state",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)


class SystemConfig(Base):
    __tablename__ = "system_config"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    setup_state: Mapped[SetupState] = mapped_column(
        setup_state_enum,
        default=SetupState.UNINITIALIZED,
        nullable=False,
    )
    canonical_serialize_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    # SoD-2 relaxation flag (doc 07 §7.1): when true, the sole approver may also release
    # (the author may *never* release their own edit, regardless). Org-level; defaults strict.
    allow_approver_release: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
