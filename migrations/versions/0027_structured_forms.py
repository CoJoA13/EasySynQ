"""structured forms: form_template + record.structured_pdf + config flag + FORM_SCHEMA_SET/CONFIG_UPDATED

Slice S-rec-3 (doc 06 §4.2, doc 14 §5.5) — Mode-B structured-form capture. A Form/Template is a
controlled DOCUMENT carrying a ``field_schema``; filling it captures a structured Record whose
``form_field_values`` are validated server-side against the schema pinned in the template's Effective
version.

1. **form_template** — a shared-PK subtype of ``documented_information`` (``id`` PK *is* the base
   row's id, RESTRICT — the ``record`` subtype precedent, 0008): ``org_id`` (tenancy) + the editable
   working ``field_schema`` JSONB. The schema is frozen into each ``document_version.metadata_snapshot``
   at check-in (the pinned source of truth for capture). Plain b-tree PK only → no ``env.py`` change.
2. **record.structured_pdf_blob_sha256** — a nullable pointer to the cached structured-record PDF
   (a DERIVED, regenerable view in the non-WORM renditions bucket; doc 14 §5.4). Plain Text, NO FK
   (the ``evidence_pack.zip_blob_sha256``/``portfolio_blob_sha256`` R27 precedent), so the WORM-destroy
   hatch never aborts on a RESTRICT FK; the destroy path drops the row + bytes (blob-row-iff-bytes).
3. **system_config.capture_pre_release_templates** — the org opt-in (default OFF) to capture against a
   non-Effective template (doc 06 §4.2). NOT-NULL with a ``false`` server_default (safe on a populated
   table); flipped only via the SYSTEM-gated PATCH /admin/config.
4. **FORM_SCHEMA_SET / CONFIG_UPDATED event_type** — additive ``ALTER TYPE … ADD VALUE`` (the
   0011-0026 pattern; ``audit_object_type`` already has ``document`` + ``config`` → no object-type ALTER).
   A from-scratch ``upgrade head`` rebuilds ``event_type`` from EVENT_TYPE_VALUES, so the members live
   in the ORM enum too.
5. **Explicit GRANTs** — SELECT/INSERT/UPDATE/DELETE on ``form_template`` to ``easysynq_app`` (the API
   authors + reads the schema), in a ``pg_roles``-guarded DO-block so a role-less CI DB doesn't error.

Migration notes: the two ADD VALUEs are never used by a row in THIS migration (the PG16 in-txn rule is
satisfied). The downgrade clears + drops ``form_template`` (a leaf — no inbound FK) and drops the two
columns; the ADD VALUEs are irreversible in PostgreSQL → no-op (a re-upgrade rebuilds the type from the
ORM values). Round-trips up↔down↔check on PG16 incl. a POPULATED-DB downgrade (a form_template row +
a Mode-B record pinned to its version).

Revision ID: 0027_structured_forms
Revises: 0026_pack_share_links
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_structured_forms"
down_revision: str | None = "0026_pack_share_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

# Listed literally (the 0026 ADD-VALUE precedent); they must match the ORM EventType members, which
# they do. The CREATE-TYPE path (none here) is what sources tuples from the ORM *_VALUES.
_NEW_EVENT_TYPES = ("FORM_SCHEMA_SET", "CONFIG_UPDATED")


def upgrade() -> None:
    # 1. form_template — the shared-PK Form/Template subtype carrying the editable working schema.
    op.create_table(
        "form_template",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["id"],
            ["documented_information.id"],
            name="fk_form_template_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_form_template_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_form_template"),
    )

    # 2. record.structured_pdf_blob_sha256 — the cached structured-record PDF pointer (derived; no FK).
    op.add_column("record", sa.Column("structured_pdf_blob_sha256", sa.Text(), nullable=True))

    # 3. system_config.capture_pre_release_templates — the org pre-release-capture toggle (default OFF).
    op.add_column(
        "system_config",
        sa.Column(
            "capture_pre_release_templates",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )

    # 4. Additive enum values (never used by a row here; PG16 in-txn rule satisfied).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 5. Explicit least-privilege grants for the non-owner app role (guarded for a role-less CI DB).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON form_template TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # form_template is a leaf (no inbound FK), but clear it explicitly for the populated-DB downgrade
    # (its id RESTRICT-FKs documented_information — drop the subtype rows before the table).
    op.execute("DELETE FROM form_template")
    op.drop_table("form_template")
    op.drop_column("system_config", "capture_pre_release_templates")
    op.drop_column("record", "structured_pdf_blob_sha256")
    # The event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's downgrade DROPs
    # event_type wholesale, so the up↔down round-trip still passes; a re-upgrade rebuilds it).
