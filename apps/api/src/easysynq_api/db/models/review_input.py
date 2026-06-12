"""review_input — the compiled 9.3.2 input rows for a Management Review (mutable working projection
in Draft; frozen by the version snapshot at submit). NOT REVOKE-protected — the snapshot is the WORM
authority."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mgmt_review_enums import ReviewInputType, review_input_type_enum


class ReviewInput(Base):
    __tablename__ = "review_input"
    __table_args__ = (Index("ix_review_input_management_review_id", "management_review_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id",
            ondelete="RESTRICT",
            name="fk_review_input_org_id_organization",
        ),
        nullable=False,
    )
    management_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "management_review.id",
            ondelete="RESTRICT",
            name="fk_review_input_management_review_id_management_review",
        ),
        nullable=False,
    )
    input_type: Mapped[ReviewInputType] = mapped_column(review_input_type_enum, nullable=False)
    available: Mapped[bool] = mapped_column(nullable=False)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
