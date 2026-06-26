"""S-escalate2: a second (final) escalation tier.

Adds sla_policy.escalate_2_after (Interval) + task.escalated_2_at (timestamptz), seeds the global
task.escalated_final notification template, and sets escalate_2_after to three (business) days for
the escalate-enabled policies (escalate_1_after IS NOT NULL — preserving the DOC_ACK/PERIODIC_REVIEW
carve-out), so the timer sweep emits a distinct second escalation to Top Management.

The template table is global (no org_id) so the template seed is exercised by a fresh-DB upgrade
head; the sla_policy UPDATE is org-scoped (the DEFAULT org from 0002 makes a full upgrade head
exercise it). Seed-DATA effects are confirmed by the integration test + live-smoke (the 0065/0068
precedent). Both new columns are mirrored in the ORM (sla_policy.py / workflow.py) so alembic check
stays clean.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "0069_escalate2_final_tier"
down_revision: str | None = "0068_remind2_final_reminder"
branch_labels: str | None = None
depends_on: str | None = None

_IN_APP_TITLE = "Escalation (final): {{subject.identifier}}"
_IN_APP_BODY = (
    '{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}"'
    " is still overdue (due {{task.due_at | date}})"
)
_EMAIL_SUBJECT = "[EasySynQ] Escalation (final): {{subject.identifier}} {{subject.title}}"
_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "A task in EasySynQ remains overdue after the first escalation and is being escalated to "
    'leadership: {{task.action_expected}} {{subject.identifier}} — "{{subject.title}}".\n\n'
    "  Due by: {{task.due_at | date}}\n\n"
    "Open in EasySynQ: {{deep_link}}\n\n"
    "Manage notifications: {{prefs_link}}\n"
)


def upgrade() -> None:
    bind = op.get_bind()
    # 1. DDL: the tier-2 offset column + the per-task stamp column (both nullable).
    op.add_column("sla_policy", sa.Column("escalate_2_after", sa.Interval(), nullable=True))
    op.add_column("task", sa.Column("escalated_2_at", sa.DateTime(timezone=True), nullable=True))

    # 2. Seed the global task.escalated_final template (idempotent on the partial-unique index).
    bind.execute(
        sa.text(
            "INSERT INTO notification_template"
            " (id, event_key, locale, version, is_effective,"
            "  in_app_title, in_app_body, email_subject, email_body)"
            " VALUES (:id, 'task.escalated_final', 'en', 1, TRUE,"
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
    # 3. Enable the second escalation: 3 (business) days after due, ONLY for escalate-enabled
    #    policies (escalate_1_after IS NOT NULL) — DOC_ACK/PERIODIC_REVIEW never escalated at tier-1
    #    and must not get a tier-2. WHERE ... IS NULL keeps it idempotent.
    bind.execute(
        sa.text(
            "UPDATE sla_policy SET escalate_2_after = INTERVAL '3 days'"
            " WHERE escalate_2_after IS NULL AND escalate_1_after IS NOT NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Reverse the seed intent (all rows were NULL before this migration).
    bind.execute(sa.text("UPDATE sla_policy SET escalate_2_after = NULL"))
    # Delete the template ONLY if no notification references it — notification.template_id is a
    # RESTRICT FK, so an unguarded DELETE aborts the downgrade on a populated DB (the S-notify-4
    # lesson; a fresh-DB round-trip is green and hides it).
    bind.execute(
        sa.text(
            "DELETE FROM notification_template"
            " WHERE event_key = 'task.escalated_final'"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)"
        )
    )
    op.drop_column("task", "escalated_2_at")
    op.drop_column("sla_policy", "escalate_2_after")
