"""The standard/framework a documented_information row conforms to (doc 14 §4).

v1 ships a single ``iso9001:2015`` row (seeded). ``framework_id`` on
``documented_information`` is the multi-standard discriminator (D3) — present and real now,
so 13485/14001/45001/IATF are additive seed data later, never a schema rewrite.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Framework(Base):
    __tablename__ = "framework"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_framework_org_id_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "iso9001:2015"
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_authorable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
