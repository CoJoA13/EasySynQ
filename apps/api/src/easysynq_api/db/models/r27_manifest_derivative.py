from __future__ import annotations

import uuid

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._r27_enums import R27DerivativeKind, r27_derivative_kind_enum


class R27ManifestDerivative(Base):
    __tablename__ = "r27_manifest_derivative"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id",
            "derivative_order",
            name="uq_r27_manifest_derivative_manifest_id_derivative_order",
        ),
        UniqueConstraint(
            "manifest_id",
            "kind",
            "domain_id",
            name="uq_r27_manifest_derivative_manifest_kind_domain_id",
        ),
        CheckConstraint("derivative_order >= 0", name="derivative_order_nonnegative"),
        CheckConstraint(
            "blob_sha256 IS NULL OR blob_sha256 ~ '^[0-9a-f]{64}$'",
            name="blob_sha256_shape",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_manifest.id", ondelete="RESTRICT"), nullable=False
    )
    derivative_order: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[R27DerivativeKind] = mapped_column(r27_derivative_kind_enum, nullable=False)
    domain_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    blob_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
