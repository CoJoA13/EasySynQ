"""Give Import Report records a dedicated immutable permanent-retention policy (audit U7).

Import Report records previously pinned the org-configurable System Default policy. An org that
weakened that policy could therefore make the evidence-grade Import Report disposable even though
the ingestion contract says ``RETAIN_PERMANENT``. Seed one reserved policy per org (the 0077
sealed-pack pattern) and re-pin existing report records. If a user policy already owns the newly
reserved name, rename that row in place and preserve all of its settings and references rather
than commandeering it.

New organizations are covered by the matching lazy ensure in the import-commit path. This
migration changes data only; there is no schema, enum, grant, or permission-key change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0090_import_report_retention"
down_revision: str | None = "0089_constraint_name_coherence"
branch_labels: str | None = None
depends_on: str | None = None

_POLICY_NAME = "Import Report Retention"
_DEFAULT_POLICY_NAME = "System Default Retention"
_POLICY_ID_SALT = "easysynq.import-report-retention.v1:"
_PRESERVED_USER_PREFIX = f"{_POLICY_NAME} (preserved user policy: "
_PRESERVED_MANAGED_PREFIX = f"{_POLICY_NAME} (preserved 0090 managed policy: "


def upgrade() -> None:
    # This name was user-controlled before 0090. A deterministic id distinguishes the managed row
    # across upgrade/downgrade cycles; move any different-id collision aside without changing its
    # policy settings or the records/document-types/events that reference it.
    op.execute(
        sa.text(
            """
            UPDATE retention_policy AS collision
            SET
                name = (
                    :preserved_user_prefix
                    || collision.id::text
                    || ':'
                    || encode(gen_random_bytes(8), 'hex')
                    || ')'
                ),
                updated_at = now()
            WHERE collision.name = :policy_name
              AND collision.id <> (
                  encode(
                      substring(
                          digest(:policy_id_salt || collision.org_id::text, 'sha256')
                          FROM 1 FOR 16
                      ),
                      'hex'
                  )::uuid
              )
            """
        ).bindparams(
            policy_name=_POLICY_NAME,
            policy_id_salt=_POLICY_ID_SALT,
            preserved_user_prefix=_PRESERVED_USER_PREFIX,
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO retention_policy (
                id,
                org_id,
                name,
                applies_to,
                basis,
                duration,
                disposition_action,
                review_required,
                worm_lock_period,
                active,
                archived_at,
                archived_by
            )
            SELECT
                encode(
                    substring(digest(:policy_id_salt || o.id::text, 'sha256') FROM 1 FOR 16),
                    'hex'
                )::uuid,
                o.id,
                :policy_name,
                NULL,
                'captured_at'::retention_basis,
                'PERMANENT',
                'RETAIN_PERMANENT'::disposition_action,
                false,
                NULL,
                true,
                NULL,
                NULL
            FROM organization AS o
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                applies_to = NULL,
                basis = 'captured_at'::retention_basis,
                duration = 'PERMANENT',
                disposition_action = 'RETAIN_PERMANENT'::disposition_action,
                review_required = false,
                worm_lock_period = NULL,
                active = true,
                archived_at = NULL,
                archived_by = NULL,
                updated_at = now()
            """
        ).bindparams(policy_name=_POLICY_NAME, policy_id_salt=_POLICY_ID_SALT)
    )
    op.execute(
        sa.text(
            """
            UPDATE record AS r
            SET retention_policy_id = permanent.id
            FROM import_run AS ir, retention_policy AS permanent
            WHERE ir.report_record_id = r.id
              AND permanent.org_id = r.org_id
              AND permanent.name = :policy_name
            """
        ).bindparams(policy_name=_POLICY_NAME)
    )


def downgrade() -> None:
    # Restore report records to the historical fallback before removing the dedicated row.
    op.execute(
        sa.text(
            """
            UPDATE record AS r
            SET retention_policy_id = defaults.id
            FROM
                import_run AS ir,
                retention_policy AS permanent,
                retention_policy AS defaults
            WHERE ir.report_record_id = r.id
              AND permanent.org_id = r.org_id
              AND permanent.id = (
                  encode(
                      substring(
                          digest(:policy_id_salt || r.org_id::text, 'sha256')
                          FROM 1 FOR 16
                      ),
                      'hex'
                  )::uuid
              )
              AND defaults.org_id = r.org_id
              AND defaults.name = :default_name
              AND r.retention_policy_id = permanent.id
            """
        ).bindparams(
            default_name=_DEFAULT_POLICY_NAME,
            policy_id_salt=_POLICY_ID_SALT,
        )
    )
    # Delete an unreferenced managed row. If some record, document type, or historical disposition
    # deliberately references it, retain the row under a non-reserved name instead of violating an
    # FK or losing that post-0090 retention decision.
    op.execute(
        sa.text(
            """
            DELETE FROM retention_policy AS permanent
            WHERE permanent.id = (
                      encode(
                          substring(
                              digest(:policy_id_salt || permanent.org_id::text, 'sha256')
                              FROM 1 FOR 16
                          ),
                          'hex'
                      )::uuid
                  )
              AND NOT EXISTS (
                  SELECT 1
                  FROM record AS r
                  WHERE r.retention_policy_id = permanent.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM document_type AS dt
                  WHERE dt.default_retention_policy_id = permanent.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM disposition_event AS de
                  WHERE de.policy_id = permanent.id
              )
            """
        ).bindparams(policy_id_salt=_POLICY_ID_SALT)
    )
    op.execute(
        sa.text(
            """
            UPDATE retention_policy AS managed
            SET
                name = (
                    :preserved_managed_prefix
                    || managed.id::text
                    || ':'
                    || encode(gen_random_bytes(8), 'hex')
                    || ')'
                ),
                updated_at = now()
            WHERE managed.name = :policy_name
              AND managed.id = (
                  encode(
                      substring(
                          digest(:policy_id_salt || managed.org_id::text, 'sha256')
                          FROM 1 FOR 16
                      ),
                      'hex'
                  )::uuid
              )
            """
        ).bindparams(
            policy_name=_POLICY_NAME,
            policy_id_salt=_POLICY_ID_SALT,
            preserved_managed_prefix=_PRESERVED_MANAGED_PREFIX,
        )
    )
    # Restore the name of a pre-0090 collision if the user left our preservation marker intact.
    # Every other field (and every FK reference) stayed on the original row throughout.
    op.execute(
        sa.text(
            """
            UPDATE retention_policy AS legacy
            SET name = :policy_name, updated_at = now()
            WHERE legacy.name LIKE (
                      :preserved_user_prefix || legacy.id::text || ':%'
                  )
              AND NOT EXISTS (
                  SELECT 1
                  FROM retention_policy AS occupied
                  WHERE occupied.org_id = legacy.org_id
                    AND occupied.name = :policy_name
              )
            """
        ).bindparams(
            policy_name=_POLICY_NAME,
            preserved_user_prefix=_PRESERVED_USER_PREFIX,
        )
    )
