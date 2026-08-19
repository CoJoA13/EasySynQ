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


class RecoveryGenerationWitness(Base):
    __tablename__ = "recovery_generation_witness"
    __table_args__ = (
        UniqueConstraint(
            "key_id",
            "witness_nonce",
            name="uq_recovery_generation_witness_key_id_witness_nonce",
        ),
        UniqueConstraint(
            "manifest_sha256",
            "generation_id",
            name="uq_recovery_generation_witness_manifest_generation",
        ),
        UniqueConstraint("request_id", name="uq_recovery_generation_witness_request_id"),
        CheckConstraint("witness_nonce ~ '^[A-Za-z0-9_-]{43}$'", name="nonce_shape"),
        CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$' AND excluded_set_sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_shape",
        ),
        CheckConstraint(
            "generation_identity ~ '[^[:space:]]'",
            name="generation_identity_nonblank",
        ),
        CheckConstraint("result = 'VERIFIED'", name="result_verified"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "recovery_generation_verifier_key.id",
            ondelete="RESTRICT",
            name="fk_recovery_witness_key_id_verifier_key",
        ),
        nullable=False,
    )
    witness_nonce: Mapped[str] = mapped_column(String(43), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_request.id", ondelete="RESTRICT"), nullable=False
    )
    manifest_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    generation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    generation_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    excluded_set_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    issued_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "r27_execution.id",
            ondelete="RESTRICT",
            name="fk_recovery_witness_execution_id_r27_execution",
        ),
        nullable=True,
    )
