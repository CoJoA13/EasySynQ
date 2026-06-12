"""management_review — a kind=DOCUMENT shared-PK subtype of documented_information (type 'MR').

management_review.id IS the documented_information.id (the quality_objective/form_template precedent).
The minutes (compiled inputs + decisions) are frozen into document_version.metadata_snapshot at submit
(NOT a column here); review_date/attendees/period_label/close_state are mutable operational state."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mgmt_review_enums import ManagementReviewCloseState, management_review_close_state_enum


class ManagementReview(Base):
    __tablename__ = "management_review"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_management_review_id_documented_information",
        ),
        primary_key=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id",
            ondelete="RESTRICT",
            name="fk_management_review_org_id_organization",
        ),
        nullable=False,
    )
    period_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    attendees: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    close_state: Mapped[ManagementReviewCloseState | None] = mapped_column(
        management_review_close_state_enum, nullable=True
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
