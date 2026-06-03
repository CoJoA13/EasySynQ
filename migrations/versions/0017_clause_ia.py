"""clause IA: the ISO clause spine + the M:N document↔clause mapping (slice S9, doc 14 §4)

Creates the read-only ``clause`` reference table (clauses 4-10 + sub-clauses, self-nested via
``parent_id``, ``pdca_phase``-tagged, ★-flagged) and the audited ``clause_mapping`` join. The ISO
9001:2015 clause catalog itself is seeded by the next migration (``0018_seed_clauses``); ``clause``
is INSERT-by-seed only (no user-edit path, no ``clause.edit`` permission — doc 07 §3.6).

``clause`` carries ``framework_id`` (its org/tenant anchor — ``framework`` is org-scoped) but no
direct ``org_id`` (doc 14 §4). ``clause_mapping`` carries ``framework_id`` per the C5 canon
(framework_id NOT NULL only on documented_information/clause/clause_mapping/scope, doc 14 §15.3) +
``org_id`` (it is an artifact-linked row, org-guarded at the PEP). The
``clause_mapping.documented_information_id`` FK is named explicitly — the convention default would
exceed PG's 63-char identifier limit, the ``documented_information.py`` precedent.

Also adds the two ``event_type`` values the map/unmap actions emit (``CLAUSE_MAPPED`` /
``CLAUSE_UNMAPPED``) via the established additive ``ALTER TYPE … ADD VALUE`` pattern (0011-0016):
in-txn-safe on PG16 (no row USES the values here), irreversible → no-op enum downgrade (0010's
downgrade DROPs ``event_type`` wholesale, so the up↔down round-trip still passes); the Python
``EventType`` carries the members too, so a from-scratch ``upgrade head`` matches a migrated DB.

Revision ID: 0017_clause_ia
Revises: 0016_user_admin_events
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_clause_ia"
down_revision: str | None = "0016_user_admin_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PDCA_PHASE = ("PLAN", "DO", "CHECK", "ACT")


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*_PDCA_PHASE, name="pdca_phase").create(bind, checkfirst=True)
    pdca_phase = postgresql.ENUM(name="pdca_phase", create_type=False)

    # clause — read-only, seeded ISO requirement tree (self-nested via parent_id).
    op.create_table(
        "clause",
        _uuid_pk(),
        sa.Column("framework_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("number", sa.Text(), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("intent_text", sa.Text(), nullable=False),
        sa.Column(
            "is_mandatory_star", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("pdca_phase", pdca_phase, nullable=False),
        sa.Column("requirement_node", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["framework.id"],
            name="fk_clause_framework_id_framework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["clause.id"], name="fk_clause_parent_id_clause", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_clause"),
        sa.UniqueConstraint("framework_id", "number", name="uq_clause_framework_id_number"),
    )

    # clause_mapping — the audited M:N document↔clause join (the submit-gate counts it).
    op.create_table(
        "clause_mapping",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("framework_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("clause_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("documented_information_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "is_requirement_level", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_clause_mapping_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["framework.id"],
            name="fk_clause_mapping_framework_id_framework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clause_id"],
            ["clause.id"],
            name="fk_clause_mapping_clause_id_clause",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["documented_information_id"],
            ["documented_information.id"],
            name="fk_clause_mapping_documented_information_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_clause_mapping_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_clause_mapping"),
        sa.UniqueConstraint(
            "documented_information_id", "clause_id", name="uq_clause_mapping_doc_clause"
        ),
    )
    op.create_index(
        "ix_clause_mapping_documented_information_id",
        "clause_mapping",
        ["documented_information_id"],
    )

    # The map/unmap audit events (additive enum, the 0011-0016 pattern).
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'CLAUSE_MAPPED'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'CLAUSE_UNMAPPED'")


def downgrade() -> None:
    op.drop_index("ix_clause_mapping_documented_information_id", table_name="clause_mapping")
    op.drop_table("clause_mapping")
    op.drop_table("clause")
    postgresql.ENUM(name="pdca_phase").drop(op.get_bind(), checkfirst=True)
    # The event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0010's downgrade DROPs the
    # event_type enum wholesale, so the round-trip still passes).
