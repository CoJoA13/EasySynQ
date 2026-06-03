"""A directed edge of the process map — an input/output relation (slice S9c, doc 02 §5.3).

A ``process_edge`` records that ``from_process_id`` feeds ``to_process_id`` (the ``io_label`` names
the input/output). Self-loops are forbidden at the DB (a ``CHECK``) and the API (409); a given
ordered pair is unique. The Process Map lens (deferred web) renders these as the graph.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ProcessEdge(Base):
    __tablename__ = "process_edge"
    # The ``ck`` naming convention wraps this token → ``ck_process_edge_no_self_loop`` (db/base.py).
    __table_args__ = (
        CheckConstraint("from_process_id <> to_process_id", name="no_self_loop"),
        UniqueConstraint("from_process_id", "to_process_id", name="uq_process_edge_from_to"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    from_process_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=False
    )
    to_process_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=False
    )
    io_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
