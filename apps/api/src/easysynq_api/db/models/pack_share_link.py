"""The ``pack_share_link`` — a time-boxed, revocable external-delivery grant (slice S-pack-2).

doc 06 §7.4 (UJ-7): an external auditor (Olsen) receives a **time-boxed, read-only** link to a
sealed Evidence Pack — never standing access to the live vault. This row is that grant: who a pack
was shared with, when it expires, whether it was revoked, and how often it has been downloaded.

The bearer credential is an **Ed25519-signed token** (``services/packs/share_token.py``, the S7c
verify-token idiom, domain-separated) carried OUTSIDE the PEP — the public guest endpoints
(``/api/v1/evidence-packs/shared``) verify the signature, then consult THIS row for the
authoritative, **revocable** state. The raw token is **never stored** — only ``token_digest`` (its
SHA-256), re-derived + constant-time-compared, so a leaked DB cannot reconstruct a working link.

State (ACTIVE / EXPIRED / REVOKED) is **derived** from the nullable timestamps (the S-rec-2
``worm_destroy_request`` precedent — no status enum). The pack is immutable + RETAIN_PERMANENT, so
revoking a link never affects the sealed artefact (doc 06 §7.4 "frozen snapshot"). ``pack_id`` is
RESTRICT (a pack with live links can't be deleted out from under them; packs aren't deletable).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class PackShareLink(Base):
    __tablename__ = "pack_share_link"
    __table_args__ = (
        # The public guest endpoint looks a link up by the SHA-256 of the presented token (unique).
        Index("ix_pack_share_link_token_digest", "token_digest", unique=True),
        Index("ix_pack_share_link_pack_id", "pack_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_pack.id", ondelete="RESTRICT"), nullable=False
    )
    # SHA-256 (lowercase hex) of the issued token — the token is returned ONCE and never stored.
    token_digest: Mapped[str] = mapped_column(Text, nullable=False)
    # An audit-trail label for who the link was shared with (e.g. the auditor's email). Optional.
    recipient: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0, nullable=False
    )
    last_downloaded_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def state(self, *, now: datetime.datetime) -> str:
        """ACTIVE / REVOKED / EXPIRED — derived (no status column); revoked beats expired."""
        if self.revoked_at is not None:
            return "REVOKED"
        if now >= self.expires_at:
            return "EXPIRED"
        return "ACTIVE"

    def is_live(self, *, now: datetime.datetime) -> bool:
        return self.state(now=now) == "ACTIVE"
