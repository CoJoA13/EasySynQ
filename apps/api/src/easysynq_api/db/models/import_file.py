"""One inventory row per walked source file (slice S-ing-1, doc 09 §4.1, doc 14 §13 §547).

``import_file`` is the scan inventory — *transient staging* (doc 14 §1.2). Every walked path lands a
row with its scan verdict so **nothing is ever silently dropped** (doc 09 §4.2): included candidates
carry ``sha256`` + ``staged_blob_uri`` (the bytes content-addressed into the non-WORM
``import-staging``
bucket in one pass), while quarantined / excluded files carry ``scan_flags`` with a reason and are
never hashed or staged (``sha256``/``staged_blob_uri`` NULL).

``UNIQUE (run_id, rel_path)`` is the doc 09 §11.1 idempotency key — a re-delivered / resumed scan
upserts the same row, never a duplicate. ``run_id`` FK is ``ON DELETE CASCADE`` — the one deliberate
exception to the codebase's RESTRICT-everywhere, justified by doc 14 §1.2 (import_* is the
designated
transient/TTL-purged layer; an import_file is pure derived inventory with no inbound FK and no
independent bytes), so the future TTL-janitor + cancel/re-run are a one-shot purge.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ImportFile(Base):
    __tablename__ = "import_file"
    __table_args__ = (
        # doc 09 §11.1 — the idempotency / upsert key.
        UniqueConstraint("run_id", "rel_path", name="uq_import_file_run_id_rel_path"),
        # Exact-dup-by-sha256 grouping for the summary + the "already staged this sha?" probe. Plain
        # composite (nulls are cheap; the table is transient) → no env.py exclusion, alembic check
        # clean.
        Index("ix_import_file_run_id_sha256", "run_id", "sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"), nullable=False
    )
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    ext: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ctime: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL for quarantined/excluded files (never hashed). For included candidates it is the content
    # address used as the staging key.
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Canonical form ``s3://import-staging/{sha256}``; NULL when the file was not staged.
    staged_blob_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The §4.2 verdict: {"disposition": "included"|"excluded"|"quarantine", "reason": <str|null>,
    # "detail": <str>}.
    scan_flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    included_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
