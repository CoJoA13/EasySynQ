"""lifecycle: single-Effective enforcement + wired lifecycle FKs (slice S4)

Wires the lifecycle FKs that S3 left as plain nullable UUIDs and creates the two partial unique
indexes the FSM exercises (deferred from 0005 to keep that migration free of partial-index drift):

- ``document_version.superseded_by_version_id`` → ``document_version.id`` (self-FK; the supersession
  chain's forward link).
- ``documented_information.current_effective_version_id`` → ``document_version.id`` (the single
  governing Effective version). This closes a doc↔version FK cycle — safe: both columns are nullable,
  every existing S3 row has them NULL (so ``ADD CONSTRAINT`` validates cleanly), and the runtime only
  sets the pointer *after* the version row exists.
- **INV-1** ``uq_document_version_one_effective`` — at most one ``Effective`` version per document;
  the hard concurrency backstop for AC#1b.
- **R25** ``uq_doc_info_singleton_effective`` — at most one ``Effective`` singleton (Quality Policy /
  Scope Statement) per (org, type) AT A TIME.

The partial-index predicates carry the explicit ``::enum`` cast PostgreSQL stores in the index
definition (``version_state = 'Effective'::version_state`` / ``current_state =
'Effective'::document_current_state``). This is the canonical form for this codebase: the ORM
``__table_args__`` declares it identically, so ``alembic check`` reports no drift. The form was
verified against live PG ``\\d+`` output (the partial-index-on-enum drift gotcha).

Revision ID: 0007_lifecycle
Revises: 0006_seed_vault
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_lifecycle"
down_revision: str | None = "0006_seed_vault"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INV1_WHERE = "version_state = 'Effective'::version_state"
_R25_WHERE = "current_state = 'Effective'::document_current_state AND is_singleton = true"


def upgrade() -> None:
    op.create_foreign_key(
        "fk_document_version_superseded_by_version_id_document_version",
        "document_version",
        "document_version",
        ["superseded_by_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # Name is explicit + shortened (54 chars): the convention's
    # fk_{table}_{col}_{reftable} would be 71 chars, over PostgreSQL's 63-char limit.
    op.create_foreign_key(
        "fk_documented_information_current_effective_version_id",
        "documented_information",
        "document_version",
        ["current_effective_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_document_version_one_effective",
        "document_version",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text(_INV1_WHERE),
    )
    op.create_index(
        "uq_doc_info_singleton_effective",
        "documented_information",
        ["org_id", "document_type_id"],
        unique=True,
        postgresql_where=sa.text(_R25_WHERE),
    )


def downgrade() -> None:
    # DROP CONSTRAINT / DROP INDEX never validate row data, so this is unconditionally safe.
    op.drop_index("uq_doc_info_singleton_effective", table_name="documented_information")
    op.drop_index("uq_document_version_one_effective", table_name="document_version")
    op.drop_constraint(
        "fk_documented_information_current_effective_version_id",
        "documented_information",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_document_version_superseded_by_version_id_document_version",
        "document_version",
        type_="foreignkey",
    )
