"""Awareness-event outbox (slice S-notify-5a, doc 10 §9.2).

One row per QMS lifecycle awareness fact (v1: ``doc.released``). Written best-effort + atomic
with the release inside the SERIALIZABLE ``_cutover`` (services/vault/lifecycle.py), then fanned
out by the ``awareness_fan_out`` Beat (services/notifications/fanout.py): the worker resolves the
read-scoped audience and creates per-recipient notification rows, stamping ``fanned_out_at`` once.

Created by migration 0066. The app role holds INSERT/SELECT/UPDATE but **not DELETE** (the 0066
REVOKE counters 0010's ``ALTER DEFAULT PRIVILEGES`` auto-grant). The claim index
``ix_awareness_event_pending`` is migration-managed (a partial index round-trips wrong if declared
on the ORM) — see migrations/env.py.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AwarenessEvent(Base):
    __tablename__ = "awareness_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id", ondelete="RESTRICT", name="fk_awareness_event_org_id_organization"
        ),
        nullable=False,
    )
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # The Effective document_version.id at release — the dedup discriminator so each new version
    # re-notifies (spec §5). Plain uuid (no FK — operational outbox, mirrors subject_id); always set
    # for doc.released, nullable for future non-version awareness keys.
    subject_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.id", ondelete="RESTRICT", name="fk_awareness_event_actor_user_id_app_user"
        ),
        nullable=True,
    )
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fanned_out_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
