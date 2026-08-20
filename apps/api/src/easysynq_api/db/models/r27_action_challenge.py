from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._r27_enums import R27ActionKind, r27_action_kind_enum


class R27ActionChallenge(Base):
    __tablename__ = "r27_action_challenge"
    __table_args__ = (
        UniqueConstraint("issuer", "token_jti", name="uq_r27_action_challenge_issuer_token_jti"),
        UniqueConstraint("action_nonce", name="uq_r27_action_challenge_action_nonce"),
        CheckConstraint("action_nonce ~ '^[A-Za-z0-9_-]{43}$'", name="nonce_shape"),
        CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="manifest_sha256_shape",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_create"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action: Mapped[R27ActionKind] = mapped_column(r27_action_kind_enum, nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_request.id", ondelete="RESTRICT"), nullable=True
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    token_jti: Mapped[str] = mapped_column(String(255), nullable=False)
    action_nonce: Mapped[str] = mapped_column(String(43), nullable=False)
    accepted_claims: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
