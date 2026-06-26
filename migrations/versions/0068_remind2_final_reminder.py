"""S-remind2: enable the second (final) pre-due reminder.

Seeds the global ``task.due_final`` notification template and sets
``sla_policy.remind_2_before`` to one (business) day for the seeded policies, so the timer sweep
emits a real second reminder under a distinct event key (the S-notify-4 defer). No DDL — pure data
seed; ``alembic check`` is trivially clean and the ORM is unchanged.

The template table is global (no ``org_id``) so the template seed is exercised by a fresh-DB
``upgrade head``; the ``sla_policy`` UPDATE is org-scoped (the ``DEFAULT`` org from 0002 makes a
full ``upgrade head`` exercise it). Seed-DATA effects are confirmed by the integration test +
live-smoke (the 0065/0067 precedent).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "0068_remind2_final_reminder"
down_revision: str | None = "0067_working_calendar"
branch_labels: str | None = None
depends_on: str | None = None

_IN_APP_TITLE = "Final reminder: {{subject.identifier}}"
_IN_APP_BODY = (
    '{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}"'
    " (due {{task.due_at | date}})"
)
_EMAIL_SUBJECT = "[EasySynQ] Final reminder: {{subject.identifier}} {{subject.title}}"
_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "Reminder — a task in EasySynQ is due very soon: "
    '{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}".\n\n'
    "  Due by: {{task.due_at | date}}\n\n"
    "Open in EasySynQ: {{deep_link}}\n\n"
    "Manage notifications: {{prefs_link}}\n"
)


def upgrade() -> None:
    bind = op.get_bind()
    # 1. Seed the global task.due_final template (idempotent on the partial-unique index).
    bind.execute(
        sa.text(
            "INSERT INTO notification_template"
            " (id, event_key, locale, version, is_effective,"
            "  in_app_title, in_app_body, email_subject, email_body)"
            " VALUES (:id, 'task.due_final', 'en', 1, TRUE,"
            "         :in_app_title, :in_app_body, :email_subject, :email_body)"
            " ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "in_app_title": _IN_APP_TITLE,
            "in_app_body": _IN_APP_BODY,
            "email_subject": _EMAIL_SUBJECT,
            "email_body": _EMAIL_BODY,
        },
    )
    # 2. Enable the second reminder: 1 (business) day before due, all task types.
    #    WHERE remind_2_before IS NULL keeps it idempotent and never clobbers a future value.
    bind.execute(
        sa.text(
            "UPDATE sla_policy SET remind_2_before = INTERVAL '1 day' WHERE remind_2_before IS NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Reverse the seed intent (all rows were NULL before this migration).
    bind.execute(sa.text("UPDATE sla_policy SET remind_2_before = NULL"))
    # Delete the template ONLY if no notification references it — notification.template_id is a
    # RESTRICT FK, so an unguarded DELETE aborts the downgrade on a populated DB (the S-notify-4
    # lesson; a fresh-DB round-trip is green and hides it).
    bind.execute(
        sa.text(
            "DELETE FROM notification_template"
            " WHERE event_key = 'task.due_final'"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)"
        )
    )
