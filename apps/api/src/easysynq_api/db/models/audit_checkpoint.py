"""Signed audit-chain checkpoints (slice S6, doc 12 §4.3, doc 14 §12).

``audit_checkpoint`` is the periodic signed anchor ``(latest_id, latest_row_hash, timestamp)`` the
``beat`` ``checkpoint_anchor`` task writes (default ~15 min + best-effort on shutdown). Bundled in
backups and mirrored to the off-host ``audit_checkpoint_sink``, it exposes a full-history rewrite by
a privileged operator — the honest "tamper-evident, not tamper-proof" guarantee (P5). Append-only.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, LargeBinary
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AuditCheckpoint(Base):
    __tablename__ = "audit_checkpoint"
    __table_args__ = (Index("ix_audit_checkpoint_org_id_latest_id", "org_id", "latest_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    latest_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latest_row_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    app_signature: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
