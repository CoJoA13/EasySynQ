"""The chain-linker's safe-prefix cursor (CR-2, migration 0071) — a singleton (``id`` is always 1).

Owned by the decoupled chain-linker (``services/audit/linker``): ``safe_watermark`` is the highest
``audit_event.id`` proven decided (committed-visible or rolled back), and ``stall_xmax`` /
``stall_ceiling`` carry the two-snapshot rollback proof across ticks (see the ``watermark`` module
for the algorithm). Only the ``easysynq_linker`` role reads/writes it (the migration's explicit
grant); the append-only trail's other roles never touch it.
"""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, DateTime, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AuditChainCursor(Base):
    __tablename__ = "audit_chain_cursor"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    safe_watermark: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stall_xmax: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stall_ceiling: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
