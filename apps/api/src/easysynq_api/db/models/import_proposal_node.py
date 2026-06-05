"""The per-keep-item proposal (slice S-ing-3, doc 09 §8, doc 14 §13 §552).

``import_proposal_node`` is a Stage-5 output — one row per **keep-item** (an included file that is
NOT
a non-canonical duplicate and NOT a non-effective version-family member; doc 09 §8 "for every
keep-item"). Redundant/superseded files have NO node — they are represented by their
``import_dupe_cluster`` / ``import_version_family`` membership, so the keep-set + the cluster/family
rows partition the included set with nothing silently vanishing (§11.3). Like all ``import_*`` it is
**transient staging** (doc 14 §1.2) and a *suggestion only* — nothing is committed here.

``proposed_identifier`` (§8.2): a recognized doc-code is **preserved verbatim** (original casing,
``identifier_source='preserved_doc_code'``); otherwise a literal ``{type}-<new>`` sentinel
(``'suggested_default'``) flagged for the reviewer — it is **never** an allocated identifier (the
real
``{TYPE}-{AREA}-{SEQ}`` sequence is consumed only at commit, S-ing-5; the proposal never touches
``NumberingCounter``). ``target_ia_path`` (§8.1) is the proposed mirror home — ``Records/`` for a
RECORD, the ``{PHASE}/{NN}-{Word}`` clause-tree placement for a DOCUMENT (reusing the mirror layout
so
the path byte-matches the eventual mirror), ``_unmapped/`` with no clauses. ``proposed_owner`` is a
best-effort hint string (``owner_source='embedded_author'`` from the doc's embedded author; the §8.3
folder-map / process-OrgRole sources are deferred), resolved to an ``app_user`` only at
review/commit
(the ``clause_numbers``-codes-not-UUIDs precedent). ``conflict_flags`` carries §11.3 conflicts —
``duplicate_identifier_within_import`` and ``collides_with_vault_doc`` — advisory only; ``kind`` is
NEVER confirmed here (R10).

``UNIQUE (run_id, file_id)`` is the idempotency key (one node per keep-item). ``run_id``/``file_id``
FKs are ``ON DELETE CASCADE`` (the transient-layer exception, doc 14 §1.2).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ImportProposalNode(Base):
    __tablename__ = "import_proposal_node"
    __table_args__ = (
        # doc 09 §8 idempotency key — one node per keep-item (DELETE-then-INSERT re-run re-derives).
        UniqueConstraint("run_id", "file_id", name="uq_import_proposal_node_run_file"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_file.id", ondelete="CASCADE"), nullable=False
    )
    # §8.2: a preserved doc-code verbatim, OR a literal "{type}-<new>" sentinel, OR NULL (no type).
    # NEVER an allocated identifier — the {TYPE}-{AREA}-{SEQ} sequence is consumed only at commit.
    proposed_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'preserved_doc_code' | 'suggested_default' | NULL.
    identifier_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    # §8.1: the proposed mirror home ('Records/' | '{PHASE}/{NN}-{Word}' | '_unmapped/').
    target_ia_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # §8.3: a best-effort owner HINT string (resolved to app_user at review/commit), not a uuid FK.
    proposed_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'embedded_author' | NULL (folder-map / process-OrgRole sources deferred).
    owner_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    # §11.3 conflicts: {"duplicate_identifier_within_import": [<file_id>...],
    # "collides_with_vault_doc": "<documented_information_id>", "needs_identifier": true}.
    conflict_flags: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
