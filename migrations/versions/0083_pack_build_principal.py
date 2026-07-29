"""Persist the initiating principal and source-IP context for Evidence Pack builds.

Issue #363 closes two worker-principal gaps: a retrying generator could borrow the original
creator's ``record.read`` grants, and the worker could not re-authorize Finding/CAPA subjects after
the generate commit. The task continues to carry only ``pack_id``; these columns make the locked
pack row authoritative across delayed delivery, acks-late replay, and a later retry.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, UUID

revision: str = "0083_pack_build_principal"
down_revision: str | None = "0082_pack_legal_erasure"
branch_labels: str | None = None
depends_on: str | None = None

_BUILD_REQUESTER_FK = "fk_evidence_pack_build_requested_by_app_user"


def upgrade() -> None:
    op.add_column(
        "evidence_pack",
        sa.Column("build_requested_by", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "evidence_pack",
        sa.Column("build_source_ip", INET(), nullable=True),
    )
    # Existing non-DRAFT rows already crossed the old generate/build boundary, whose only
    # available principal was created_by. Preserve that behavior after an in-place upgrade;
    # future generate attempts overwrite the field atomically with their real caller.
    op.execute(
        """
        UPDATE evidence_pack
        SET build_requested_by = created_by
        WHERE status::text <> 'DRAFT'
          AND build_requested_by IS NULL
        """
    )
    op.create_foreign_key(
        _BUILD_REQUESTER_FK,
        "evidence_pack",
        "app_user",
        ["build_requested_by"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(_BUILD_REQUESTER_FK, "evidence_pack", type_="foreignkey")
    op.drop_column("evidence_pack", "build_source_ip")
    op.drop_column("evidence_pack", "build_requested_by")
