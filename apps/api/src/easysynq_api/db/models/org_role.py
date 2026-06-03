"""The QMS organizational-role table — RACI/accountability reference data (slice S9c, doc 02 §3.4).

An ``org_role`` is a *QMS responsibility* ("Process Owner of Purchasing", "Top Management") used for
accountability and assignee resolution (Clause 5.3) — it is **NOT** a permission role and is never
wired to the PDP/PEP (doc 02 §3.4 "do not conflate the two kinds of role"). The authorization layer
is ``role``/``role_grant``/``role_assignment`` (doc 14 §3); ``org_role`` is separate reference data
that ``process.owner_org_role_id`` points at. Built **empty-but-present** in S9c (no authoring
endpoint yet); owner-assignment + ``org_role_assignment`` are deferred (R-row, doc 10).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class OrgRole(Base):
    __tablename__ = "org_role"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_org_role_org_id_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
