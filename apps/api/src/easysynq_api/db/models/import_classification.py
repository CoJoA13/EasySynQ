"""Per-file scored classification proposal (slice S-ing-2, doc 09 §6, doc 14 §13 §549).

``import_classification`` is the Stage-3 output — one scored proposal per ``import_file`` across the
four doc 09 §6.1 dimensions (kind / type / clause_map / process_link) + the **derived** PDCA phase,
each with a confidence and a human-readable evidence list. Like all ``import_*`` it is **transient
staging** (doc 14 §1.2). It is a *suggestion only* — **nothing is confirmed or committed here**: the
human review + ``kind`` confirmation R10 requires is the S-ing-4 ``import_decision`` slice, and the
vault commit is S-ing-5.

Staging-shape choices (resolved to vault ids/rows at commit): ``kind`` is the staging
``import_kind`` enum (carries UNKNOWN); ``type_code`` is a document_type code (POL/SOP/WI/FRM) or a
record_type (AUDIT/CAPA/…), disambiguated by ``kind``; ``clause_numbers`` are clause **codes** not
UUIDs;
``process_names`` are proposed existing/new process names. ``pdca_phase`` is **derived** from the
matched requirement-node clauses (never independently guessed — §6.1). ``band`` is the row-level
review band = worst(type, clause); ``ambiguous`` flags a <10 top-2 margin on any scored dimension.

``UNIQUE (run_id, file_id, classifier_version)`` is the doc 09 §3.1 idempotency key — re-running the
SAME classifier version upserts the same row; a future version is a distinct, comparable row (§6.6).
Both ``run_id`` and ``file_id`` FKs are ``ON DELETE CASCADE`` (the transient-layer exception).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._clause_enums import PdcaPhase, pdca_phase_enum
from ._ingestion_enums import (
    ImportConfidenceBand,
    ImportKind,
    import_confidence_band_enum,
    import_kind_enum,
)


class ImportClassification(Base):
    __tablename__ = "import_classification"
    __table_args__ = (
        # doc 09 §3.1 idempotency / upsert key (+ the run_id-prefix index for batch resume).
        UniqueConstraint(
            "run_id",
            "file_id",
            "classifier_version",
            name="uq_import_classification_run_file_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_file.id", ondelete="CASCADE"), nullable=False
    )
    classifier_version: Mapped[str] = mapped_column(Text, nullable=False)
    # Dimension 1 — kind (scored only; UNKNOWN allowed; never auto-confirmed, R10).
    kind: Mapped[ImportKind] = mapped_column(import_kind_enum, nullable=False)
    kind_conf: Mapped[int] = mapped_column(Integer, nullable=False)
    # Dimension 2 — type (a document_type code or a record_type, per kind).
    type_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    type_conf: Mapped[int] = mapped_column(Integer, nullable=False)
    # Dimension 3 — clause map (M:N; clause CODES, id-resolution deferred to commit).
    clause_numbers: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'"), nullable=False
    )
    clause_conf: Mapped[int] = mapped_column(Integer, nullable=False)
    # Dimension 4 — process link (M:N; proposed process names).
    process_names: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    process_conf: Mapped[int] = mapped_column(Integer, nullable=False)
    # Derived from the matched requirement-node clauses (never guessed — §6.1). NULL when no clause.
    pdca_phase: Mapped[PdcaPhase | None] = mapped_column(pdca_phase_enum, nullable=True)
    band: Mapped[ImportConfidenceBand] = mapped_column(import_confidence_band_enum, nullable=False)
    ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False)
    top2_margin: Mapped[int] = mapped_column(Integer, nullable=False)
    # The "why" shown to Mara: [{dimension, candidate, signal_type, weight, explanation}, …].
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
