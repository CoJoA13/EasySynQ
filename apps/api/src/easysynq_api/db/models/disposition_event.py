"""The immutable disposition record (slice S-rec-2, doc 06 §5.3, doc 14 §10).

One ``disposition_event`` row per *executed* disposition act — the tombstone that survives even when
the evidence bytes are gone. ``DISPOSED`` removes/anonymizes the blob per ``disposition_action`` but
this row (plus the record, its links, its ``content_hash``, and the full audit history) persists
forever, so an auditor can always see *that* a record existed and *that it was disposed lawfully*
(who/when/under-which-policy). Immutable — INSERT-only, no UPDATE path (the ``evidence_blob``
precedent).

Doc 14 §10 mandates the core columns (``record_id``/``action``/``approved_by``/``executed_at``/
``policy_id``/``tombstone``); S-rec-2 adds the R27 dual-control fields so a legal-order destroy is
self-describing on the immutable row: ``is_worm_destroy`` flags the pre-expiry escape hatch,
``requested_by`` is the first authorizer (``approved_by`` is the second/executing one), and
``legal_basis`` captures the documented justification. ``policy_id`` is NULLABLE — null for a
non-policy legal-order destroy; ``approved_by`` is NULLABLE — null for a system (Beat-sweep)
auto-disposition.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._retention_enums import DispositionAction, disposition_action_enum


class DispositionEvent(Base):
    __tablename__ = "disposition_event"
    __table_args__ = (Index("ix_disposition_event_record_id", "record_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[DispositionAction] = mapped_column(disposition_action_enum, nullable=False)
    tombstone: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), default=True, nullable=False
    )
    # NULL for a non-policy legal-order destroy (the R27 hatch is not policy-driven).
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("retention_policy.id", ondelete="RESTRICT"), nullable=True
    )
    # The executing/second authorizer; NULL for a system (Beat-sweep) auto-disposition.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    # The R27 first authorizer (the requester of a dual-control WORM destroy); NULL otherwise.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    is_worm_destroy: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False, nullable=False
    )
    legal_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
