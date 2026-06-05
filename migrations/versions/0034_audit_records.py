"""audit records: audit_program + audit_plan + audit + audit_state + AUDIT_* events (S-aud-1)

The first slice of the v1 Audits/Findings/CAPA family (doc 02 Cl 9.2, doc 10 §5, doc 14 §9/§14). It
introduces the internal-audit scheduling + lifecycle layer; no finding/CAPA tables yet (S-aud-2+).

1. **audit_state** — a fresh ``CREATE TYPE`` (Scheduled→Planned→InProgress→FindingsDraft→Reported→
   Closing→Closed; usable same-txn — it is created, not ALTERed). Tuple sourced from the ORM
   ``AUDIT_STATE_VALUES`` so the hand-authored DDL and the SAEnum binding never drift.
2. **event_type ADD VALUE** — the six AUDIT_* lifecycle events (additive ``ALTER TYPE … ADD VALUE``,
   the 0011-0033 pattern). NONE is used by a row in this migration (PG16 in-txn rule satisfied —
   events are emitted at runtime). Programme/plan events key on the *reserved* ``AuditObjectType.audit``
   value and the audit record's events on ``record`` (audit.id is a record id), so NO
   ``audit_object_type`` ADD VALUE is needed.
3. **audit_program** — the maintained Cl 9.2 schedule container (own-table, NOT a documented_information
   subtype — decisions-register R39: a programme is a scheduling container, not a controlled document
   with renditions/mirror presence).
4. **audit_plan** — one scheduled audit of a process under a programme (own-table; ``program_id``
   RESTRICT).
5. **audit** — a ``kind=RECORD`` shared-PK subtype (``audit.id`` → ``record.id``): the captured record
   is immutable, only the mutable ``state`` column advances through the FSM.
6. **Explicit GRANTs** — SELECT,INSERT,UPDATE on audit_program + audit (maintain / FSM state) and
   SELECT,INSERT on audit_plan (no in-slice UPDATE) for ``easysynq_app``, pg_roles-guarded (the
   0029-0033 precedent).

Downgrade: drop audit → audit_plan → audit_program (reverse FK order; wholesale DROP TABLE handles a
populated DB — no inbound FK from another existing table), then the fresh ``audit_state`` TYPE. The
event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's downgrade DROPs the type; a
re-upgrade re-adds via ADD VALUE IF NOT EXISTS). Orphaned base ``record``/``documented_information``
rows from dropped audits are harmless leftovers. Round-trips up↔down↔check on PG16 incl. a populated-DB
downgrade.

Revision ID: 0034_audit_records
Revises: 0033_ingestion_commit
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._iso_audit_enums import AUDIT_STATE_VALUES

revision: str = "0034_audit_records"
down_revision: str | None = "0033_ingestion_commit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = (
    "AUDIT_PROGRAM_CREATED",
    "AUDIT_PROGRAM_UPDATED",
    "AUDIT_PLAN_CREATED",
    "AUDIT_CREATED",
    "AUDIT_TRANSITIONED",
    "AUDIT_CLOSED",
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The audit lifecycle enum (CREATE TYPE → usable same-txn). Tuple from the ORM *_VALUES.
    postgresql.ENUM(*AUDIT_STATE_VALUES, name="audit_state").create(bind, checkfirst=True)
    audit_state = postgresql.ENUM(name="audit_state", create_type=False)

    # 2. Extend event_type (additive; none used by a row here → in-txn safe).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 3. audit_program — the maintained schedule container (own-table).
    op.create_table(
        "audit_program",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=True),
        sa.Column("coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "archived", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_audit_program_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_audit_program_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_program"),
        sa.UniqueConstraint(
            "org_id", "identifier", name="uq_audit_program_org_id_identifier"
        ),
    )

    # 4. audit_plan — one scheduled audit of a process under a programme (own-table).
    op.create_table(
        "audit_plan",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auditee_process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lead_auditor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_date", sa.Date(), nullable=True),
        sa.Column("checklist_ref", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_audit_plan_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["audit_program.id"],
            name="fk_audit_plan_program_id_audit_program",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["auditee_process_id"],
            ["process.id"],
            name="fk_audit_plan_auditee_process_id_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lead_auditor_user_id"],
            ["app_user.id"],
            name="fk_audit_plan_lead_auditor_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_audit_plan_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_plan"),
    )

    # 5. audit — a kind=RECORD shared-PK subtype (audit.id → record.id). Mutable ``state`` column.
    op.create_table(
        "audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_auditor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.Date(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column(
            "state", audit_state, server_default=sa.text("'Scheduled'"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["id"], ["record.id"], name="fk_audit_id_record", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_audit_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["audit_plan.id"],
            name="fk_audit_plan_id_audit_plan",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lead_auditor_user_id"],
            ["app_user.id"],
            name="fk_audit_lead_auditor_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit"),
    )

    # 6. Least-privilege grants for the non-owner app role, pg_roles-guarded (the 0029-0033 pattern).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON audit_program TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON audit_plan TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON audit TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Drop in reverse FK order; wholesale DROP TABLE handles a populated DB (no inbound FK from another
    # existing table — audit_finding/capa do not exist yet). The event_type ADD VALUEs are irreversible
    # in PostgreSQL → no-op (0001's downgrade DROPs the type; a re-upgrade re-adds via ADD VALUE IF NOT
    # EXISTS). Orphaned base record/documented_information rows from dropped audits are harmless.
    op.drop_table("audit")
    op.drop_table("audit_plan")
    op.drop_table("audit_program")
    op.execute("DROP TYPE IF EXISTS audit_state")
