"""Retention schedule catalog (doc 14 §10 / doc 06 §5.1).

S3 created the bare stub (id/org/name) so ``document_type.default_retention_policy_id`` resolves;
**S-rec-1** graduates it to the real *policy-as-data* schema: ``applies_to`` (the auto-attach map —
a record_type / clause / process the policy is the default for), ``basis`` (when the retention clock
starts), ``duration`` (ISO-8601 period, or the ``PERMANENT`` sentinel), ``disposition_action`` (what
happens at end-of-retention), ``review_required`` (whether a human must approve disposition), and
``worm_lock_period`` (the MinIO object-lock window, ``>= duration``).

The resolved policy is **snapshotted** onto a record at capture (``record.retention_policy_id``) — a
one-way ratchet: later edits never shorten an already-captured record's retention (doc 06 §5.2). A
seeded ``"System Default Retention"`` per org is the always-present fallback (the NOT-NULL
``record.retention_policy_id`` must always resolve); ``UNIQUE(org_id, name)`` makes the default
addressable and policy names unique per tenant.

The new NOT-NULL columns carry ``server_default``\\s frozen byte-identical to migration 0023's DDL
(``compare_server_default`` is OFF in ``env.py`` → a drift here is silent).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._retention_enums import (
    DispositionAction,
    RetentionBasis,
    disposition_action_enum,
    retention_basis_enum,
)


class RetentionPolicy(Base):
    __tablename__ = "retention_policy"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_retention_policy_org_id_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # The auto-attach map: at most one of {record_type | clause_id | process_id} per row, driving
    # the resolver's record-type / clause / process tiers (doc 06 §5.1). NULL = explicitly-assigned
    # policy with no auto-attach (e.g. a per-record override, or the system default).
    applies_to: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    basis: Mapped[RetentionBasis] = mapped_column(
        retention_basis_enum, server_default=text("'captured_at'"), nullable=False
    )
    duration: Mapped[str] = mapped_column(Text, server_default=text("'P10Y'"), nullable=False)
    disposition_action: Mapped[DispositionAction] = mapped_column(
        disposition_action_enum, server_default=text("'RETAIN_PERMANENT'"), nullable=False
    )
    review_required: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    worm_lock_period: Mapped[str | None] = mapped_column(Text, nullable=True)
    # S-rec-4 (doc 06 §5.1, doc 15 §8.16): soft-archive. A hard DELETE is blocked by 3 RESTRICT FKs
    # (record / document_type / disposition_event), so retirement = ``active=false``. An archived
    # policy stops auto-attaching to NEW captures (the resolver's record_type/clause/process tiers
    # filter ``active``), but records already pinned to it keep being swept (``due_active_records``
    # joins by id, no active filter) — so "archive + create a shorter policy" is the spec's
    # shorten-retention-for-future-only workflow (doc 06 §5.2, the one-way ratchet). The seeded
    # System Default is never archivable.
    active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), default=True, nullable=False
    )
    archived_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
