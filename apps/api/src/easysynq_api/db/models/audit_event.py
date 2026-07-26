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
import hashlib
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
from sqlalchemy.orm import Mapped, mapped_column, validates

from ..base import Base
from ._audit_enums import (
    ActorType,
    AuditObjectType,
    EventType,
    actor_type_enum,
    audit_object_type_enum,
    event_type_enum,
)

# ``scope_ref`` is indexed (``ix_audit_event_org_id_scope_ref_id``, migration 0075), and a Postgres
# btree tuple may not exceed ~1/3 of an 8 kB page — 2704 bytes. The column is unbounded ``Text`` and
# several producers interpolate caller-supplied values into it: ``pep._scope_ref`` emits
# ``f"folder:{resource.folder_path}"`` and ``DocumentCreate.folder_path`` has no length limit, while
# the vault path uses ``documented_information.identifier`` / ``legacy_identifier`` — also unbounded
# ``Text``, and an import can preserve an arbitrary legacy identifier. Without a bound, a single
# ~2.7 kB incompressible value makes the INSERT fail; because ``DbAuthzAuditSink`` commits in its
# OWN transaction, that surfaces as a **500 instead of a 403, with the denial never recorded**.
# 512 CHARACTERS is the cap, deliberately not bytes: worst-case UTF-8 is 4 bytes/char, so 512 chars
# ≤ 2048 bytes, leaving ample room for org_id (16) + id (8) + tuple overhead under the 2704 limit —
# and a char cap cannot split a multi-byte sequence the way a byte cap would.
_SCOPE_REF_MAX_CHARS = 512
_SCOPE_REF_DIGEST_CHARS = 16
_SCOPE_REF_MARKER = "…sha256:"


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_audit_event"),
        Index("brin_audit_event_occurred_at", "occurred_at", postgresql_using="brin"),
        Index("ix_audit_event_object_id", "object_id"),
        Index("ix_audit_event_actor_id", "actor_id"),
        Index("ix_audit_event_event_type", "event_type"),
        # The per-document history access path (``GET /documents/{id}/audit-events``,
        # api/audit.py::document_audit_events): ``WHERE org_id = ? AND scope_ref = ? AND id <
        # cursor ORDER BY id DESC``. Two equality columns then ``id``, which serves BOTH the keyset
        # range and the ordering — so a matching child index answers the page without a sort.
        # Created on the PARTITIONED PARENT (migration 0075), so PG propagates it to every existing
        # partition and to each future one the ``roll_partitions`` Beat adds via ``CREATE TABLE …
        # PARTITION OF``. Those auto-named children (``audit_event_YYYY_MM_*_idx``) are already
        # excluded from autogenerate by ``env.py::_include_object``'s ``audit_event_`` prefix rule;
        # this parent index is NOT (it is ``ix_``-prefixed), so it must stay modelled here or
        # ``alembic check`` phantom-DROPs it. Name follows NAMING_CONVENTION["ix"] (db/base.py) —
        # all three columns, so it cannot be misread as a bare ``scope_ref`` index (org_id leads).
        # ⚠ Building it blocks WRITES (ShareLock) but not reads — see migration 0075's operator note
        # before running an in-place upgrade against a live stack.
        Index("ix_audit_event_org_id_scope_ref_id", "org_id", "scope_ref", "id"),
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

    @validates("scope_ref")
    def _bound_scope_ref(self, _key: str, value: str | None) -> str | None:
        """Cap ``scope_ref`` so it can never exceed the btree tuple limit (see the constants above).

        Deliberately on the MODEL rather than at any producer: ``scope_ref`` is written from ~56
        call sites across the vault, authz, ingestion and workflow paths, and a bound enforced at
        one of them would silently not apply to the other 55 — or to the next one added. Firing on
        attribute set means every construction path is covered, including plain ``AuditEvent(...)``.

        Truncation keeps a readable prefix and appends a digest **of the full original**, so two
        different over-long values stay distinguishable rather than collapsing to the same prefix.
        The result is deterministic, which matters because ``scope_ref`` is an input to the audit
        hash chain (``services/audit/canonical.py``) — the stored value is what gets hashed, so a
        capped row is self-consistent. Existing rows are never rewritten (the table is append-only);
        this only bounds what is written from here on.
        """
        if value is None or len(value) <= _SCOPE_REF_MAX_CHARS:
            return value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:_SCOPE_REF_DIGEST_CHARS]
        keep = _SCOPE_REF_MAX_CHARS - len(_SCOPE_REF_MARKER) - _SCOPE_REF_DIGEST_CHARS
        return f"{value[:keep]}{_SCOPE_REF_MARKER}{digest}"
