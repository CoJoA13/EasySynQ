"""The per-document distribution list — WHO a controlled document is issued to (slice S-ack-1;
doc 04 §8.1, doc 14 §5.6, R15/R43).

An entry targets a ``user`` / ``org_role`` (v1) or a reserved ``process`` / ``folder`` kind, with a
per-entry ``ack_required``. Entries are editable issuance **config**, NOT evidence — created and
removed, never updated (grants ``SELECT, INSERT, DELETE``; change = delete + re-add, the
``document_link`` precedent). Acknowledgements deliberately carry NO FK here: entries are
deletable; the Cl 7.3 evidence must survive them.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ack_enums import DistributionTargetType, distribution_target_type_enum


class DistributionEntry(Base):
    __tablename__ = "distribution_entry"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "target_type", "target_id", name="uq_distribution_entry_target"
        ),
        Index("ix_distribution_entry_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_distribution_entry_document",
        ),
        nullable=False,
    )
    target_type: Mapped[DistributionTargetType] = mapped_column(
        distribution_target_type_enum, nullable=False
    )
    # The targeted principal's id (app_user.id / role.id; process/folder ids reserved). Polymorphic
    # over target_type — no FK by design (the workflow_instance.subject_id precedent).
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ack_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
