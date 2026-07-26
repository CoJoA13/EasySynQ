"""Batch 11 (review 2026-07-22 finding 2): operator alarms for backup + audit-integrity failures.

Three additive changes, no destructive DDL:

* the ``BACKUP_FAILED`` ``event_type`` value — a failed scheduled backup now leaves a durable audit
  row (object_type ``config``, the RESTORE_TEST_PASSED/_FAILED sibling) instead of only a log line;
* GLOBAL ``notification_template`` seeds for ``system.backup_failed`` + ``integrity.alarm``. Both
  keys have been class-mapped since S-notify-3a but had no template, so even a wired emitter would
  have rendered nothing (``render()`` returns None → the enqueue is skipped). The templates are
  global (no ``org_id``), so a fresh-DB ``alembic upgrade head`` exercises the seed and the
  ``migrations`` CI job covers it — unlike a per-org seed, which a zero-org CI database no-ops;
* ``audit_checkpoint_sink.enabled_at`` — the grace-window anchor for a witness that is enabled but
  has NEVER anchored. Before it, that state was benign forever, so a permanently dead off-host
  witness was indistinguishable from one added five minutes ago.

⚠ Every ``{{slot}}`` in the seeded bodies below MUST appear in that event's
``VARIABLE_WHITELIST`` entry (``services/notifications/constants.py``) — the renderer leaves an
unlisted slot as literal ``{{text}}`` in the delivered notification. ``test_notification_ops_events``
renders these exact strings and fails on any leftover slot.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "0074_operator_alarms"
down_revision: str | None = "0073_pending_blob_purge"
branch_labels: str | None = None
depends_on: str | None = None

# --- system.backup_failed (ADMIN_OPS) ---------------------------------------------------------
# Operational metadata ONLY: a System Administrator sits outside the QMS and holds no document.*,
# so an ops alarm never names a controlled document (the system.email_delivery_failed precedent).
_BACKUP_IN_APP_TITLE = "Scheduled backup failed"
_BACKUP_IN_APP_BODY = (
    "The scheduled backup to {{destination}} failed at {{failed_at}}. No new restorable"
    " archive was written. Error: {{error}}"
)
_BACKUP_EMAIL_SUBJECT = "[EasySynQ] Scheduled backup FAILED"
_BACKUP_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "The scheduled EasySynQ backup did not complete, so no new restorable archive was written.\n"
    "Until this is resolved, the newest archive available for a restore keeps getting older.\n\n"
    "  Destination: {{destination}}\n"
    "  Failed at:   {{failed_at}}\n"
    "  Error:       {{error}}\n\n"
    "Check that the backup destination is mounted, writable and not full, then re-run the backup.\n\n"
    "Manage notifications: {{prefs_link}}\n"
)

# --- integrity.alarm (CRITICAL — immediate + pierces quiet hours) ------------------------------
_INTEGRITY_IN_APP_TITLE = "Audit integrity alarm"
_INTEGRITY_IN_APP_BODY = (
    "The audit-trail integrity check ({{check}}) reported a failure at {{detected_at}}."
    " Broken links: {{break_count}}. {{reason_summary}}"
)
_INTEGRITY_EMAIL_SUBJECT = "[EasySynQ] CRITICAL: audit integrity alarm"
_INTEGRITY_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "The EasySynQ audit-trail integrity check reported a failure. The audit trail is the evidence"
    " an external auditor relies on, so treat this as urgent.\n\n"
    "  Check:        {{check}}\n"
    "  Detected at:  {{detected_at}}\n"
    "  Broken links: {{break_count}}\n"
    "  Detail:       {{reason_summary}}\n\n"
    "Do not restore, re-seed or prune the audit trail before investigating — doing so destroys the"
    " evidence of what happened.\n\n"
    "Manage notifications: {{prefs_link}}\n"
)

# DETERMINISTIC seed ids (Codex P2). The upgrade's ON CONFLICT DO NOTHING deliberately yields to an
# operator-authored effective template — but a downgrade keyed on ``event_key`` alone would then
# delete THAT row, plus any inactive historical version, none of which this migration created.
# Deriving the id from the (event_key, locale, version) triple lets the downgrade delete exactly the
# row it inserted and nothing else.
_SEED_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _seed_id(event_key: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NAMESPACE, f"easysynq:notification_template:{event_key}:en:1")


_SEEDS = (
    (
        "system.backup_failed",
        _BACKUP_IN_APP_TITLE,
        _BACKUP_IN_APP_BODY,
        _BACKUP_EMAIL_SUBJECT,
        _BACKUP_EMAIL_BODY,
    ),
    (
        "integrity.alarm",
        _INTEGRITY_IN_APP_TITLE,
        _INTEGRITY_IN_APP_BODY,
        _INTEGRITY_EMAIL_SUBJECT,
        _INTEGRITY_EMAIL_BODY,
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    # Additive enum value — ADD VALUE cannot run inside a transaction block; IF NOT EXISTS so a
    # from-scratch upgrade head (which already has it via EVENT_TYPE_VALUES) is a no-op.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BACKUP_FAILED'")

    # NOT NULL with a now() server default: v1 has no in-app create/enable surface for a sink, so
    # the provisioning path is a direct operator INSERT that does not know this column exists. The
    # default backfills existing rows too, which deliberately hands every already-configured sink a
    # fresh grace window rather than alarming the moment this migration lands.
    op.add_column(
        "audit_checkpoint_sink",
        # sa.func.now() (not sa.text("now()")) to match the ORM's server_default exactly and the
        # 0063 created_at precedent — a server_default spelled differently on the two sides is a
        # classic `alembic check` drift source.
        sa.Column(
            "enabled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    for event_key, in_app_title, in_app_body, email_subject, email_body in _SEEDS:
        bind.execute(
            sa.text(
                "INSERT INTO notification_template"
                " (id, event_key, locale, version, is_effective,"
                "  in_app_title, in_app_body, email_subject, email_body)"
                " VALUES (:id, :event_key, 'en', 1, TRUE,"
                "         :in_app_title, :in_app_body, :email_subject, :email_body)"
                " ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING"
            ),
            {
                "id": _seed_id(event_key),
                "event_key": event_key,
                "in_app_title": in_app_title,
                "in_app_body": in_app_body,
                "email_subject": email_subject,
                "email_body": email_body,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Delete by the DETERMINISTIC seed id, not by event_key: an operator-authored template (which
    # the upgrade's ON CONFLICT deliberately left alone) and any inactive historical version must
    # survive a downgrade. Still guarded with NOT EXISTS — notification.template_id is a RESTRICT
    # FK, so an unguarded delete aborts a POPULATED downgrade, and a fresh-DB CI run is blind to
    # that (the S-notify-4 lesson).
    for event_key, *_ in _SEEDS:
        bind.execute(
            sa.text(
                "DELETE FROM notification_template"
                " WHERE id = :id"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)"
            ),
            {"id": _seed_id(event_key)},
        )
    op.drop_column("audit_checkpoint_sink", "enabled_at")
    # BACKUP_FAILED is left in place — ALTER TYPE has no DROP VALUE, and a re-upgrade's
    # IF NOT EXISTS makes it idempotent (the additive-enum no-op-downgrade convention).
