"""The append-only, hash-chained ``audit_event`` table (slice S6, doc 12 §4.2, doc 14 §12).

The spine of ISO-9001 traceability: every security- and content-relevant action writes exactly one
immutable, attributed row **in the same transaction** as the change it records (doc 12 §4.4). The
table is **monthly RANGE-partitioned on ``occurred_at``** and its ``id`` is a single
``bigint GENERATED ALWAYS AS IDENTITY`` sequence shared across all partitions, so a gap in ``id`` is
itself a tamper signal (C4/R7 — there is deliberately **no ``seq`` column and no advisory-lock
trigger**). ``prev_hash``/``row_hash``/``chained_at`` are **NULL until linked**: the decoupled,
single-threaded chain-linker (R12) is the only writer that ever populates them, off the request
hot path. ``signature_event_id`` is the reserved Part-11 hook (NULL in v1).

Partitioning, the IDENTITY clause, and the INSERT/SELECT-only DB grants are applied by the
hand-authored ``0010`` migration (autogenerate cannot model them); this ORM model mirrors the
columns, the composite ``(id, occurred_at)`` PK (PG requires the partition key in the PK), and the
BRIN + btree indexes so ``alembic check`` stays clean. The monthly child partitions
(``audit_event_YYYY_MM``) are excluded from autogenerate in ``migrations/env.py``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    Text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._audit_enums import (
    ActorType,
    AuditObjectType,
    EventType,
    actor_type_enum,
    audit_object_type_enum,
    event_type_enum,
)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_audit_event"),
        Index("brin_audit_event_occurred_at", "occurred_at", postgresql_using="brin"),
        Index("ix_audit_event_object_id", "object_id"),
        Index("ix_audit_event_actor_id", "actor_id"),
        Index("ix_audit_event_event_type", "event_type"),
    )

    # Single shared sequence across all partitions → globally monotonic; gap = tamper signal.
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    # Partition key — UTC server clock (R8). Part of the PK (PG partitioning requirement).
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    actor_type: Mapped[ActorType] = mapped_column(actor_type_enum, nullable=False)
    # Reserved for delegated/impersonation — must be empty in v1 (doc 12 §4.2); NOT hashed.
    on_behalf_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[EventType] = mapped_column(event_type_enum, nullable=False)
    object_type: Mapped[AuditObjectType] = mapped_column(audit_object_type_enum, nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scope_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mandatory Change Reason for content-changing events (enforced at the PEP / service layer).
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Chain-link columns — NULL until the single-threaded linker stamps them (R12).
    prev_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    row_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    chained_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Reserved Part-11 hook — NULL in v1 (doc 12 §4.2 / §11.2).
    signature_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signature_event.id", ondelete="RESTRICT"),
        nullable=True,
    )
