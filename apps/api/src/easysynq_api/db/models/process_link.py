"""The M:N document↔process join — an *audited link* (slice S9c, doc 02 §6.2, doc 14 §4).

A ``process_link`` records that a ``documented_information`` artifact serves / belongs to a
``process`` — the join that drives the Process Map lens and (S9d) the by-process mirror index. M:N
(one document → several processes; one process → several documents). Mapping/unmapping is audited
(``PROCESS_LINKED`` / ``PROCESS_UNLINKED``, object_type ``document`` — the link is *about the
document*, the S9 ``clause_mapping`` precedent).

The ``documented_information_id`` FK is named explicitly — the naming-convention default
(``fk_process_link_documented_information_id_documented_information``) is 64 chars > PG's 63-char
identifier limit (the ``clause_mapping.py`` precedent).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ProcessLink(Base):
    __tablename__ = "process_link"
    __table_args__ = (
        UniqueConstraint(
            "process_id", "documented_information_id", name="uq_process_link_process_doc"
        ),
        Index("ix_process_link_documented_information_id", "documented_information_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    process_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=False
    )
    documented_information_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_process_link_documented_information_id",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
