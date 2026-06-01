"""Off-host audit-checkpoint sink config (slice S6, doc 12 §4.6, doc 14 §12, register R13).

Models WHERE signed checkpoints are continuously mirrored off-host so a privileged operator who
controls both the live DB and the backups still cannot silently rewrite history undetected. At
least one off-host/append-only sink is **MANDATORY for any install claiming tamper-evidence**, and
is configured during setup as a **soft gate** (setup never blocked; a persistent UI warning if
absent — R13).

``connection`` holds **non-secret** config only — endpoint, bucket, region, and an ``off_host``
flag the operator sets true only when the endpoint is genuinely separate-host/external. Per doc 18
§11 D-8 the actual credential is a **separate Docker secret**, never stored/encrypted in this row
(reconciling doc 14 §12's "envelope-encrypted" note). v1 implements only the ``worm_bucket`` kind.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._audit_enums import CheckpointSinkKind, checkpoint_sink_kind_enum


class AuditCheckpointSink(Base):
    __tablename__ = "audit_checkpoint_sink"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[CheckpointSinkKind] = mapped_column(checkpoint_sink_kind_enum, nullable=False)
    connection: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=false(), nullable=False)
    last_anchored_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
