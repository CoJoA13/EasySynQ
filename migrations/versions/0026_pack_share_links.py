"""evidence-pack external delivery: pack_share_link + portfolio col + PACK_SHARED/DOWNLOADED/REVOKED

Completes UJ-7 (slice S-pack-2, doc 06 §7.4): a sealed Evidence Pack can be delivered to an external
auditor via a **time-boxed, revocable** Ed25519 share-link, plus an on-demand PDF portfolio variant.

1. **pack_share_link** — the durable, revocable delivery grant (doc 06 §7.4): the bearer token's
   ``token_digest`` (the raw token is never stored), the ``recipient`` audit label, ``expires_at``, and
   the ``revoked_at``/``revoked_by``/``revoke_reason`` + ``download_count``/``last_downloaded_at`` trail.
   State is derived from the nullable timestamps (the 0024 ``worm_destroy_request`` precedent). ``pack_id``
   is RESTRICT (a pack with live links isn't deletable); both indexes are plain b-tree → no ``env.py`` change.
2. **evidence_pack.portfolio_blob_sha256** — a nullable pointer to the cached single-PDF portfolio (a
   DERIVED view in the non-WORM renditions bucket; NOT part of the seal). Plain Text, NO FK (the
   ``zip_blob_sha256`` R27 precedent).
3. **PACK_SHARED / PACK_DOWNLOADED / PACK_SHARE_REVOKED event_type** — additive ``ALTER TYPE … ADD VALUE``
   (the 0011-0025 pattern; pack-share lifecycle keys on the existing ``evidence_pack`` audit_object_type →
   no new object type). A from-scratch ``upgrade head`` rebuilds ``event_type`` from EVENT_TYPE_VALUES, so
   the members live in the ORM enum too.
4. **Explicit GRANTs** — SELECT/INSERT/UPDATE/DELETE on ``pack_share_link`` to ``easysynq_app`` (the API
   mints/lists/revokes; the public guest endpoint runs as the app role and UPDATEs the download counter).
   Belt-and-suspenders over 0010's ALTER DEFAULT PRIVILEGES (the 0024/0025 child-table precedent), guarded
   so a role-less CI DB doesn't error.

Migration notes: the three ADD VALUEs are never used by a row in THIS migration (the PG16 in-txn rule is
satisfied). The downgrade clears + drops ``pack_share_link`` (a leaf table — no inbound FK) and drops the
column; the ADD VALUEs are irreversible in PostgreSQL → no-op (a re-upgrade rebuilds the type wholesale).

Revision ID: 0026_pack_share_links
Revises: 0025_evidence_packs
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_pack_share_links"
down_revision: str | None = "0025_evidence_packs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

# Listed literally (the 0025 ADD-VALUE precedent); they must match the ORM EventType members, which
# they do. The CREATE-TYPE path (none here) is what sources tuples from the ORM *_VALUES.
_NEW_EVENT_TYPES = ("PACK_SHARED", "PACK_DOWNLOADED", "PACK_SHARE_REVOKED")


def upgrade() -> None:
    # 1. pack_share_link — the time-boxed, revocable external-delivery grant.
    op.create_table(
        "pack_share_link",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pack_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_digest", sa.Text(), nullable=False),
        sa.Column("recipient", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column("download_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_pack_share_link_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pack_id"],
            ["evidence_pack.id"],
            name="fk_pack_share_link_pack_id_evidence_pack",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_pack_share_link_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["app_user.id"],
            name="fk_pack_share_link_revoked_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pack_share_link"),
    )
    op.create_index(
        "ix_pack_share_link_token_digest", "pack_share_link", ["token_digest"], unique=True
    )
    op.create_index("ix_pack_share_link_pack_id", "pack_share_link", ["pack_id"])

    # 2. evidence_pack.portfolio_blob_sha256 — the cached PDF portfolio pointer (derived, nullable).
    op.add_column("evidence_pack", sa.Column("portfolio_blob_sha256", sa.Text(), nullable=True))

    # 3. Additive enum values (never used by a row here; PG16 in-txn rule satisfied).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 4. Explicit least-privilege grants for the non-owner app role (guarded for a role-less CI DB).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON pack_share_link TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # pack_share_link is a leaf (no inbound FK), but clear it explicitly for the populated-DB downgrade.
    op.execute("DELETE FROM pack_share_link")
    op.drop_index("ix_pack_share_link_pack_id", table_name="pack_share_link")
    op.drop_index("ix_pack_share_link_token_digest", table_name="pack_share_link")
    op.drop_table("pack_share_link")
    op.drop_column("evidence_pack", "portfolio_blob_sha256")
    # The event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's downgrade DROPs the type
    # wholesale, so the up↔down round-trip still passes; a re-upgrade rebuilds it from EVENT_TYPE_VALUES).
