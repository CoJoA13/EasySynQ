"""S-notify-4: sla_policy table + task timer-stamp columns + escalation-timer templates.

Adds the data foundation for the durable ``timer_sweep`` Beat (doc 10 §9.5):

* ``event_type`` += ``TASK_ESCALATED`` (ALTER TYPE ADD VALUE in autocommit_block; irreversible
  in PG → no-op downgrade, the 0059 pattern).
* NEW TABLE ``sla_policy`` — one row per (org, task_type) holding INTERVAL reminder/escalate
  offsets.  SELECT-only for the app role (REVOKE block counters 0010's ALTER DEFAULT PRIVILEGES
  auto-grant).
* 4 nullable ``timestamptz`` columns on ``task`` (``remind_1_sent_at``, ``remind_2_sent_at``,
  ``overdue_notified_at``, ``escalated_1_at``) — idempotency guards for the sweep.
* Partial index ``ix_task_timer_pending`` backing the sweep query (migration-managed; excluded
  from ``env.py`` autogenerate).
* 3 seeded ``notification_template`` rows: ``task.due_soon``, ``task.overdue``,
  ``task.escalated`` (locale ``en``, version 1, effective).
* 1 active ``sla_policy`` row per ``TaskType`` for the default org (resilient org lookup).

Revision ID: 0065_escalation_timers
Revises: 0064_notification_digests
Create Date: 2026-06-23
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._workflow_enums import TaskType

revision: str = "0065_escalation_timers"
down_revision: str | None = "0064_notification_digests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

_DUE_SOON_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "You have a task in EasySynQ that is due soon: "
    '{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}".\n\n'
    "  Due by: {{task.due_at | date}}\n\n"
    "Open in EasySynQ: {{deep_link}}\n\n"
    "Manage notifications: {{prefs_link}}\n"
)

_OVERDUE_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "You have a task in EasySynQ that is now overdue: "
    '{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}".\n\n'
    "  Was due: {{task.due_at | date}}\n\n"
    "Please act as soon as possible. Open in EasySynQ: {{deep_link}}\n\n"
    "Manage notifications: {{prefs_link}}\n"
)

_ESCALATED_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    'An overdue task on {{subject.identifier}} — "{{subject.title}}" has been escalated to you '
    "(due {{task.due_at | date}}).\n\n"
    "Open in EasySynQ: {{deep_link}}\n\n"
    "Manage notifications: {{prefs_link}}\n"
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. ADD the new event_type value outside a transaction (irreversible ADD VALUE pattern).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'TASK_ESCALATED'")

    # 2. Create the sla_policy table.
    op.create_table(
        "sla_policy",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "task_type",
            postgresql.ENUM(name="task_type", create_type=False),
            nullable=False,
        ),
        sa.Column("remind_1_before", sa.Interval(), nullable=True),
        sa.Column("remind_2_before", sa.Interval(), nullable=True),
        sa.Column("escalate_1_after", sa.Interval(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sla_policy"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_sla_policy_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "task_type", name="uq_sla_policy_org_task_type"),
    )

    # 3. Add the 4 timer-stamp columns to the task table.
    for col_name in (
        "remind_1_sent_at",
        "remind_2_sent_at",
        "overdue_notified_at",
        "escalated_1_at",
    ):
        op.add_column("task", sa.Column(col_name, sa.DateTime(timezone=True), nullable=True))

    # 4. Partial index backing the sweep query (migration-managed; excluded from env.py autogenerate).
    op.create_index(
        "ix_task_timer_pending",
        "task",
        ["due_at"],
        postgresql_where=sa.text("state IN ('PENDING','CLAIMED') AND due_at IS NOT NULL"),
    )

    # 5. App-role grants — sla_policy is seed-managed reference data → SELECT-only.
    #    0010's ALTER DEFAULT PRIVILEGES grants full DML to easysynq_app on every new table,
    #    so a plain GRANT SELECT is a no-op; REVOKE INSERT/UPDATE/DELETE to enforce SELECT-only.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT ON sla_policy TO {_APP_ROLE}';
                EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON sla_policy FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 6. Seed the 3 notification_template rows for the timer events.
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
                "event_key": "task.due_soon",
                "locale": "en",
                "version": 1,
                "is_effective": True,
                "in_app_title": "Due soon: {{subject.identifier}}",
                "in_app_body": (
                    '{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}"'
                    " (due {{task.due_at | date}})"
                ),
                "email_subject": (
                    "[EasySynQ] Due soon: {{subject.identifier}} {{subject.title}}"
                ),
                "email_body": _DUE_SOON_EMAIL_BODY,
            },
            {
                "id": uuid.uuid4(),
                "event_key": "task.overdue",
                "locale": "en",
                "version": 1,
                "is_effective": True,
                "in_app_title": "Overdue: {{subject.identifier}}",
                "in_app_body": (
                    '{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}"'
                    " is now overdue (was due {{task.due_at | date}})"
                ),
                "email_subject": (
                    "[EasySynQ] Overdue: {{subject.identifier}} {{subject.title}}"
                ),
                "email_body": _OVERDUE_EMAIL_BODY,
            },
            {
                "id": uuid.uuid4(),
                "event_key": "task.escalated",
                "locale": "en",
                "version": 1,
                "is_effective": True,
                "in_app_title": "Escalated: {{subject.identifier}}",
                "in_app_body": (
                    'An overdue task on {{subject.identifier}} — "{{subject.title}}"'
                    " has been escalated to you (due {{task.due_at | date}})."
                ),
                "email_subject": (
                    "[EasySynQ] Escalated to you: {{subject.identifier}} {{subject.title}}"
                ),
                "email_body": _ESCALATED_EMAIL_BODY,
            },
        ],
    )

    # 7. Seed one active sla_policy per TaskType for the default org.
    #    Resilient org lookup (the 0053/0062 precedent — NOT WHERE short_code='DEFAULT').
    org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one_or_none()
    _three_days = datetime.timedelta(days=3)
    _one_day = datetime.timedelta(days=1)

    if org_id is not None:
        op.bulk_insert(
            sa.table(
                "sla_policy",
                sa.column("id", postgresql.UUID(as_uuid=True)),
                sa.column("org_id", postgresql.UUID(as_uuid=True)),
                sa.column("task_type", postgresql.ENUM(name="task_type", create_type=False)),
                sa.column("remind_1_before", sa.Interval()),
                sa.column("remind_2_before", sa.Interval()),
                sa.column("escalate_1_after", sa.Interval()),
                sa.column("active", sa.Boolean()),
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "org_id": org_id,
                    "task_type": tt.value,
                    "remind_1_before": _three_days,
                    "remind_2_before": _one_day,
                    "escalate_1_after": _one_day,
                    "active": True,
                }
                for tt in TaskType
            ],
        )


def downgrade() -> None:
    # Reverse in opposite order; enum ADD VALUE is irreversible → no-op comment per 0059.
    op.execute(
        "DELETE FROM notification_template "
        "WHERE event_key IN ('task.due_soon', 'task.overdue', 'task.escalated')"
    )
    op.execute("DELETE FROM sla_policy")
    op.drop_index("ix_task_timer_pending", table_name="task")
    for col_name in (
        "escalated_1_at",
        "overdue_notified_at",
        "remind_2_sent_at",
        "remind_1_sent_at",
    ):
        op.drop_column("task", col_name)
    op.drop_table("sla_policy")
    # ALTER TYPE event_type ADD VALUE 'TASK_ESCALATED' is irreversible in PostgreSQL → no-op.
