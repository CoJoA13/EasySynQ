"""evidence packs: evidence_pack + pack_item + PACK_* events + evidence_pack object_type (S-pack-1)

Stands up the Evidence Packs (UJ-7) subsystem (doc 06 §7): a first-class, scope-limited, immutable
audit bundle of records + their evidence + a traceability manifest, sealed and registered as an
EVIDENCE-type Record.

1. **evidence_pack** — the pack header (doc 06 §7): scope_kind + scope_selector (+ a date overlay),
   the build ``status`` (DRAFT→BUILDING→SEALED|FAILED), the gap/exclusion summaries, the
   domain-separated manifest ``content_hash``, and the pointers to the sealed artefact
   (``zip_blob_sha256`` — plain Text, NO FK; the ZIP is reached via ``pack_record_id → evidence_blob``,
   so the pack's R27 WORM-destroy hatch never aborts on a RESTRICT FK to ``blob``).
2. **pack_item** — the resolved membership (RECORD + its PINNED DOCUMENT_VERSION items), each carrying
   the R28 ``inclusion_status`` (INCLUDED / EXCLUDED_PERMISSION / EXCLUDED_ABSENCE — the exclusion
   report IS this table). ``pack_id`` is ON DELETE CASCADE (derived membership, no independent bytes).
3. **PACK_GENERATED / PACK_BUILD_FAILED event_type** + the **evidence_pack audit_object_type** —
   additive ``ALTER TYPE … ADD VALUE`` (the 0011-0024 pattern; pack lifecycle events key on the
   ``evidence_pack`` header id, the pre-seal failed-build has no record id yet).
4. **Explicit GRANTs** — SELECT/INSERT/UPDATE/DELETE on the two new tables to ``easysynq_app`` (the
   pack-build worker + the API run on the non-owner app role). Belt-and-suspenders over 0010's ALTER
   DEFAULT PRIVILEGES (the 0024 child-table precedent), guarded so a role-less CI DB doesn't error.

Migration notes: the four new native enum types are ``CREATE TYPE`` (not ``ALTER … ADD VALUE``), so
their values are usable in the same transaction (no row uses them here anyway). Both indexes are plain
b-tree → no ``env.py`` change. FK names follow ``fk_{table}_{col}_{target}`` (all verified ≤63 chars).
The ADD VALUEs are never used by a row in this migration (PG16 in-txn rule satisfied).

Revision ID: 0025_evidence_packs
Revises: 0024_records_disposition
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._pack_enums import (
    PACK_INCLUSION_STATUS_VALUES,
    PACK_ITEM_TYPE_VALUES,
    PACK_SCOPE_KIND_VALUES,
    PACK_STATUS_VALUES,
)

revision: str = "0025_evidence_packs"
down_revision: str | None = "0024_records_disposition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

# The value tuples come from the ORM enum module (the 0023 precedent) so the hand-authored CREATE TYPE
# and the ORM SAEnum bindings can never drift (alembic check cannot catch a CREATE-TYPE label change).
_ENUMS: dict[str, tuple[str, ...]] = {
    "pack_scope_kind": PACK_SCOPE_KIND_VALUES,
    "pack_status": PACK_STATUS_VALUES,
    "pack_item_type": PACK_ITEM_TYPE_VALUES,
    "pack_inclusion_status": PACK_INCLUSION_STATUS_VALUES,
}

_NEW_EVENT_TYPES = ("PACK_GENERATED", "PACK_BUILD_FAILED")
_NEW_OBJECT_TYPES = ("evidence_pack",)


def _org_fk(table: str, column: str = "org_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["organization.id"], name=f"fk_{table}_{column}_organization", ondelete="RESTRICT"
    )


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["app_user.id"], name=f"fk_{table}_{column}_app_user", ondelete="RESTRICT"
    )


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    pack_scope_kind = postgresql.ENUM(name="pack_scope_kind", create_type=False)
    pack_status = postgresql.ENUM(name="pack_status", create_type=False)
    pack_item_type = postgresql.ENUM(name="pack_item_type", create_type=False)
    pack_inclusion_status = postgresql.ENUM(name="pack_inclusion_status", create_type=False)

    # 1. evidence_pack — the pack header.
    op.create_table(
        "evidence_pack",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("framework_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("scope_kind", pack_scope_kind, nullable=False),
        sa.Column("scope_selector", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("status", pack_status, server_default=sa.text("'DRAFT'"), nullable=False),
        sa.Column("build_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("item_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("gap_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("exclusion_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("zip_blob_sha256", sa.Text(), nullable=True),
        sa.Column("pack_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        _org_fk("evidence_pack"),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["framework.id"],
            name="fk_evidence_pack_framework_id_framework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pack_record_id"],
            ["record.id"],
            name="fk_evidence_pack_pack_record_id_record",
            ondelete="RESTRICT",
        ),
        _user_fk("evidence_pack", "created_by"),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_pack"),
    )
    op.create_index("ix_evidence_pack_org_id_status", "evidence_pack", ["org_id", "status"])

    # 2. pack_item — the resolved membership (RECORD + pinned DOCUMENT_VERSION items, R28-classified).
    op.create_table(
        "pack_item",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_type", pack_item_type, nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("inclusion_status", pack_inclusion_status, nullable=False),
        sa.Column("exclusion_reason", sa.Text(), nullable=True),
        sa.Column("content_hash_at_seal", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("pack_item"),
        sa.ForeignKeyConstraint(
            ["pack_id"],
            ["evidence_pack.id"],
            name="fk_pack_item_pack_id_evidence_pack",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"], ["record.id"], name="fk_pack_item_record_id_record", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_version.id"],
            name="fk_pack_item_version_id_document_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pack_item"),
    )
    op.create_index("ix_pack_item_pack_id", "pack_item", ["pack_id"])

    # 3. Additive enum values (never used by a row in this migration; PG16 in-txn rule satisfied).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")
    for value in _NEW_OBJECT_TYPES:
        op.execute(f"ALTER TYPE audit_object_type ADD VALUE IF NOT EXISTS '{value}'")

    # 4. Explicit least-privilege grants for the non-owner app role (the pack-build worker + the API
    #    run on database_url). Guarded so a from-scratch CI DB without the role separation doesn't error.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON evidence_pack TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON pack_item TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # pack_item → evidence_pack (CASCADE) but clear explicitly for the populated-DB downgrade; the
    # pack EVIDENCE record/blob rows the worker created are NOT this migration's rows (the records
    # subsystem owns them under its own FKs) → left standing, harmless on re-upgrade.
    op.execute("DELETE FROM pack_item")
    op.execute("DELETE FROM evidence_pack")
    op.drop_index("ix_pack_item_pack_id", table_name="pack_item")
    op.drop_table("pack_item")
    op.drop_index("ix_evidence_pack_org_id_status", table_name="evidence_pack")
    op.drop_table("evidence_pack")
    for name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")
    # The event_type / audit_object_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's
    # downgrade DROPs those types wholesale, so the up↔down round-trip still passes; a re-upgrade
    # rebuilds them from the ORM *_VALUES).
