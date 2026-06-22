"""S-notify-3a: digest preferences, quiet hours, digest-email kind, digest.daily template.

Additive — NO new table. Adds:
  * Two PG enums: notification_digest_mode, notification_email_kind.
  * notification_preference: 4 per-class digest-mode cols (nullable) + digest_hour + timezone +
    quiet_start + quiet_end + a CHECK on digest_hour (0-23).
  * system_config: notifications_escalation_pierce_quiet_hours (bool, default ON).
  * notification: digest_due_at, digested_at (nullable timestamps).
  * notification_email: email_kind (not-null, default 'single'), recipient_user_id (nullable FK
    to app_user), item_count (nullable int); notification_id made nullable + the inline UNIQUE
    dropped and replaced by a partial-unique index (WHERE notification_id IS NOT NULL).
  * notification_template: seeded 'digest.daily' row (en, v1, effective).

Downgrade note: rolling back after digest emails have been emitted will fail at the
``ALTER COLUMN notification_id SET NOT NULL`` step (digest rows have notification_id = NULL).
That is intentional — downgrade after live digest operation is a destructive rollback.

Revision ID: 0064_notification_digests
Revises: 0063_notifications
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._notification_enums import (
    NOTIFICATION_DIGEST_MODE_VALUES,
    NOTIFICATION_EMAIL_KIND_VALUES,
)

revision = "0064_notification_digests"
down_revision = "0063_notifications"
branch_labels = None
depends_on = None

_DIGEST_MODE = postgresql.ENUM(
    *NOTIFICATION_DIGEST_MODE_VALUES, name="notification_digest_mode", create_type=False
)
_EMAIL_KIND = postgresql.ENUM(
    *NOTIFICATION_EMAIL_KIND_VALUES, name="notification_email_kind", create_type=False
)
_DIGEST_DAILY_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "Here is your EasySynQ summary ({{item_count}} item(s)):\n\n"
    "{{items}}\n\n"
    "Open EasySynQ to act on these. Manage notifications: {{prefs_link}}\n"
)


def upgrade() -> None:
    bind = op.get_bind()
    _DIGEST_MODE.create(bind, checkfirst=True)
    _EMAIL_KIND.create(bind, checkfirst=True)

    # notification_preference: per-class modes (NULL ⇒ code default) + scheduling + quiet hours
    for col in (
        "digest_mode_action_required",
        "digest_mode_awareness",
        "digest_mode_critical",
        "digest_mode_admin_ops",
    ):
        op.add_column("notification_preference", sa.Column(col, _DIGEST_MODE, nullable=True))
    op.add_column(
        "notification_preference",
        sa.Column("digest_hour", sa.SmallInteger(), server_default=sa.text("8"), nullable=False),
    )
    op.create_check_constraint(
        "ck_notification_preference_digest_hour",
        "notification_preference",
        "digest_hour >= 0 AND digest_hour <= 23",
    )
    op.add_column(
        "notification_preference",
        sa.Column("timezone", sa.Text(), server_default=sa.text("'UTC'"), nullable=False),
    )
    op.add_column("notification_preference", sa.Column("quiet_start", sa.Time(), nullable=True))
    op.add_column("notification_preference", sa.Column("quiet_end", sa.Time(), nullable=True))

    # system_config: org-gated escalation pierce (default ON)
    op.add_column(
        "system_config",
        sa.Column(
            "notifications_escalation_pierce_quiet_hours",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )

    # notification: digest markers
    op.add_column(
        "notification", sa.Column("digest_due_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "notification", sa.Column("digested_at", sa.DateTime(timezone=True), nullable=True)
    )

    # notification_email: digest rows have no single source notification
    op.add_column(
        "notification_email",
        sa.Column("email_kind", _EMAIL_KIND, server_default=sa.text("'single'"), nullable=False),
    )
    op.add_column(
        "notification_email",
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_notification_email_recipient_user_id_app_user",
        "notification_email",
        "app_user",
        ["recipient_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("notification_email", sa.Column("item_count", sa.Integer(), nullable=True))
    op.drop_constraint("uq_notification_email_notification_id", "notification_email", type_="unique")
    op.alter_column("notification_email", "notification_id", nullable=True)
    op.create_index(
        "uq_notification_email_one_per_notification",
        "notification_email",
        ["notification_id"],
        unique=True,
        postgresql_where=sa.text("notification_id IS NOT NULL"),
    )

    # the digest.daily template (en, v1, effective)
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
                "event_key": "digest.daily",
                "locale": "en",
                "version": 1,
                "is_effective": True,
                "in_app_title": "",
                "in_app_body": "",
                "email_subject": "[EasySynQ] Your daily summary — {{item_count}} item(s)",
                "email_body": _DIGEST_DAILY_EMAIL_BODY,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM notification_template WHERE event_key = 'digest.daily'")
    op.drop_index("uq_notification_email_one_per_notification", table_name="notification_email")
    op.alter_column("notification_email", "notification_id", nullable=False)
    op.create_unique_constraint(
        "uq_notification_email_notification_id", "notification_email", ["notification_id"]
    )
    op.drop_column("notification_email", "item_count")
    op.drop_constraint(
        "fk_notification_email_recipient_user_id_app_user", "notification_email", type_="foreignkey"
    )
    op.drop_column("notification_email", "recipient_user_id")
    op.drop_column("notification_email", "email_kind")
    op.drop_column("notification", "digested_at")
    op.drop_column("notification", "digest_due_at")
    op.drop_column("system_config", "notifications_escalation_pierce_quiet_hours")
    op.drop_column("notification_preference", "quiet_end")
    op.drop_column("notification_preference", "quiet_start")
    op.drop_column("notification_preference", "timezone")
    op.drop_constraint(
        "ck_notification_preference_digest_hour", "notification_preference", type_="check"
    )
    op.drop_column("notification_preference", "digest_hour")
    op.drop_column("notification_preference", "digest_mode_admin_ops")
    op.drop_column("notification_preference", "digest_mode_critical")
    op.drop_column("notification_preference", "digest_mode_awareness")
    op.drop_column("notification_preference", "digest_mode_action_required")
    bind = op.get_bind()
    _EMAIL_KIND.drop(bind, checkfirst=True)
    _DIGEST_MODE.drop(bind, checkfirst=True)
