"""vault: framework + document core, immutable versions, CAS blobs, check-out (slice S3)

Creates the controlled-vault spine (D2): the framework + document_type catalogs, the
content-addressed ``blob`` table, the ``documented_information`` document spine, immutable
``document_version`` snapshots, the ``working_draft`` check-out mirror, and the
``numbering_counter`` for atomic identifier allocation.

S3 produces only ``Draft`` versions — the lifecycle FSM, the Effective cutover, and the
**INV-1 single-Effective partial unique index** (plus the R25 singleton index) land in S4,
where they are exercised; deferring them keeps this migration free of partial-index drift.
``folder_path`` is dotted text in S3 (the PDP matches it in Python); the real ltree type +
GiST index are an additive later change. ``sha256`` is lowercase-hex text (content addressing).

Revision ID: 0005_vault
Revises: 0004_seed_authz
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_vault"
down_revision: str | None = "0004_seed_authz"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS: dict[str, tuple[str, ...]] = {
    "version_state": ("Draft", "InReview", "Approved", "Effective", "Superseded", "Obsolete"),
    "document_current_state": (
        "Draft",
        "InReview",
        "Approved",
        "Effective",
        "UnderRevision",
        "Superseded",
        "Obsolete",
    ),
    "document_kind": ("DOCUMENT", "RECORD"),
    "document_level": ("L1_POLICY", "L2_PROCEDURE", "L3_WORK_INSTRUCTION", "L4_FORM"),
    "change_significance": ("MAJOR", "MINOR"),
    "classification": ("Public", "Internal", "Confidential", "Restricted"),
}


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

    document_current_state = postgresql.ENUM(name="document_current_state", create_type=False)
    document_kind = postgresql.ENUM(name="document_kind", create_type=False)
    document_level = postgresql.ENUM(name="document_level", create_type=False)
    version_state = postgresql.ENUM(name="version_state", create_type=False)
    change_significance = postgresql.ENUM(name="change_significance", create_type=False)
    classification = postgresql.ENUM(name="classification", create_type=False)

    # framework — the standard a documented_information row conforms to.
    op.create_table(
        "framework",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_authorable", sa.Boolean(), server_default=sa.false(), nullable=False),
        _org_fk("framework"),
        sa.PrimaryKeyConstraint("id", name="pk_framework"),
        sa.UniqueConstraint("org_id", "code", name="uq_framework_org_id_code"),
    )

    # retention_policy — FK target only in S3 (rows are v1).
    op.create_table(
        "retention_policy",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        _org_fk("retention_policy"),
        sa.PrimaryKeyConstraint("id", name="pk_retention_policy"),
    )

    # document_type — catalog + the {TYPE} identifier token.
    op.create_table(
        "document_type",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("document_level", document_level, nullable=False),
        sa.Column("is_singleton", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("default_retention_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        _org_fk("document_type"),
        sa.ForeignKeyConstraint(
            ["default_retention_policy_id"],
            ["retention_policy.id"],
            name="fk_document_type_default_retention_policy_id_retention_policy",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_type"),
        sa.UniqueConstraint("org_id", "code", name="uq_document_type_org_id_code"),
    )

    # blob — content-addressed, deduplicated, WORM (sha256 hex PK).
    op.create_table(
        "blob",
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("worm_locked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("worm_retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sse", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("blob"),
        sa.PrimaryKeyConstraint("sha256", name="pk_blob"),
    )

    # documented_information — the document spine (kind-discriminated; record extension = S5).
    op.create_table(
        "documented_information",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("framework_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", document_kind, nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("legacy_identifier", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("document_type_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("area_code", sa.Text(), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("folder_path", sa.Text(), nullable=True),
        sa.Column("current_state", document_current_state, server_default="Draft", nullable=False),
        sa.Column("is_singleton", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("current_effective_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("classification", classification, server_default="Internal", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        _org_fk("documented_information"),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["framework.id"],
            name="fk_documented_information_framework_id_framework",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_type.id"],
            name="fk_documented_information_document_type_id_document_type",
            ondelete="RESTRICT",
        ),
        _user_fk("documented_information", "owner_user_id"),
        _user_fk("documented_information", "created_by"),
        _user_fk("documented_information", "updated_by"),
        sa.PrimaryKeyConstraint("id", name="pk_documented_information"),
        sa.UniqueConstraint(
            "org_id", "identifier", name="uq_documented_information_org_id_identifier"
        ),
    )

    # document_version — immutable snapshot (no INV-1 partial index in S3; created in S4).
    op.create_table(
        "document_version",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_seq", sa.Integer(), nullable=False),
        sa.Column("revision_label", sa.Text(), nullable=False),
        sa.Column("change_significance", change_significance, nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("version_state", version_state, server_default="Draft", nullable=False),
        sa.Column("source_blob_sha256", sa.Text(), nullable=False),
        sa.Column("metadata_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rendition_blob_sha256", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("imported", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        _org_fk("document_version"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documented_information.id"],
            name="fk_document_version_document_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_blob_sha256"],
            ["blob.sha256"],
            name="fk_document_version_source_blob_sha256_blob",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rendition_blob_sha256"],
            ["blob.sha256"],
            name="fk_document_version_rendition_blob_sha256_blob",
            ondelete="RESTRICT",
        ),
        _user_fk("document_version", "author_user_id"),
        _user_fk("document_version", "created_by"),
        sa.PrimaryKeyConstraint("id", name="pk_document_version"),
        sa.UniqueConstraint(
            "document_id", "version_seq", name="uq_document_version_document_id_version_seq"
        ),
    )

    # working_draft — the check-out mirror (Redis is the lock authority).
    op.create_table(
        "working_draft",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("checked_out_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "checked_out_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scratch_blob_ref", sa.Text(), nullable=True),
        sa.Column("lock_token", sa.Text(), nullable=True),
        sa.Column("lock_ttl", sa.Interval(), server_default=sa.text("'8 hours'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("working_draft"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documented_information.id"],
            name="fk_working_draft_document_id_documented_information",
            ondelete="RESTRICT",
        ),
        _user_fk("working_draft", "checked_out_by"),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["document_version.id"],
            name="fk_working_draft_source_version_id_document_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_working_draft"),
        sa.UniqueConstraint("document_id", name="uq_working_draft_document_id"),
    )

    # numbering_counter — atomic per-(type, area) identifier sequence.
    op.create_table(
        "numbering_counter",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type_code", sa.Text(), nullable=False),
        sa.Column("area_code", sa.Text(), nullable=False),
        sa.Column("next_value", sa.Integer(), server_default="0", nullable=False),
        _org_fk("numbering_counter"),
        sa.PrimaryKeyConstraint("org_id", "type_code", "area_code", name="pk_numbering_counter"),
    )


def downgrade() -> None:
    op.drop_table("numbering_counter")
    op.drop_table("working_draft")
    op.drop_table("document_version")
    op.drop_table("documented_information")
    op.drop_table("blob")
    op.drop_table("document_type")
    op.drop_table("retention_policy")
    op.drop_table("framework")
    for name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")
