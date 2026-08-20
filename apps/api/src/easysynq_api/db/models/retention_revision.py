from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._worm_enums import (
    RetentionAuthorityKind,
    RetentionRevisionState,
    retention_authority_kind_enum,
    retention_revision_state_enum,
)


class RetentionRevision(Base):
    __tablename__ = "retention_revision"
    __table_args__ = (
        UniqueConstraint(
            "retention_policy_id",
            "revision_no",
            name="uq_retention_revision_retention_policy_id_revision_no",
        ),
        UniqueConstraint(
            "document_worm_config_id",
            "revision_no",
            name="uq_retention_revision_document_worm_config_id_revision_no",
        ),
        CheckConstraint(
            "(authority_kind = 'POLICY' AND retention_policy_id IS NOT NULL "
            "AND document_worm_config_id IS NULL) OR "
            "(authority_kind = 'INSTALLATION_MINIMUM' AND retention_policy_id IS NULL "
            "AND document_worm_config_id IS NOT NULL)",
            name="authority_shape",
        ),
        CheckConstraint("revision_no >= 1", name="revision_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    authority_kind: Mapped[RetentionAuthorityKind] = mapped_column(
        retention_authority_kind_enum, nullable=False
    )
    retention_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("retention_policy.id", ondelete="RESTRICT"), nullable=True
    )
    document_worm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "document_worm_config.id",
            ondelete="RESTRICT",
            name="fk_retention_revision_worm_config_id_config",
        ),
        nullable=True,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    active_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    proposed_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    state: Mapped[RetentionRevisionState] = mapped_column(
        retention_revision_state_enum, nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    audit_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
