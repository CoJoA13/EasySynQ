"""Per-org storage configuration + the WORM-verify gate signal (slice S8b, doc 08 §7 / doc 14 §2).

S8b is intentionally minimal: it records only what gate **G-B** (object-lock WORM verified) and the
**D-7** object-lock-mode choice need. The backup/bucket/mirror columns doc 14 §2 lists
(``bucket_*``, ``sse_enabled``, ``mirror_*``) are added by **S8b2** when its restore drill needs
them — rather than creating dead columns now. 1:1 with the org (an ``id`` PK + a unique ``org_id``,
per doc 14's ERD ``ORGANIZATION ||--|| STORAGE_CONFIG``).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class StorageConfig(Base):
    __tablename__ = "storage_config"
    __table_args__ = (UniqueConstraint("org_id", name="uq_storage_config_org_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # The G-B gate signal — non-null once an object-locked probe proved early-delete is DENIED
    # (doc 08 §7.2). The constraint "non-null before any blob write" (doc 14 §2) is enforced by the
    # setup latch (no QMS writes reach the vault until finalize), not a DB check.
    worm_verified_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # D-7 record-choice: the bucket object-lock mode the operator selected. GOVERNANCE (default)
    # keeps R37 fresh-bucket restore + the R27 dual-control-destroy hatch buildable; COMPLIANCE is a
    # hardened v1.x opt-in (no plumbing in v1 — the value is recorded, not enforced here).
    object_lock_mode: Mapped[str] = mapped_column(
        Text, server_default="GOVERNANCE", default="GOVERNANCE", nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
