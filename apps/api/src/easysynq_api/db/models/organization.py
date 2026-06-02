"""The organization (singleton tenant in v1; ``org_id`` everywhere FKs here)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Organization(Base):
    __tablename__ = "organization"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    legal_name: Mapped[str] = mapped_column(String(255))
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # [A-Z0-9-]
    # IANA tz set in the setup wizard (S8a, doc 08 §6); authoritative for effective-date
    # interpretation (R8). Defaults to UTC until the org profile is completed.
    timezone: Mapped[str] = mapped_column(String(64), server_default="UTC", default="UTC")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
