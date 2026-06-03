"""process IA: the Clause 4.4 process graph + org_role/supplier + the M:N document↔process link

Creates the process-IA cluster (slice S9c, doc 02 §3.3, doc 14 §4/§6): ``org_role`` (RACI reference
data — NOT a permission role, doc 02 §3.4) and ``supplier`` (the outsourced-process counterpart, Cl
8.4) as the FK targets, then ``process`` (self-nested Clause 4.4 node, ``SEED``/``ACTIVE`` state,
nullable owner/supplier FKs), ``process_edge`` (the directed map graph; a ``CHECK`` forbids
self-loops, a ``UNIQUE`` forbids dup ordered pairs) and the audited ``process_link`` join.
``org_role``/``supplier`` are built **empty-but-present** (D-3) — no authoring endpoints in S9c.

Adds the audit values the process actions emit: ``ALTER TYPE audit_object_type ADD VALUE 'process'``
(process/edge events key here; ``process_link`` events reuse ``document``, the clause_mapping
precedent) + seven ``event_type`` values (``PROCESS_CREATED`` … ``PROCESS_UNLINKED``) — the
established additive ``ALTER TYPE … ADD VALUE`` pattern (0011-0017), in-txn-safe on PG16 (no row
uses them here). Irreversible → no-op enum downgrade (0010's downgrade DROPs the enums wholesale, so
the round-trip still passes); the Python ``EventType``/``AuditObjectType`` carry the members too, so
a from-scratch ``upgrade head`` matches a migrated DB. ``process.pdca_phase`` REUSES the
``pdca_phase`` enum owned by 0017 — downgrade must NOT drop it.

Revision ID: 0019_process_ia
Revises: 0018_seed_clauses
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_process_ia"
down_revision: str | None = "0018_seed_clauses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROCESS_STATE = ("SEED", "ACTIVE")
_SUPPLIER_STATUS = ("ACTIVE", "UNDER_EVALUATION", "INACTIVE")
_EVENT_TYPES = (
    "PROCESS_CREATED",
    "PROCESS_UPDATED",
    "PROCESS_STATE_CHANGED",
    "PROCESS_EDGE_ADDED",
    "PROCESS_EDGE_REMOVED",
    "PROCESS_LINKED",
    "PROCESS_UNLINKED",
)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _created_cols() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id"], ["organization.id"], name=f"fk_{table}_org_id_organization", ondelete="RESTRICT"
    )


def _created_by_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["created_by"], ["app_user.id"], name=f"fk_{table}_created_by_app_user", ondelete="RESTRICT"
    )


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*_PROCESS_STATE, name="process_state").create(bind, checkfirst=True)
    postgresql.ENUM(*_SUPPLIER_STATUS, name="supplier_status").create(bind, checkfirst=True)
    process_state = postgresql.ENUM(name="process_state", create_type=False)
    supplier_status = postgresql.ENUM(name="supplier_status", create_type=False)
    pdca_phase = postgresql.ENUM(name="pdca_phase", create_type=False)  # owned by 0017

    # org_role — QMS RACI reference data (not authz; empty-but-present in S9c).
    op.create_table(
        "org_role",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *_created_cols(),
        _org_fk("org_role"),
        _created_by_fk("org_role"),
        sa.PrimaryKeyConstraint("id", name="pk_org_role"),
        sa.UniqueConstraint("org_id", "name", name="uq_org_role_org_id_name"),
    )

    # supplier — outsourced-process counterpart (Cl 8.4; empty-but-present).
    op.create_table(
        "supplier",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", supplier_status, nullable=False),
        sa.Column("re_eval_due", sa.Date(), nullable=True),
        *_created_cols(),
        _org_fk("supplier"),
        _created_by_fk("supplier"),
        sa.PrimaryKeyConstraint("id", name="pk_supplier"),
    )

    # process — the Clause 4.4 node (self-nested; nullable owner/supplier FKs).
    op.create_table(
        "process",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_org_role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pdca_phase", pdca_phase, nullable=False),
        sa.Column("criteria", sa.Text(), nullable=True),
        sa.Column("state", process_state, server_default=sa.text("'SEED'"), nullable=False),
        sa.Column("excluded", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_outsourced", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("outsourced_supplier_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_created_cols(),
        _org_fk("process"),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["process.id"], name="fk_process_parent_id_process", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["owner_org_role_id"],
            ["org_role.id"],
            name="fk_process_owner_org_role_id_org_role",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outsourced_supplier_id"],
            ["supplier.id"],
            name="fk_process_outsourced_supplier_id_supplier",
            ondelete="RESTRICT",
        ),
        _created_by_fk("process"),
        sa.PrimaryKeyConstraint("id", name="pk_process"),
        sa.UniqueConstraint("org_id", "name", name="uq_process_org_id_name"),
    )

    # process_edge — the directed map graph (no self-loops, no dup ordered pairs).
    op.create_table(
        "process_edge",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("io_label", sa.Text(), nullable=True),
        *_created_cols(),
        _org_fk("process_edge"),
        sa.ForeignKeyConstraint(
            ["from_process_id"],
            ["process.id"],
            name="fk_process_edge_from_process_id_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_process_id"],
            ["process.id"],
            name="fk_process_edge_to_process_id_process",
            ondelete="RESTRICT",
        ),
        _created_by_fk("process_edge"),
        sa.PrimaryKeyConstraint("id", name="pk_process_edge"),
        sa.CheckConstraint("from_process_id <> to_process_id", name="ck_process_edge_no_self_loop"),
        sa.UniqueConstraint("from_process_id", "to_process_id", name="uq_process_edge_from_to"),
    )

    # process_link — the audited M:N document↔process join (FK named explicitly: 63-char limit).
    op.create_table(
        "process_link",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("documented_information_id", postgresql.UUID(as_uuid=True), nullable=False),
        *_created_cols(),
        _org_fk("process_link"),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["process.id"],
            name="fk_process_link_process_id_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["documented_information_id"],
            ["documented_information.id"],
            name="fk_process_link_documented_information_id",
            ondelete="RESTRICT",
        ),
        _created_by_fk("process_link"),
        sa.PrimaryKeyConstraint("id", name="pk_process_link"),
        sa.UniqueConstraint(
            "process_id", "documented_information_id", name="uq_process_link_process_doc"
        ),
    )
    op.create_index(
        "ix_process_link_documented_information_id",
        "process_link",
        ["documented_information_id"],
    )

    # The process audit values (additive enums, the 0011-0017 pattern — no row USES them here).
    op.execute("ALTER TYPE audit_object_type ADD VALUE IF NOT EXISTS 'process'")
    for value in _EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_index("ix_process_link_documented_information_id", table_name="process_link")
    op.drop_table("process_link")
    op.drop_table("process_edge")
    op.drop_table("process")
    op.drop_table("supplier")
    op.drop_table("org_role")
    postgresql.ENUM(name="process_state").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="supplier_status").drop(op.get_bind(), checkfirst=True)
    # pdca_phase is owned by 0017 (not dropped here). The audit_object_type/event_type ADD VALUEs
    # are irreversible in PostgreSQL → no-op (0010's downgrade DROPs both enums wholesale at base).
