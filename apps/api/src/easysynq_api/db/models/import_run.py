"""The ingestion run header / state machine (slice S-ing-1, doc 09 §3.2, doc 14 §13 §546).

An ``import_run`` is a first-class, audited object (doc 09 §3.2): the operator points it at a
read-only mounted source tree and the worker inventories every file into ``import_file`` —
*transient
staging* (doc 14 §1.2: only the future commit slice writes the vault). S-ing-1 drives
``Created → Scanning → Scanned`` (+ ``Failed``/``Cancelled``); ``ocr_enabled`` /
``classifier_version``
/ ``committed_by`` are accepted/reserved now (they're in the doc 14 §13 column list) and consumed by
later slices (extract / classify / commit).

The Redis source-root lock ``import:src:{hash(org_id|source_root)}`` is the single authority for
"one active run per root" (doc 09 §3.3): ``SET NX EX`` is atomic so a 2nd concurrent create gets
409,
no DB constraint needed. ``lock_token`` mirrors the opaque acquire token so the worker / cancel can
CAS-release it (the ``working_draft.checked_out_by`` mirror precedent); ``source_root_hash`` stores
the
key hash so release/reaper never re-derive it. ``scan_started_at`` drives the stalled-scan Beat
reaper
(the ``evidence_pack.build_started_at`` precedent).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ingestion_enums import ImportRunStatus, import_run_status_enum


class ImportRun(Base):
    __tablename__ = "import_run"
    __table_args__ = (
        # The stalled-scan reaper sweeps Scanning runs whose scan_started_at is older than the stall
        # window (FOR UPDATE SKIP LOCKED) — a plain composite b-tree (no expression/partial → no
        # env.py exclusion, alembic check clean).
        Index("ix_import_run_status_scan_started_at", "status", "scan_started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    # The validated source path (relative to / within the configured IMPORT_SOURCE_ROOT mount). Only
    # the *relative* rel_path is stored per file (doc 09 §4.1 — no host secrets); the run carries
    # the
    # one admin-visible root.
    source_root: Mapped[str] = mapped_column(Text, nullable=False)
    source_root_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ImportRunStatus] = mapped_column(
        import_run_status_enum, server_default=text("'Created'"), nullable=False
    )
    lock_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Run-config knobs captured at creation; persisted-but-unused in S-ing-1 (consumed at slice
    # 2/3).
    ocr_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    classifier_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The calm inventory summary (doc 09 §4.3) — materialized at scan-complete via SQL aggregates.
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    # S-ing-5: who clicked Commit (set on the API Reviewing/Proposed→Committing flip). The detached
    # commit worker carries this as the import_baseline signature's signer + the provenance
    # decided_by.
    committed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    scan_started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # S-ing-5: the commit-stage progress anchor. Commit holds NO source-root lock (freed at
    # Proposed),
    # so the dedicated reap_stalled_commits uses progress-liveness: stall =
    # now - COALESCE(MAX(import_commit_result.committed_at), committing_started_at) > stall_seconds
    # →
    # RE-ENQUEUE (never FAIL). Set when the API flips the run to Committing.
    committing_started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # S-ing-5: the immutable §12.1 Import Report Record (a RETAIN_PERMANENT EVIDENCE record)
    # produced
    # at commit completion; the mirror enumerates this to export current/_ImportReport/. RESTRICT —
    # the
    # report is never disposed.
    report_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
