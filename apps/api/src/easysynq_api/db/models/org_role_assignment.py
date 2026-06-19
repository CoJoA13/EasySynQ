"""The owner-assignment binding — a user's RACI accountability for a process (S-owner-assignment-1).

An ``org_role_assignment`` binds an ``app_user`` to an ``org_role`` (a QMS responsibility such as
"Process Owner"), optionally scoped to a concrete ``process`` (Clause 5.3 accountability; doc 02
§3.4 / doc 14 §3). It is the **RACI/accountability fact only** — NOT itself a permission grant
(``org_role`` is never wired to the PDP/PEP; "do not conflate the two kinds of role"). The
owner-assignment action records this row AND, separately, mints/extends the concrete PROCESS-scoped
``role_assignment.bound_scope`` that carries the actual authorization (substituting the seeded
``:assignment_process`` placeholder). ``process_id`` is **nullable** — a global org-role (e.g. Top
Management) has no process binding; a process owner carries the concrete process id. The
``UNIQUE(org_role_id, user_id, process_id)`` makes the binding idempotent (Postgres treats a NULL
``process_id`` as distinct, which is fine for the org-wide RACI case).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class OrgRoleAssignment(Base):
    __tablename__ = "org_role_assignment"
    __table_args__ = (
        UniqueConstraint(
            "org_role_id",
            "user_id",
            "process_id",
            name="uq_org_role_assignment_org_role_id_user_id_process_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    org_role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_role.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
