"""S-ack-1 (doc 04 §8, R15/R42/R43): the acknowledgements family schema.

Creates ``distribution_entry`` (editable issuance config — SELECT/INSERT/DELETE) and the
append-only ``acknowledgement`` evidence row (REVOKE UPDATE,DELETE — the capa_stage house style),
adds ``documented_information.acknowledgement_required``, the additive DOC_ACK task/subject enum
values + the two audit event types, seeds the R42 ``document.distribute`` key (catalog 99 → 100,
granted to QMS Owner) and the single-stage ``doc_acknowledgement`` workflow definition (the 0045
recipe: per-user context assignee, quorum ANY, NO signature block — an ack is never a
signature_event, R2).

Revision ID: 0048_acknowledgements
Revises: 0047_blob_verify_drift_read
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._ack_enums import (
    ACK_CREATED_REASON_VALUES,
    DISTRIBUTION_TARGET_TYPE_VALUES,
)

revision: str = "0048_acknowledgements"
down_revision: str | None = "0047_blob_verify_drift_read"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_KEY = "document.distribute"
_DEF_KEY = "doc_acknowledgement"
_NEW_EVENT_TYPES = ("DOCUMENT_ACKNOWLEDGED", "DISTRIBUTION_UPDATED")
_STAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "ack",
        "mode": "PARALLEL",
        "assignees": {
            "context_users": "user_id",
            "task_type": "DOC_ACK",
            "action_expected": "acknowledge",
        },
        "quorum": {"type": "ANY"},
        "transitions": [],
        # NO signature block — an ack writes an acknowledgement row + audit event, never a
        # signature_event (R2/R43; document.acknowledge is sig_hook=false).
    },
)


def upgrade() -> None:
    # 1. Additive enum values (IF NOT EXISTS → idempotent). ⚠ Unlike every prior ADD VALUE
    # migration (the 0011 "not USED in this txn" rule), the doc_acknowledgement seed BELOW uses
    # 'DOC_ACK' in this same migration, and PG ≥ 12 raises UnsafeNewEnumValueUsage for a value
    # added in the current transaction — so the ADD VALUEs run in an autocommit block (committed
    # immediately; safe: additive, idempotent, and irreversible in PG anyway).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'DOC_ACK'")
        op.execute("ALTER TYPE workflow_subject_type ADD VALUE IF NOT EXISTS 'DOC_ACK'")
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()

    # 2. The two fresh enums (tuples from the ORM *_VALUES — the 0010 rule).
    postgresql.ENUM(*DISTRIBUTION_TARGET_TYPE_VALUES, name="distribution_target_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*ACK_CREATED_REASON_VALUES, name="ack_created_reason").create(
        bind, checkfirst=True
    )
    target_type = postgresql.ENUM(name="distribution_target_type", create_type=False)
    created_reason = postgresql.ENUM(name="ack_created_reason", create_type=False)

    # 3. The per-document master switch (NOT NULL needs a server_default backfill on an
    # existing table; the ORM uses a Python-side default — the is_singleton precedent).
    op.add_column(
        "documented_information",
        sa.Column(
            "acknowledgement_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # 4. distribution_entry — editable issuance config (doc 14 §5.6).
    op.create_table(
        "distribution_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", target_type, nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ack_required", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_distribution_entry_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documented_information.id"],
            name="fk_distribution_entry_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_distribution_entry_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_distribution_entry"),
        sa.UniqueConstraint(
            "document_id", "target_type", "target_id", name="uq_distribution_entry_target"
        ),
    )
    op.create_index("ix_distribution_entry_document_id", "distribution_entry", ["document_id"])

    # 5. acknowledgement — the append-only Cl 7.3 evidence (doc 14 §5.6 + org_id/document_id/
    # created_reason per the build conventions).
    op.create_table(
        "acknowledgement",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("client_ip", sa.Text(), nullable=True),
        sa.Column("created_reason", created_reason, nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_acknowledgement_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documented_information.id"],
            name="fk_acknowledgement_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_version.id"],
            name="fk_acknowledgement_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_acknowledgement_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_acknowledgement"),
        sa.UniqueConstraint(
            "user_id", "document_version_id", name="uq_acknowledgement_user_version"
        ),
    )
    op.create_index(
        "ix_acknowledgement_document_id_user_id", "acknowledgement", ["document_id", "user_id"]
    )

    # 6. Least-privilege grants (pg_roles-guarded): distribution_entry is editable config
    # (no UPDATE — change = delete + re-add); acknowledgement is append-only (the capa_stage
    # REVOKE house style).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, DELETE ON distribution_entry TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE ON distribution_entry FROM {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON acknowledgement TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE, DELETE ON acknowledgement FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 7. R42: seed document.distribute (CONTENT-domain, ARTIFACT-finest, non-sig-hook, non-SoD;
    # catalog 99 → 100). The 0047 recipe.
    permission_t = sa.table(
        "permission",
        sa.column("key", sa.Text),
        sa.column("resource", sa.Text),
        sa.column("action", sa.Text),
        sa.column("is_system_domain", sa.Boolean),
        sa.column("sod_sensitive", sa.Boolean),
        sa.column("sig_hook", sa.Boolean),
        sa.column("finest_scope", postgresql.ENUM(name="scope_level", create_type=False)),
    )
    bind.execute(
        pg_insert(permission_t)
        .values(
            [
                {
                    "key": _NEW_KEY,
                    "resource": "document",
                    "action": "distribute",
                    "is_system_domain": False,
                    "sod_sensitive": False,
                    "sig_hook": False,
                    "finest_scope": "ARTIFACT",
                }
            ]
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )

    # 8. Resilient org lookup (the 0045 HARD variant — the doc_acknowledgement seed is
    # load-bearing: a missing definition makes the daily sweep degrade-to-no-op forever, and the
    # QMS Owner grant should land with it). NEVER skip-if-absent.
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one()

    # 8a. Grant document.distribute to QMS Owner.
    perm_id = bind.execute(
        sa.text("SELECT id FROM permission WHERE key = :k"), {"k": _NEW_KEY}
    ).scalar_one()
    role_id = bind.execute(
        sa.text("SELECT id FROM role WHERE org_id = :o AND name = 'QMS Owner'"),
        {"o": org_id},
    ).scalar_one_or_none()
    if role_id is not None:
        role_grant_t = sa.table(
            "role_grant",
            sa.column("org_id", postgresql.UUID(as_uuid=True)),
            sa.column("role_id", postgresql.UUID(as_uuid=True)),
            sa.column("permission_id", postgresql.UUID(as_uuid=True)),
            sa.column("scope_template", postgresql.JSONB),
        )
        bind.execute(
            pg_insert(role_grant_t)
            .values(
                [
                    {
                        "org_id": org_id,
                        "role_id": role_id,
                        "permission_id": perm_id,
                        "scope_template": {"level": "SYSTEM"},
                    }
                ]
            )
            .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
        )

    # 9. The doc_acknowledgement workflow definition (the 0045 seed shape verbatim).
    definition_t = sa.table(
        "workflow_definition",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("effective", sa.Boolean),
        sa.column("subject_type", postgresql.ENUM(name="workflow_subject_type", create_type=False)),
        sa.column("stages", postgresql.JSONB),
        sa.column("default_sla", postgresql.JSONB),
    )
    bind.execute(
        pg_insert(definition_t)
        .values(
            org_id=org_id,
            key=_DEF_KEY,
            version=1,
            effective=True,
            subject_type="DOC_ACK",
            stages={"entry": "ack"},
            default_sla=None,  # due_at is stamped by the sweep (now + ACK_DUE_DAYS), not an SLA
        )
        .on_conflict_do_nothing(index_elements=["org_id", "key", "version"])
    )
    definition_id = bind.execute(
        sa.text(
            "SELECT id FROM workflow_definition WHERE org_id = :org AND key = :key AND version = 1"
        ),
        {"org": org_id, "key": _DEF_KEY},
    ).scalar_one()
    stage_t = sa.table(
        "workflow_stage",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("definition_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("mode", postgresql.ENUM(name="workflow_stage_mode", create_type=False)),
        sa.column("assignees", postgresql.JSONB),
        sa.column("quorum", postgresql.JSONB),
        sa.column("transitions", postgresql.JSONB),
        sa.column("signature", postgresql.JSONB),
    )
    for st in _STAGES:
        bind.execute(
            pg_insert(stage_t)
            .values(
                org_id=org_id,
                definition_id=definition_id,
                key=st["key"],
                mode=st["mode"],
                assignees=st.get("assignees"),
                quorum=st.get("quorum"),
                transitions=st.get("transitions"),
                signature=st.get("signature"),
            )
            .on_conflict_do_nothing(index_elements=["definition_id", "key"])
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Seed delete guarded by child instances (the 0023/0045 precedent).
    has_instances = bind.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM workflow_instance wi "
            "JOIN workflow_definition wd ON wi.definition_id = wd.id WHERE wd.key = :k)"
        ),
        {"k": _DEF_KEY},
    ).scalar()
    if not has_instances:
        bind.execute(
            sa.text(
                "DELETE FROM workflow_stage WHERE definition_id IN "
                "(SELECT id FROM workflow_definition WHERE key = :k)"
            ),
            {"k": _DEF_KEY},
        )
        bind.execute(sa.text("DELETE FROM workflow_definition WHERE key = :k"), {"k": _DEF_KEY})
    # role_grant BEFORE permission (RESTRICT FK).
    bind.execute(
        sa.text(
            "DELETE FROM role_grant WHERE permission_id IN "
            "(SELECT id FROM permission WHERE key = :k)"
        ),
        {"k": _NEW_KEY},
    )
    bind.execute(sa.text("DELETE FROM permission WHERE key = :k"), {"k": _NEW_KEY})
    op.drop_index("ix_acknowledgement_document_id_user_id", table_name="acknowledgement")
    op.drop_table("acknowledgement")
    op.drop_index("ix_distribution_entry_document_id", table_name="distribution_entry")
    op.drop_table("distribution_entry")
    op.drop_column("documented_information", "acknowledgement_required")
    for enum_name in ("ack_created_reason", "distribution_target_type"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
    # The ADD VALUEs are irreversible in PG → no-op (the 0011/0047 precedent).
