"""Roles and their two join tables (doc 14 §3; names per the ERD, not doc 18 M08).

A ``role`` is a convenience *bundle*, never a hard boundary (AZ-INV-4): ``role_grant``
rows tie a role to permissions with a parameterized ``scope_template`` (e.g.
``FOLDER=:assigned_folder``); ``role_assignment`` binds a role to a user and concretizes
that template via ``bound_scope``. The PEP resolves both into the PDP's ``ResolvedGrant``s.
``is_reserved`` protects the seeded ADMIN / QMS-Owner roles from deletion.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Role(Base):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_role_org_id_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_reserved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RoleGrant(Base):
    __tablename__ = "role_grant"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "role_id", "permission_id", name="uq_role_grant_org_id_role_id_permission_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("role.id", ondelete="RESTRICT"),
        nullable=False,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permission.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope_template: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class RoleAssignment(Base):
    __tablename__ = "role_assignment"

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
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("role.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bound_scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
