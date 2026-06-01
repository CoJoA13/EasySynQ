"""Atomic per-(type, area) identifier sequence (doc 04 §7, doc 18 M17).

A counter row per ``(org_id, type_code, area_code)``; the next ``{SEQ}`` is allocated with a
single ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` so concurrent document creation never
collides. (Functionally the per-(type,area) PG sequence of doc 18, but migration-friendly and
single-statement-atomic.)
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class NumberingCounter(Base):
    __tablename__ = "numbering_counter"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    type_code: Mapped[str] = mapped_column(Text, primary_key=True)
    area_code: Mapped[str] = mapped_column(Text, primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
