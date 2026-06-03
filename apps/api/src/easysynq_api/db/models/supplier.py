"""The supplier catalog — outsourced-process counterpart (slice S9c, doc 14 §6, Cl 8.4).

A ``supplier`` is an external provider; an outsourced ``process`` (``is_outsourced=true``) links to
the supplier that performs it via ``process.outsourced_supplier_id`` (R17, ISO 9001 8.4.1 + 4.4).
Supplier *evaluations* are Records (doc 06) — not modelled here. Built **empty-but-present** in S9c
(no ``/suppliers`` authoring endpoint yet; the D-3 "create the v1 table now" strategy) so the
``process`` outsourcing FK has a real target. ``status`` values are the v1 forward-compat choice
(doc 14 §6 leaves them unspecified).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._process_enums import SupplierStatus, supplier_status_enum


class Supplier(Base):
    __tablename__ = "supplier"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SupplierStatus] = mapped_column(supplier_status_enum, nullable=False)
    re_eval_due: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
