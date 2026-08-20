from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class DocumentWormConfig(Base):
    __tablename__ = "document_worm_config"
    __table_args__ = (
        UniqueConstraint("org_id", name="uq_document_worm_config_org_id"),
        CheckConstraint("btrim(active_period) <> ''", name="active_period_nonblank"),
        CheckConstraint("active_revision_no >= 1", name="active_revision_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    active_period: Mapped[str] = mapped_column(Text, nullable=False)
    active_revision_no: Mapped[int] = mapped_column(
        Integer, server_default=text("1"), default=1, nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
