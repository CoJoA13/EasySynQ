from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._r27_enums import R27ActionKind, r27_action_kind_enum


class R27Attestation(Base):
    __tablename__ = "r27_attestation"
    __table_args__ = (
        UniqueConstraint("challenge_id", name="uq_r27_attestation_challenge_id"),
        UniqueConstraint("request_id", "action", name="uq_r27_attestation_request_id_action"),
        CheckConstraint("canonical_sha256 ~ '^[0-9a-f]{64}$'", name="canonical_sha256_shape"),
        CheckConstraint(
            "jsonb_typeof(audience) = 'array' "
            "AND jsonb_array_length(audience) > 0 "
            "AND NOT jsonb_path_exists("
            'audience, \'$[*] ? (@.type() != "string" || @ like_regex "^\\\\s*$")\''
            ")",
            name="audience_nonempty_string_array",
        ),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("r27_action_challenge.id", ondelete="RESTRICT"),
        nullable=False,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_request.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[R27ActionKind] = mapped_column(r27_action_kind_enum, nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    canonical_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    authorizer_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_authorizer_key.id", ondelete="RESTRICT"), nullable=False
    )
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    token_jti: Mapped[str] = mapped_column(String(255), nullable=False)
    audience: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    authorized_party: Mapped[str] = mapped_column(String(255), nullable=False)
    acr: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_time: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amr: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    permission_granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    issued_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
