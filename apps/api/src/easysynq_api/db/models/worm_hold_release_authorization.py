from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CHAR, BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WormHoldReleaseAuthorization(Base):
    __tablename__ = "worm_hold_release_authorization"

    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "worm_hold_release_operation.id",
            ondelete="RESTRICT",
            name="fk_worm_hold_auth_operation_id_operation",
        ),
        primary_key=True,
    )
    canonical_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    host_operator_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    authorizing_audit_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    authorized_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    authorizer_role: Mapped[str] = mapped_column(String(64), nullable=False)
