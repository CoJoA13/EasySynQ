"""The Audit Programme — the maintained Cl 9.2 schedule container (slice S-aud-1; doc 02 §2,
doc 10 §5.2, doc 14 §9).

doc 14 lists ``audit_program`` as "a maintained document", but a programme is a lightweight
*scheduling container* (a period + coverage + a set of planned audits), not a controlled document
with renditions / clause-mappings / a mirror presence. Modelling it as a ``kind=DOCUMENT`` subtype
with no ``document_version`` would leave a version-less *Effective* document that the mirror join
silently drops but the document library would mis-list (and its detail/download paths assume a
version). So it is its OWN table (the ``audit_plan`` / ``ncr`` shape) — a deliberate divergence from
the design-critic's kind=DOCUMENT recommendation, recorded as decisions-register **R39**. The
retained *evidence* (``audit`` / ``audit_finding`` / ``capa``) stays a record subtype per doc 14.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AuditProgram(Base):
    __tablename__ = "audit_program"
    __table_args__ = (
        UniqueConstraint("org_id", "identifier", name="uq_audit_program_org_id_identifier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    identifier: Mapped[str] = mapped_column(Text, nullable=False)  # {AUDPROG}-{SEQ} (doc 04 §7)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. "2026" / "2026 H1"
    coverage: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )  # clauses/processes
    archived: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False, nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
