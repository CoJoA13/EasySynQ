"""A reusable ABAC scope — the attribute boundary a grant applies to (doc 14 §3, doc 07 §5).

``level`` + ``selector`` give the structural boundary (e.g. ``FOLDER`` →
``{"folder_path": "SOPs.Purchasing"}``); ``predicates`` carry narrowing-only attribute
filters (time window, ``read_only``, ``lifecycle_state``, …). ``framework_id`` is a
reserved multi-standard hook (doc 18 §10) — nullable, no FK in MVP (all scopes iso9001).
Referenced by ``permission_override.scope_id``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.authz.types import ScopeLevel
from ..base import Base
from ._authz_enums import scope_level_enum


class Scope(Base):
    __tablename__ = "scope"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    framework_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    level: Mapped[ScopeLevel] = mapped_column(scope_level_enum, nullable=False)
    selector: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    predicates: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
