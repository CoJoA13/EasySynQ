"""awareness events (doc.released) — slice S-notify-5a

Revision ID: 0066_awareness_events
Revises: 0065_escalation_timers
Create Date: 2026-06-23
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066_awareness_events"
down_revision: str | None = "0065_escalation_timers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

_DOC_RELEASED_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    'A new Effective version of {{subject.identifier}} — "{{subject.title}}" '
    "({{version.label}}) has been released.\n\n"
    "Open it: {{deep_link}}\n\n"
    "— EasySynQ\n"
    "Manage your notifications: {{prefs_link}}"
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The awareness_event outbox table.
    op.create_table(
        "awareness_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fanned_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_awareness_event"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_awareness_event_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["app_user.id"],
            name="fk_awareness_event_actor_user_id_app_user",
            ondelete="RESTRICT",
        ),
    )

    # 2. Claim-scan partial index (migration-managed → excluded from env.py autogenerate).
    op.create_index(
        "ix_awareness_event_pending",
        "awareness_event",
        ["occurred_at"],
        postgresql_where=sa.text("fanned_out_at IS NULL"),
    )

    # 3. The version-discriminated awareness dedup column + partial-unique index on notification.
    op.add_column(
        "notification",
        sa.Column("subject_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_notification_dedup_awareness",
        "notification",
        ["recipient_user_id", "event_key", "subject_type", "subject_id", "subject_version_id"],
        unique=True,
        postgresql_where=sa.text("task_id IS NULL"),
    )

    # 4. App-role grants — awareness_event is an operational outbox: INSERT (emit) + SELECT/UPDATE
    #    (claim + stamp), but NOT DELETE (the 0063 ledger posture). 0010's ALTER DEFAULT PRIVILEGES
    #    already granted full DML, so REVOKE DELETE to enforce; the GRANT is a defensive no-op.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON awareness_event TO {_APP_ROLE}';
                EXECUTE 'REVOKE DELETE ON awareness_event FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 5. Seed the doc.released template. Raw INSERT + ON CONFLICT to reference the partial-unique
    #    uq_notification_template_one_effective (event_key, locale WHERE is_effective) — re-upgrade-safe.
    bind.execute(
        sa.text(
            "INSERT INTO notification_template"
            " (id, event_key, locale, version, is_effective,"
            "  in_app_title, in_app_body, email_subject, email_body)"
            " VALUES (:id, 'doc.released', 'en', 1, TRUE,"
            "         :in_app_title, :in_app_body, :email_subject, :email_body)"
            " ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "in_app_title": "{{subject.identifier}} {{version.label}} is now Effective",
            "in_app_body": (
                'A new Effective version of {{subject.identifier}} — "{{subject.title}}"'
                " ({{version.label}}) has been released."
            ),
            "email_subject": "[EasySynQ] Now Effective: {{subject.identifier}} {{version.label}}",
            "email_body": _DOC_RELEASED_EMAIL_BODY,
        },
    )


def downgrade() -> None:
    # Guard the template DELETE: notification.template_id is RESTRICT, and a fan-out that ran after
    # upgrade will have stamped rows referencing this template. A plain DELETE aborts on a populated
    # DB (CI is blind — fresh DB, the worker never fired). Leave it in place when children exist
    # (the 0023/0065 NOT-EXISTS precedent).
    op.execute(
        "DELETE FROM notification_template t "
        "WHERE t.event_key = 'doc.released' "
        "AND NOT EXISTS (SELECT 1 FROM notification n WHERE n.template_id = t.id)"
    )
    op.drop_index("uq_notification_dedup_awareness", table_name="notification")
    op.drop_column("notification", "subject_version_id")
    op.drop_index("ix_awareness_event_pending", table_name="awareness_event")
    op.drop_table("awareness_event")
