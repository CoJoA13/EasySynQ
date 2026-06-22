"""S-notify-1: the notification spine + email outbox (doc 10 §9, R53).

4 operational tables (notification / notification_email / notification_template /
notification_preference) + the notification_email_status enum + the system_config opt-in column +
two seeded en templates. No WORM, no hash chain. See spec §3/§10.

Revision ID: 0063_notifications
Revises: 0062_register_steward_role
Create Date: 2026-06-21
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._notification_enums import NOTIFICATION_EMAIL_STATUS_VALUES

revision: str = "0063_notifications"
down_revision: str | None = "0062_register_steward_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

_TASK_ASSIGNED_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "You have a task in EasySynQ: {{task.action_expected}} {{subject.identifier}} — "
    '"{{subject.title}}".\n\n'
    "  Due by: {{task.due_at | date}}\n\n"
    "Open in EasySynQ: {{deep_link}}\n\n"
    "Manage notifications: {{prefs_link}}\n"
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The fresh delivery-status enum (CREATE TYPE → usable same-txn; tuple from the ORM *_VALUES).
    postgresql.ENUM(*NOTIFICATION_EMAIL_STATUS_VALUES, name="notification_email_status").create(
        bind, checkfirst=True
    )
    email_status = postgresql.ENUM(name="notification_email_status", create_type=False)

    # 2. notification_template (created first — notification.template_id FKs to it).
    op.create_table(
        "notification_template",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), server_default=sa.text("'en'"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_effective", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("in_app_title", sa.Text(), nullable=False),
        sa.Column("in_app_body", sa.Text(), nullable=False),
        sa.Column("email_subject", sa.Text(), nullable=False),
        sa.Column("email_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notification_template"),
    )
    op.create_index(
        "uq_notification_template_one_effective",
        "notification_template",
        ["event_key", "locale"],
        unique=True,
        postgresql_where=sa.text("is_effective"),
    )

    # 3. notification.
    op.create_table(
        "notification",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("deep_link", sa.Text(), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_notification"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name="fk_notification_org_id_organization", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["app_user.id"],
            name="fk_notification_recipient_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task.id"], name="fk_notification_task_id_task", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["notification_template.id"],
            name="fk_notification_template_id_notification_template",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_notification_recipient_unread", "notification", ["recipient_user_id", "read_at", "created_at"]
    )
    op.create_index(
        "uq_notification_dedup_task",
        "notification",
        ["recipient_user_id", "task_id", "event_key"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )

    # 4. notification_email.
    op.create_table(
        "notification_email",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", email_status, server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notification_email"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_notification_email_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notification.id"],
            name="fk_notification_email_notification_id_notification",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("notification_id", name="uq_notification_email_notification_id"),
    )
    op.create_index(
        "ix_notification_email_status_next", "notification_email", ["status", "next_attempt_at"]
    )

    # 5. notification_preference.
    op.create_table(
        "notification_preference",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_notification_preference"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_notification_preference_user_id_app_user",
            ondelete="RESTRICT",
        ),
    )

    # 6. The per-org opt-in column (column-add, server_default false → backfills every existing row).
    op.add_column(
        "system_config",
        sa.Column("notifications_email_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    # 7. Seed the two en templates (global; version 1, effective). task.assigned (in-app + email);
    #    system.email_delivery_failed (in-app only — email_* empty, NEVER sent + NO subject fields).
    op.bulk_insert(
        sa.table(
            "notification_template",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("event_key", sa.Text()),
            sa.column("locale", sa.Text()),
            sa.column("version", sa.Integer()),
            sa.column("is_effective", sa.Boolean()),
            sa.column("in_app_title", sa.Text()),
            sa.column("in_app_body", sa.Text()),
            sa.column("email_subject", sa.Text()),
            sa.column("email_body", sa.Text()),
        ),
        [
            {
                "id": uuid.uuid4(),
                "event_key": "task.assigned",
                "locale": "en",
                "version": 1,
                "is_effective": True,
                "in_app_title": "New task: {{subject.identifier}}",
                "in_app_body": (
                    '{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}" '
                    "(due {{task.due_at | date}})"
                ),
                "email_subject": "[EasySynQ] Action required: {{subject.identifier}} {{subject.title}}",
                "email_body": _TASK_ASSIGNED_EMAIL_BODY,
            },
            {
                "id": uuid.uuid4(),
                "event_key": "system.email_delivery_failed",
                "locale": "en",
                "version": 1,
                "is_effective": True,
                "in_app_title": "Email delivery failed",
                "in_app_body": (
                    "An email to {{recipient_email}} could not be delivered after {{attempts}} "
                    "attempts. Last error: {{last_error}} (notification {{notification_id}})."
                ),
                "email_subject": "",
                "email_body": "",
            },
        ],
    )

    # 8. App-role grants (operational tables: read/write; templates read-only).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON notification TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON notification_email TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON notification_preference TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT ON notification_template TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_column("system_config", "notifications_email_enabled")
    op.drop_index("ix_notification_email_status_next", table_name="notification_email")
    op.drop_table("notification_email")
    op.drop_index("uq_notification_dedup_task", table_name="notification")
    op.drop_index("ix_notification_recipient_unread", table_name="notification")
    op.drop_table("notification")
    op.drop_table("notification_preference")
    op.drop_index("uq_notification_template_one_effective", table_name="notification_template")
    op.drop_table("notification_template")
    op.execute("DROP TYPE IF EXISTS notification_email_status")
