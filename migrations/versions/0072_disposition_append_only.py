"""Batch 4 (2026-07-22 review): make disposition_event append-only for the app role.

Migration 0024 granted SELECT, INSERT, UPDATE, DELETE on ``disposition_event`` (the immutable
disposition / R27 WORM-destroy tombstone) to the non-owner ``easysynq_app`` role, but no migration
ever REVOKE'd UPDATE/DELETE — unlike every sibling append-only evidence table (``audit_event`` /
``signature_event`` / ``audit_checkpoint`` in 0010, ``capa_stage``, ``dcr_stage_event``,
``acknowledgement`` in 0048, ``kpi_measurement`` in 0049), which are all REVOKE'd. The app only ever
INSERTs a tombstone (``_write_tombstone``) and SELECTs it; leaving UPDATE/DELETE lets an app-role
compromise, a SQL-injection path, or a stray statement alter or erase the legal basis + dual-control
approver history of a destroyed record — a tamper-evidence hole on the R27 legal-erasure proof.

``worm_destroy_request`` is deliberately left editable — it is NOT append-only (the approve/cancel
flow UPDATEs ``approved_by`` / ``executed_at`` / ``cancelled_*``), so only ``disposition_event`` is
locked down here.

``pg_roles``-guarded (belt-and-suspenders for a role-less DB — the 0024/0048 house style; the
``migrations`` CI job DOES run the REVOKE, since 0010 creates ``easysynq_app`` unconditionally, but
as the OWNER, so it validates syntax + the round-trip, not the denial). Privilege-only, so
``alembic check`` sees no schema drift. The behavioral denial (the app role gets SQLSTATE 42501) is
proven by ``test_disposition_event_append_only_for_app_role``, not the owner-role migrations job.
"""

from __future__ import annotations

from alembic import op

revision: str = "0072_disposition_append_only"
down_revision: str | None = "0071_audit_chain_cursor"
branch_labels: str | None = None
depends_on: str | None = None

_APP_ROLE = "easysynq_app"


def upgrade() -> None:
    # Match the sibling append-only tombstone tables: the app INSERTs + SELECTs, never mutates.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'REVOKE UPDATE, DELETE ON disposition_event FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Restore 0024's original grant (SELECT / INSERT are untouched by this migration).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT UPDATE, DELETE ON disposition_event TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )
