from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._r27_enums import R27RequestState, r27_request_state_enum


class R27Manifest(Base):
    __tablename__ = "r27_manifest"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_r27_manifest_request_id"),
        UniqueConstraint("manifest_nonce", name="uq_r27_manifest_manifest_nonce"),
        CheckConstraint("manifest_nonce ~ '^[A-Za-z0-9_-]{43}$'", name="nonce_shape"),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND excluded_set_sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_shape",
        ),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_request.id", ondelete="RESTRICT"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_nonce: Mapped[str] = mapped_column(String(43), nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    excluded_set_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expected_state: Mapped[R27RequestState] = mapped_column(r27_request_state_enum, nullable=False)
    issued_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
