"""The dual-control WORM-destroy-under-legal-order request (slice S-rec-2, R27, doc 06 §5.5).

The R27 escape hatch: a WORM blob that genuinely must be destroyed *before* its object-lock expires
(a mis-import, or a binding erasure/legal order) requires **two distinct authorizers**, a captured
legal basis, and a fully-audited destruction — no single actor can ever destroy a WORM-locked or
legal-held record. This table is the two-step pending workflow (the ``dcr`` mutable-state precedent,
R22): a first actor *requests* (``requested_by`` + ``legal_basis``), a second, distinct actor
*approves+executes* (``approved_by``, enforced ``<> requested_by``), or anyone with the right may
*cancel* an open request.

State is derived from the nullable timestamps (no status enum): **open** = ``executed_at IS NULL AND
cancelled_at IS NULL``, **executed**, or **cancelled**. A partial UNIQUE on ``record_id WHERE open``
permits at most one open request per record (authored as raw DDL in 0024 + excluded from
``alembic check`` — the 0020 expression-index lesson). The ``CHECK(approved_by <> requested_by)`` is
the DB backstop for the in-service dual-control 409.

The *executed* destruction is recorded immutably in ``disposition_event`` (``is_worm_destroy=true``,
both actors, the legal basis) — this table is only the request/approval workflow.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WormDestroyRequest(Base):
    __tablename__ = "worm_destroy_request"
    __table_args__ = (
        # The naming convention (db/base.py) wraps this to
        # ``ck_worm_destroy_request_approver_neq_requester`` — pass only the bare token.
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> requested_by",
            name="approver_neq_requester",
        ),
        Index("ix_worm_destroy_request_record_id", "record_id"),
        # NOTE: the partial UNIQUE "one open request per record"
        # (``ix_worm_destroy_request_open ... WHERE executed_at IS NULL AND cancelled_at IS NULL``)
        # is created as raw DDL in migration 0024 and excluded from ``alembic check`` via
        # ``env.py._MIGRATION_MANAGED_INDEXES`` — a declarative partial index round-trips wrong
        # (PG normalises the predicate), the 0020 FTS-GIN lesson. Not modelled here on purpose.
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=False
    )
    legal_basis: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    requested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    executed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    cancelled_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
