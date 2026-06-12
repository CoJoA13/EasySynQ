"""review_output — the 9.3.3 decisions/actions of a Management Review. Decision content (type/description/
owner/due) freezes into the version snapshot at submit; spawned_* + tracking columns mutate post-release."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mgmt_review_enums import ReviewOutputType, review_output_type_enum


class ReviewOutput(Base):
    __tablename__ = "review_output"
    __table_args__ = (Index("ix_review_output_management_review_id", "management_review_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT", name="fk_review_output_org_id_organization"),
        nullable=False,
    )
    management_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "management_review.id",
            ondelete="RESTRICT",
            name="fk_review_output_management_review_id_management_review",
        ),
        nullable=False,
    )
    output_type: Mapped[ReviewOutputType] = mapped_column(review_output_type_enum, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="RESTRICT", name="fk_review_output_owner_user_id_app_user"),
        nullable=True,
    )
    due_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    spawned_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="RESTRICT", name="fk_review_output_spawned_task_id_task"),
        nullable=True,
    )
    spawned_capa_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # reserved-null, no FK
    spawned_initiative_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # reserved-null
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
