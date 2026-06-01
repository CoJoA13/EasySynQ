"""Separation-of-duties constraints (doc 14 §3 / doc 07 §7).

The TABLE ships in S2 so the schema is final, but the constraints are **seeded and
enforced in S5** (doc 18 §7) — the PDP's SoD step is a no-op pass-through until then.
Each row declares two incompatible duties (``duty_a``/``duty_b``) bound to the same
target (``target_binding``); ``severity`` chooses hard-deny vs flag-and-require-reason.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._authz_enums import SodSeverity, SodTargetBinding, sod_severity_enum, sod_target_binding_enum


class SodConstraint(Base):
    __tablename__ = "sod_constraint"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duty_a: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    duty_b: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    relation: Mapped[str] = mapped_column(Text, default="SAME_PRINCIPAL_FORBIDDEN", nullable=False)
    target_binding: Mapped[SodTargetBinding] = mapped_column(
        sod_target_binding_enum, nullable=False
    )
    severity: Mapped[SodSeverity] = mapped_column(sod_severity_enum, nullable=False)
    org_overridable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
