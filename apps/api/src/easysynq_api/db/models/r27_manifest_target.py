from __future__ import annotations

import uuid

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class R27ManifestTarget(Base):
    __tablename__ = "r27_manifest_target"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id", "target_order", name="uq_r27_manifest_target_manifest_id_target_order"
        ),
        UniqueConstraint(
            "manifest_id",
            "bucket",
            "object_key",
            "object_version_id",
            name="uq_r27_manifest_target_physical_identity",
        ),
        CheckConstraint("target_order >= 0", name="target_order_nonnegative"),
        CheckConstraint("blob_sha256 ~ '^[0-9a-f]{64}$'", name="blob_sha256_shape"),
        CheckConstraint(
            "length(object_version_id) BETWEEN 1 AND 1024",
            name="object_version_id_length",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_manifest.id", ondelete="RESTRICT"), nullable=False
    )
    target_order: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    object_version_id: Mapped[str] = mapped_column(Text, nullable=False)
