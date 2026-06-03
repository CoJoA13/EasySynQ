"""records: retention-as-data + evidence_blob + evidence_for_link + RECORD_* events (S-rec-1)

Turns the inert ``record`` scaffolding (created in 0008) into a working records subsystem (doc 06):

1. **retention-policy-as-data** — graduates ``retention_policy`` from the S3 stub (id/org/name) to
   the doc-06 §5.1 schema (``applies_to``/``basis``/``duration``/``disposition_action``/
   ``review_required``/``worm_lock_period``) + ``UNIQUE(org_id, name)``, and seeds one
   ``"System Default Retention"`` per org (the always-present fallback for the NOT-NULL
   ``record.retention_policy_id``).
2. **evidence_blob** — the M:N record↔blob attachment store (multi-file records; doc 06 §3/§4.4).
3. **evidence_for_link** — the polymorphic audited *evidence-for* edge (record→clause/process/
   document; the ``signature_event`` no-FK precedent, doc 06 §6 / doc 14 §5.5).
4. **RECORD_* event_type** — RECORD_CAPTURED / RECORD_CORRECTED / RECORD_EVIDENCE_LINKED /
   RECORD_EVIDENCE_UNLINKED, additive ``ALTER TYPE event_type ADD VALUE`` (the 0011-0022 pattern;
   ``AuditObjectType.record`` already exists → no audit_object_type ALTER).

Enum/migration notes: the three new native enums (``retention_basis``/``disposition_action``/
``evidence_for_target_type``) are ``CREATE TYPE`` (not ``ALTER … ADD VALUE``), so their values are
usable in the same transaction — the per-org seed inserts ``basis``/``disposition_action`` values
immediately, which is safe. The event_type ADD VALUEs are never used by a row in this migration. The
new NOT-NULL ``retention_policy`` columns carry ``server_default``\\s frozen byte-identical to the ORM
(``compare_server_default`` is OFF). No functional/expression indexes → no ``env.py`` change.

Revision ID: 0023_records_capture
Revises: 0022_restore_events
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._evidence_enums import EVIDENCE_FOR_TARGET_TYPE_VALUES
from easysynq_api.db.models._retention_enums import (
    DISPOSITION_ACTION_VALUES,
    RETENTION_BASIS_VALUES,
)

revision: str = "0023_records_capture"
down_revision: str | None = "0022_restore_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The value tuples come from the ORM enum modules (the 0010_audit.py precedent) so the hand-authored
# CREATE TYPE and the ORM SAEnum bindings can never drift — alembic check cannot catch a CREATE-TYPE
# (vs ALTER) label/order change, so the single source of truth matters.
_ENUMS: dict[str, tuple[str, ...]] = {
    "retention_basis": RETENTION_BASIS_VALUES,
    "disposition_action": DISPOSITION_ACTION_VALUES,
    "evidence_for_target_type": EVIDENCE_FOR_TARGET_TYPE_VALUES,
}

_NEW_EVENT_TYPES = (
    "RECORD_CAPTURED",
    "RECORD_CORRECTED",
    "RECORD_EVIDENCE_LINKED",
    "RECORD_EVIDENCE_UNLINKED",
)

_DEFAULT_POLICY_NAME = "System Default Retention"


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

    retention_basis = postgresql.ENUM(name="retention_basis", create_type=False)
    disposition_action = postgresql.ENUM(name="disposition_action", create_type=False)
    evidence_for_target_type = postgresql.ENUM(name="evidence_for_target_type", create_type=False)

    # 1. retention_policy-as-data (doc 06 §5.1) — the real policy columns + the per-tenant unique.
    op.add_column(
        "retention_policy",
        sa.Column("applies_to", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "retention_policy",
        sa.Column(
            "basis", retention_basis, server_default=sa.text("'captured_at'"), nullable=False
        ),
    )
    op.add_column(
        "retention_policy",
        sa.Column("duration", sa.Text(), server_default=sa.text("'P10Y'"), nullable=False),
    )
    op.add_column(
        "retention_policy",
        sa.Column(
            "disposition_action",
            disposition_action,
            server_default=sa.text("'RETAIN_PERMANENT'"),
            nullable=False,
        ),
    )
    op.add_column(
        "retention_policy",
        sa.Column("review_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("retention_policy", sa.Column("worm_lock_period", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_retention_policy_org_id_name", "retention_policy", ["org_id", "name"]
    )

    # 2. evidence_blob — the M:N record↔blob attachment store.
    op.create_table(
        "evidence_blob",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blob_sha256", sa.Text(), nullable=False),
        sa.Column("is_original", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        _org_fk("evidence_blob"),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["record.id"],
            name="fk_evidence_blob_record_id_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["blob_sha256"],
            ["blob.sha256"],
            name="fk_evidence_blob_blob_sha256_blob",
            ondelete="RESTRICT",
        ),
        _user_fk("evidence_blob", "created_by"),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_blob"),
        sa.UniqueConstraint(
            "record_id", "blob_sha256", name="uq_evidence_blob_record_id_blob_sha256"
        ),
    )
    op.create_index("ix_evidence_blob_blob_sha256", "evidence_blob", ["blob_sha256"])

    # 3. evidence_for_link — the polymorphic audited evidence-for edge (no FK on target_id).
    op.create_table(
        "evidence_for_link",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", evidence_for_target_type, nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("link_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        _org_fk("evidence_for_link"),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["record.id"],
            name="fk_evidence_for_link_record_id_record",
            ondelete="RESTRICT",
        ),
        _user_fk("evidence_for_link", "created_by"),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_for_link"),
        sa.UniqueConstraint(
            "record_id",
            "target_type",
            "target_id",
            name="uq_evidence_for_link_record_id_target_type_target_id",
        ),
    )
    op.create_index(
        "ix_evidence_for_link_target_type_target_id",
        "evidence_for_link",
        ["target_type", "target_id"],
    )

    # 4. RECORD_* event_type values (additive; never used by a row in this migration).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 5. Seed one System Default Retention policy per org (idempotent; the always-present fallback).
    op.execute(
        sa.text(
            "INSERT INTO retention_policy "
            "(id, org_id, name, basis, duration, disposition_action, review_required) "
            "SELECT gen_random_uuid(), o.id, :name, "
            "'captured_at'::retention_basis, 'P10Y', "
            "'RETAIN_PERMANENT'::disposition_action, false "
            "FROM organization o "
            "ON CONFLICT (org_id, name) DO NOTHING"
        ).bindparams(name=_DEFAULT_POLICY_NAME)
    )


def downgrade() -> None:
    # Delete the seeded fallback ONLY where no captured record still pins it (review fix): on a
    # populated install a record's NOT-NULL retention_policy_id (a 0008 column 0023 does NOT drop)
    # references it under FK RESTRICT, so an unguarded delete would abort the whole downgrade. On
    # such a DB the seed row is left standing — harmless (re-seeding is ON CONFLICT DO NOTHING), so
    # the downgrade is effectively one-way for the seed row once records exist (the 0012 precedent).
    op.execute(
        sa.text(
            "DELETE FROM retention_policy rp WHERE rp.name = :name "
            "AND NOT EXISTS (SELECT 1 FROM record r WHERE r.retention_policy_id = rp.id)"
        ).bindparams(name=_DEFAULT_POLICY_NAME)
    )
    op.drop_index("ix_evidence_for_link_target_type_target_id", table_name="evidence_for_link")
    op.drop_table("evidence_for_link")
    op.drop_index("ix_evidence_blob_blob_sha256", table_name="evidence_blob")
    op.drop_table("evidence_blob")
    op.drop_constraint("uq_retention_policy_org_id_name", "retention_policy", type_="unique")
    op.drop_column("retention_policy", "worm_lock_period")
    op.drop_column("retention_policy", "review_required")
    op.drop_column("retention_policy", "disposition_action")
    op.drop_column("retention_policy", "duration")
    op.drop_column("retention_policy", "basis")
    op.drop_column("retention_policy", "applies_to")
    for name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")
    # The event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's downgrade DROPs
    # event_type wholesale, so the up↔down round-trip still passes).
