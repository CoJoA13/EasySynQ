"""A direct per-user override — the ABAC layer on top of role bundles (doc 14 §3).

An override ALLOWs or DENYs one permission for one user within a concrete ``scope``.
DENY beats any role-derived ALLOW (deny-wins, register R3). ``scope_id`` is NOT NULL —
even a system-wide override points at a concrete ``level=SYSTEM`` scope row, so the PDP
never special-cases a null scope. ``valid_from``/``valid_until``/``predicates`` are the
live time-box/ABAC hook (doc 18 §10); ``require_reason`` is the pre-Part-11 toughening
(doc 07 §10, OV-3). ``created_by`` records the granter for the audit trail.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.authz.types import Effect
from ..base import Base
from ._authz_enums import grant_effect_enum


class PermissionOverride(Base):
    __tablename__ = "permission_override"
    __table_args__ = (
        Index(
            "ix_permission_override_user_id_permission_id_scope_id",
            "user_id",
            "permission_id",
            "scope_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permission.id", ondelete="RESTRICT"),
        nullable=False,
    )
    effect: Mapped[Effect] = mapped_column(grant_effect_enum, nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scope.id", ondelete="RESTRICT"),
        nullable=False,
    )
    predicates: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    valid_from: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    require_reason: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
