"""S-capa-overdue: CAPA target-completion date + overdue notification.

Adds capa.target_completion_date (Date) + capa.overdue_notified_at (timestamptz), two additive
event_type values (CAPA_OVERDUE, CAPA_TARGET_DATE_SET), and the global capa.overdue notification
template. Both columns are nullable (existing CAPAs keep NULL → never overdue until the backfill CLI
or an edit sets a date) and ORM-mirrored (capa.py) so alembic check stays clean. The template table
is global (no org_id) so the seed is exercised by a fresh-DB upgrade head.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "0070_capa_overdue"
down_revision: str | None = "0069_escalate2_final_tier"
branch_labels: str | None = None
depends_on: str | None = None

_IN_APP_TITLE = "CAPA overdue: {{subject.identifier}}"
_IN_APP_BODY = (
    '{{subject.identifier}} — "{{subject.title}}" passed its target completion date'
    " ({{target_completion_date}}) and is still open"
)
_EMAIL_SUBJECT = "[EasySynQ] CAPA overdue: {{subject.identifier}} {{subject.title}}"
_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "A corrective action in EasySynQ has passed its target completion date and remains open: "
    '{{subject.identifier}} — "{{subject.title}}".\n\n'
    "  Target completion date: {{target_completion_date}}\n\n"
    "Open in EasySynQ: {{deep_link}}\n\n"
    "Manage notifications: {{prefs_link}}\n"
)


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("capa", sa.Column("target_completion_date", sa.Date(), nullable=True))
    op.add_column(
        "capa", sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Additive enum values — each in its own autocommit block (ADD VALUE cannot run in a txn block);
    # IF NOT EXISTS so a from-scratch upgrade head (which already has them via the ORM) is a no-op.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'CAPA_OVERDUE'")
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'CAPA_TARGET_DATE_SET'")
    bind.execute(
        sa.text(
            "INSERT INTO notification_template"
            " (id, event_key, locale, version, is_effective,"
            "  in_app_title, in_app_body, email_subject, email_body)"
            " VALUES (:id, 'capa.overdue', 'en', 1, TRUE,"
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


def downgrade() -> None:
    bind = op.get_bind()
    # Guard the template delete: notification.template_id is a RESTRICT FK, so an unguarded delete
    # aborts a populated downgrade (fresh-DB CI is blind to this — the S-notify-4 lesson).
    bind.execute(
        sa.text(
            "DELETE FROM notification_template"
            " WHERE event_key = 'capa.overdue'"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)"
        )
    )
    op.drop_column("capa", "overdue_notified_at")
    op.drop_column("capa", "target_completion_date")
    # The two event_type values are left in place — ALTER TYPE has no DROP VALUE; a re-upgrade's
    # IF NOT EXISTS makes them idempotent (the additive-enum no-op-downgrade convention).
