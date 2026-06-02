"""backup_policy — admin-controlled backup config + the restore-test signal (slice S8b2, doc 14 §2).

One row per org (single-org per install, D1) capturing the configured backup ``destination`` /
``cron`` / ``retention`` / encryption-key reference, plus the **persisted restore-test result** the
G-C setup gate reads. The gate (and the AC#5 proof ``test_setup_finalize_requires_restore_pass``)
passes ONLY when ``last_restore_test_result == 'PASS'`` — a configured-but-unverified backup, or a
FAILED drill (which still stamps ``last_restore_test_at``), does not satisfy it (doc 08 §8).

The drill that sets these fields runs as an async worker task (it may take minutes); finalize never
runs it inline — it reads the persisted result. ``destination`` is a filesystem/NFS path in MVP
(S3-destination + retention *pruning* + WAL/PITR stay S11/v1.x, D-6); ``wal_pitr_enabled`` is a
recorded forward-seam (default false). Retention columns are **counts** (doc 08 §8.1: 7/4/6), not
intervals — doc 14's literal "interval" wording is an artifact.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, false, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class BackupPolicy(Base):
    __tablename__ = "backup_policy"
    __table_args__ = (UniqueConstraint("org_id", name="uq_backup_policy_org_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    cron: Mapped[str] = mapped_column(Text, nullable=False)
    wal_pitr_enabled: Mapped[bool] = mapped_column(
        server_default=false(), default=False, nullable=False
    )
    retention_daily: Mapped[int] = mapped_column(Integer, server_default="7", nullable=False)
    retention_weekly: Mapped[int] = mapped_column(Integer, server_default="4", nullable=False)
    retention_monthly: Mapped[int] = mapped_column(Integer, server_default="6", nullable=False)
    alert_sink: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The restore-test signal the G-C gate reads. last_restore_test_at is stamped on BOTH PASS and
    # FAIL; the gate keys on last_restore_test_result == 'PASS' (so a FAIL never satisfies G-C).
    last_restore_test_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_restore_test_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
