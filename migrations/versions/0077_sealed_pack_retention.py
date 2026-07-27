"""Give sealed evidence packs a dedicated immutable permanent-retention policy.

Evidence-pack records previously pinned the org-configurable System Default policy. An org that
changed that policy before sealing a pack could therefore create a disposable pack even though the
pack contract says ``RETAIN_PERMANENT``. Seed one reserved policy per org, normalize any unlikely
pre-existing same-name row to the preservation-maximal values, and re-pin existing pack records.

New organizations are covered by the matching lazy ensure in the pack build path. This migration
changes data only; there is no schema, enum, grant, or permission-key change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0077_sealed_pack_retention"
down_revision: str | None = "0076_import_owner_snapshot"
branch_labels: str | None = None
depends_on: str | None = None

_PACK_POLICY_NAME = "Sealed Evidence Pack Retention"
_DEFAULT_POLICY_NAME = "System Default Retention"


def upgrade() -> None:
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
                gen_random_uuid(),
                o.id,
                :pack_name,
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
            ON CONFLICT (org_id, name) DO UPDATE SET
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
        ).bindparams(pack_name=_PACK_POLICY_NAME)
    )
    op.execute(
        sa.text(
            """
            UPDATE record AS r
            SET retention_policy_id = permanent.id
            FROM evidence_pack AS ep, retention_policy AS permanent
            WHERE ep.pack_record_id = r.id
              AND r.org_id = ep.org_id
              AND permanent.org_id = ep.org_id
              AND permanent.name = :pack_name
            """
        ).bindparams(pack_name=_PACK_POLICY_NAME)
    )


def downgrade() -> None:
    # Restore pack records to the historical fallback before removing the dedicated row. If an
    # unrelated legacy record used this formerly-unreserved name, retain its policy row rather than
    # violating the record FK on downgrade.
    op.execute(
        sa.text(
            """
            UPDATE record AS r
            SET retention_policy_id = defaults.id
            FROM
                evidence_pack AS ep,
                retention_policy AS permanent,
                retention_policy AS defaults
            WHERE ep.pack_record_id = r.id
              AND r.org_id = ep.org_id
              AND permanent.org_id = ep.org_id
              AND permanent.name = :pack_name
              AND defaults.org_id = ep.org_id
              AND defaults.name = :default_name
              AND r.retention_policy_id = permanent.id
            """
        ).bindparams(pack_name=_PACK_POLICY_NAME, default_name=_DEFAULT_POLICY_NAME)
    )
    op.execute(
        sa.text(
            """
            DELETE FROM retention_policy AS permanent
            WHERE permanent.name = :pack_name
              AND NOT EXISTS (
                  SELECT 1
                  FROM record AS r
                  WHERE r.retention_policy_id = permanent.id
              )
            """
        ).bindparams(pack_name=_PACK_POLICY_NAME)
    )
