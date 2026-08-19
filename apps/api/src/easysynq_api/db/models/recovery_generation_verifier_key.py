from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class RecoveryGenerationVerifierKey(Base):
    __tablename__ = "recovery_generation_verifier_key"
    __table_args__ = (
        UniqueConstraint("key_id", name="uq_recovery_generation_verifier_key_key_id"),
        UniqueConstraint("fingerprint", name="uq_recovery_generation_verifier_key_fingerprint"),
        CheckConstraint("algorithm = 'ED25519'", name="algorithm_ed25519"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_shape"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    not_before: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    installed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.id",
            ondelete="RESTRICT",
            name="fk_recovery_verifier_key_installed_by_app_user",
        ),
        nullable=False,
    )
    installed_audit_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
