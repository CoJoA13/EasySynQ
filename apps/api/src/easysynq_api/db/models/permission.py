"""The permission catalog — the atomic unit of access (``resource.action``).

**Global reference data, deliberately without ``org_id``.** The v1 catalog is closed
(doc 07 §3, register R5) and identical for every org, so ``key`` is globally unique
(doc 14 §15.1) — exactly like the read-only ``clause`` catalog. ``is_system_domain``
drives the two-tier grant guard (R35); ``sig_hook`` marks the Part-11 signature actions;
``finest_scope`` records the narrowest scope a grant of this permission may carry.
Seeded, verbatim, by migration ``0004_seed_authz``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.authz.types import ScopeLevel
from ..base import Base
from ._authz_enums import scope_level_enum


class Permission(Base):
    __tablename__ = "permission"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(Text, unique=True)
    resource: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    is_system_domain: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sod_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sig_hook: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    finest_scope: Mapped[ScopeLevel] = mapped_column(scope_level_enum, nullable=False)
