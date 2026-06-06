"""audit findings + the NC→CAPA auto-link + the deferred cross-FK (S-aud-2)

The findings slice of the v1 Audits/Findings/CAPA family (doc 02 Cl 9.2, doc 10 §5.3, doc 14 §9).

1. **Fresh enum** ``finding_type`` (``CREATE TYPE`` → usable same-txn): NC / OBSERVATION / OFI. Value
   tuple sourced from the ORM ``_iso_audit_enums.FINDING_TYPE_VALUES`` so DDL + SAEnum never drift.
   Finding *severity* reuses the existing ``nc_severity`` type (R39, created in 0036).
2. **event_type ADD VALUE** — AUDIT_FINDING_CREATED / AUDIT_FINDING_CORRECTED (no row at a new value
   in-migration — PG16 rule; events are emitted at runtime). Findings reuse ``audit_object_type=record``
   (finding.id IS a record id) → zero ``audit_object_type`` ADD VALUE.
3. **audit_finding** — a ``kind=RECORD`` shared-PK subtype (``audit_finding.id`` → ``record.id``);
   ``audit_id`` FK→audit, ``finding_type``, nullable ``severity``, soft ``clause_ref``/``process_ref``,
   and ``auto_capa_id`` FK→capa (the forward half of the NC→CAPA auto-link, inline since capa exists
   from 0036). A boolean CHECK ``ck_audit_finding_nc_has_severity`` enforces "NC ⇒ severity" at the DB
   boundary the close gate trusts.
4. **The deferred cross-FK** — ``capa.origin_finding_id`` → ``audit_finding.id`` (the reverse half).
   S-capa-1 left the column FK-less + always-NULL, so adding the FK now orphans nothing. The name
   ``fk_capa_origin_finding_id_audit_finding`` matches the ORM (capa.py, use_alter back-edge) so
   ``alembic check`` is clean. capa↔audit_finding is a 2-table cycle (audit_finding.auto_capa_id →
   capa); creating this FK AFTER both tables exist (op.create_foreign_key, not inline) is the break.

Downgrade: NULL ``capa.origin_finding_id`` (so a populated down→up re-adds the FK cleanly), drop the
inbound FK from capa, drop the index, drop audit_finding (its outbound ``auto_capa_id`` FK→capa drops
with the table — an outbound RESTRICT FK never blocks DROP TABLE of the referencing table, the S-capa-1
empirical lesson), DROP TYPE finding_type. The event_type ADD VALUEs are irreversible in PostgreSQL →
no-op (0001's downgrade DROPs the type; a re-upgrade re-adds via ADD VALUE IF NOT EXISTS). Round-trips
up↔down↔check on PG16 incl. a populated-DB downgrade.

Revision ID: 0037_audit_findings
Revises: 0036_capa_core
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._iso_audit_enums import FINDING_TYPE_VALUES

revision: str = "0037_audit_findings"
down_revision: str | None = "0036_capa_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("AUDIT_FINDING_CREATED", "AUDIT_FINDING_CORRECTED")


def upgrade() -> None:
    # 1. The fresh finding_type enum (CREATE TYPE → usable same-txn). Tuple from the ORM *_VALUES.
    postgresql.ENUM(*FINDING_TYPE_VALUES, name="finding_type").create(
        op.get_bind(), checkfirst=True
    )
    finding_type = postgresql.ENUM(name="finding_type", create_type=False)
    nc_severity = postgresql.ENUM(name="nc_severity", create_type=False)  # exists from 0036

    # 2. Extend the audit-log event enum (additive; none used by a row here → in-txn safe).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 3. audit_finding — a kind=RECORD shared-PK subtype (audit_finding.id → record.id).
    op.create_table(
        "audit_finding",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_type", finding_type, nullable=False),
        sa.Column("severity", nc_severity, nullable=True),
        sa.Column("clause_ref", sa.Text(), nullable=True),
        sa.Column("process_ref", sa.Text(), nullable=True),
        sa.Column("auto_capa_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["id"], ["record.id"], name="fk_audit_finding_id_record", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_audit_finding_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["audit.id"], name="fk_audit_finding_audit_id_audit", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["auto_capa_id"],
            ["capa.id"],
            name="fk_audit_finding_auto_capa_id_capa",
            ondelete="RESTRICT",
        ),
        # Bare token — the metadata ck naming convention (ck_%(table_name)s_%(constraint_name)s)
        # expands it to ck_audit_finding_nc_has_severity, matching the ORM __table_args__ name.
        sa.CheckConstraint(
            "finding_type <> 'NC' OR severity IS NOT NULL",
            name="nc_has_severity",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_finding"),
    )
    op.create_index("ix_audit_finding_audit_id", "audit_finding", ["audit_id"])

    # 4. The deferred cross-FK: capa.origin_finding_id → audit_finding.id (the reverse half of the
    #    bidirectional NC→CAPA auto-link). Created AFTER both tables exist (the cycle break); the name
    #    matches the ORM (capa.py use_alter back-edge) so alembic check is clean.
    op.create_foreign_key(
        "fk_capa_origin_finding_id_audit_finding",
        "capa",
        "audit_finding",
        ["origin_finding_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 5. Least-privilege grant (UPDATE for the auto_capa_id set on the NC auto-link). pg_roles-guarded.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON audit_finding TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    # NULL the reverse pointer first so a populated down→up re-adds the FK without dangling values.
    bind.execute(sa.text("UPDATE capa SET origin_finding_id = NULL"))
    # Drop the inbound FK from capa before dropping the referenced table.
    op.drop_constraint("fk_capa_origin_finding_id_audit_finding", "capa", type_="foreignkey")
    op.drop_index("ix_audit_finding_audit_id", table_name="audit_finding")
    # audit_finding's outbound auto_capa_id FK→capa drops with the table (S-capa-1 empirical lesson).
    op.drop_table("audit_finding")
    op.execute("DROP TYPE IF EXISTS finding_type")
