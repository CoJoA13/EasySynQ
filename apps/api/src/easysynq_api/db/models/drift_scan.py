"""The per-scan drift summary (S-drift-2, doc 05 §9.2 "write scan summary"; R11).

Family-generic (owner fork §0.3): kind=MIRROR now; S-drift-3's D1 blob re-hash reuses it with an
additive BLOB_REHASH kind, and the S-drift-3 admin drift-status surface reads latest-per-kind via
``ix_drift_scan_kind_started_at``. Written ONCE at scan terminal (write-once by code — the
tamper-evident record is the audit trail; this is the queryable operational summary). ``counts``:
{scanned, ok, stale, tampered, extra, missing, symlink_divergent, quarantined, errors, build_name,
is_current, baseline, scan_id, rebuild_triggered}.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._drift_enums import (
    DriftScanKind,
    DriftScanStatus,
    drift_scan_kind_enum,
    drift_scan_status_enum,
)


class DriftScan(Base):
    __tablename__ = "drift_scan"
    __table_args__ = (Index("ix_drift_scan_kind_started_at", "kind", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[DriftScanKind] = mapped_column(drift_scan_kind_enum, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[DriftScanStatus] = mapped_column(drift_scan_status_enum, nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)  # 'beat' | 'sync' | 'cli'
