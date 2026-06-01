"""The append-only signature_event table (slice S5, doc 14 §8, doc 04 §4.2, register R2).

A Part-11-shaped record of an approval/release/obsolete decision, bound to the signed bytes via
``content_digest``. **Append-only**: rescission is via ``voided_by``/``voided_reason``,
never a DELETE (the DB-grant REVOKE that makes this structural lands in S6 with ``audit_event``).
The subject is polymorphic — ``signed_object_type`` + ``signed_object_id`` (no FK; doc 14 §8 governs
the shape over doc 18 §15.4's typed-FK form). The Part-11 columns (``reauth_at`` / ``manifest`` /
``crypto_signature`` / ``prev_signature_hash`` / ``signature_hash``) are present but NULL in v1.

``signer_user_id`` is **nullable**: a future-dated release activated by the Beat sweep runs as
the system principal and carries no human signer (the human accountability is the prior approval
signature). Manual approve/release/obsolete always carry the acting user.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._signature_enums import (
    SignatureMeaning,
    SignatureMethod,
    SignedObjectType,
    signature_meaning_enum,
    signature_method_enum,
    signed_object_type_enum,
)


class SignatureEvent(Base):
    __tablename__ = "signature_event"
    __table_args__ = (
        Index(
            "ix_signature_event_signed_object_type_signed_object_id",
            "signed_object_type",
            "signed_object_id",
        ),
        Index("ix_signature_event_org_id_signer_user_id", "org_id", "signer_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    signer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    on_behalf_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    signed_object_type: Mapped[SignedObjectType] = mapped_column(
        signed_object_type_enum, nullable=False
    )
    signed_object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    meaning: Mapped[SignatureMeaning] = mapped_column(signature_meaning_enum, nullable=False)
    intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[SignatureMethod] = mapped_column(signature_method_enum, nullable=False)
    content_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Reserved Part-11 columns — present but never populated in v1 (D3).
    reauth_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    crypto_signature: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    prev_signature_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    signature_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    voided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    voided_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
