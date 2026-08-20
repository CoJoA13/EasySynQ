from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class R27AuthorizerKey(Base):
    __tablename__ = "r27_authorizer_key"
    __table_args__ = (
        UniqueConstraint("key_id", name="uq_r27_authorizer_key_key_id"),
        UniqueConstraint("fingerprint", name="uq_r27_authorizer_key_fingerprint"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_shape"),
        CheckConstraint(
            "installed_by_identity ~ '[^[:space:]]'", name="installed_by_identity_nonblank"
        ),
        CheckConstraint(
            "(retired_at IS NULL OR retired_at>=active_at) "
            "AND (revoked_at IS NULL OR revoked_at>=active_at) "
            "AND (retired_at IS NULL OR revoked_at IS NULL OR revoked_at>=retired_at)",
            name="lifecycle_monotone",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    active_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    installed_by_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    installed_audit_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
