"""The Document Change Request (DCR) — an own table with a *mutable* ``state`` lifecycle column
(slice S-dcr-1; doc 05 §5, doc 14 §7, doc 15 §8.7, decisions-register R22/R5).

Per **R22** the DCR is a controlled **workflow object**, NOT a ``kind=RECORD`` immutable
artifact: the mutable ``state`` is the headline, the append-only ``dcr_stage_event`` trail is the
immutable history, and the "closed form retained as a record-like snapshot" is the frozen ``dcr``
row + its stage trail (no separate snapshot table — the ``worm_destroy_request`` mutable-state
precedent). Because a DCR id is NOT a record id, its events key on a fresh
``audit_object_type='dcr'`` (the ``ncr`` own-table precedent).

It carries a human ``DCR-{YYYY}-{SEQ}`` identifier (4-digit SEQ per doc 14 §7 / doc 05 §10.2;
allocated from the per-(org, "DCR", year) numbering counter). ``target_document_id`` is NULL for
a ``CREATE`` DCR and required for ``REVISE``/``RETIRE`` — enforced by the
``ck_dcr_create_iff_no_target`` biconditional CHECK (mirrored in the migration with the same bare
token).

Forward seams (NULL in S-dcr-1):
- ``source_link_type`` + ``source_link_id`` — the originating object (CAPA / finding /
  mgmt_review / risk). ``source_link_id`` is a polymorphic UUID with NO FK (the
  ``signature_event.signed_object_id`` precedent; mgmt_review/risk targets do not exist in v1).
  Captured at intake when a DCR is driven by a CAPA corrective action (the §10→§7.5 loop, wired
  in S-dcr-5).
- ``resulting_version_id`` — the immutable version this DCR produced. NO FK here: S-dcr-5 adds
  the deferred cross-FK to ``document_version`` (+ the reverse ``document_version.dcr_id``) via
  ``use_alter`` (the ``capa.origin_finding_id`` ↔ ``audit_finding`` precedent).
- ``decision`` / ``decided_by`` / ``decided_at`` — set at the approval/rejection (S-dcr-4).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._dcr_enums import (
    DcrChangeType,
    DcrReasonClass,
    DcrSourceLinkType,
    DcrState,
    dcr_change_type_enum,
    dcr_reason_class_enum,
    dcr_source_link_type_enum,
    dcr_state_enum,
)
from ._vault_enums import ChangeSignificance, change_significance_enum


class Dcr(Base):
    __tablename__ = "dcr"
    __table_args__ = (
        UniqueConstraint("org_id", "identifier", name="uq_dcr_org_id_identifier"),
        # CREATE ⟺ no target (REVISE/RETIRE require a target). Bare token — the metadata ck
        # naming convention (ck_%(table_name)s_%(constraint_name)s) expands it to
        # ck_dcr_create_iff_no_target, matching the migration's same-token constraint (the 0037
        # nc_has_severity precedent).
        CheckConstraint(
            "(change_type = 'CREATE') = (target_document_id IS NULL)",
            name="create_iff_no_target",
        ),
        Index("ix_dcr_org_id_state", "org_id", "state"),
        Index("ix_dcr_target_document_id", "target_document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL for CREATE; required for REVISE/RETIRE (the CHECK enforces the biconditional).
    target_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT"),
        nullable=True,
    )
    change_type: Mapped[DcrChangeType] = mapped_column(dcr_change_type_enum, nullable=False)
    change_significance: Mapped[ChangeSignificance] = mapped_column(
        change_significance_enum, nullable=False
    )
    reason_class: Mapped[DcrReasonClass] = mapped_column(dcr_reason_class_enum, nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Forward seam: the originating object (CAPA/finding/mgmt_review/risk). source_link_id is a
    # polymorphic UUID with NO FK (the signature_event precedent). NULL in S-dcr-1.
    source_link_type: Mapped[DcrSourceLinkType | None] = mapped_column(
        dcr_source_link_type_enum, nullable=True
    )
    source_link_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Optional scheduled effectivity (R8: stored timestamptz UTC). Flows to the version at
    # S-dcr-5.
    proposed_effective_from: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Forward seam: the version this DCR produced. NO FK in S-dcr-1 (S-dcr-5 adds the deferred
    # cross-FK).
    resulting_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    state: Mapped[DcrState] = mapped_column(
        dcr_state_enum, server_default=text("'Open'"), nullable=False
    )
    # Set at the approval/rejection decision (S-dcr-4). NULL until then.
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    decided_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
