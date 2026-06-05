"""The Audit record subtype — a retained Cl 9.2 audit instance with a lifecycle state (S-aud-1;
doc 10 §5.1, doc 14 §14).

``audit.id`` IS the ``record.id`` (= ``documented_information.id``): a ``kind=RECORD`` shared-PK
subtype (the ``record.py`` precedent). The captured ``record`` row is immutable; only the mutable
``state`` column advances through the FSM — exactly as ``record.disposition_state`` is a mutable
lifecycle column on an otherwise-immutable record. Per-audit audit-LOG events reuse
``object_type='record'`` (``audit.id`` is a record id) so ``GET /documents/{id}/audit-events``
surfaces them — NO new ``audit_object_type`` value (decisions-register R39).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._iso_audit_enums import AuditState, audit_state_enum


class Audit(Base):
    __tablename__ = "audit"

    # Shared primary key: audit.id == record.id == documented_information.id (subtype link).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_plan.id", ondelete="RESTRICT"), nullable=False
    )
    lead_auditor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    started_at: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # MUTABLE lifecycle column (the record.disposition_state precedent). Advanced only via the
    # services/audits FSM under SELECT … FOR UPDATE.
    state: Mapped[AuditState] = mapped_column(
        audit_state_enum,
        server_default=text("'Scheduled'"),
        default=AuditState.Scheduled,
        nullable=False,
    )
