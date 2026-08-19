from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._r27_enums import (
    R27ExecutionState,
    R27ResultCode,
    r27_execution_state_enum,
    r27_result_code_enum,
)


class R27Execution(Base):
    __tablename__ = "r27_execution"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_r27_execution_request_id"),
        UniqueConstraint("execution_id", name="uq_r27_execution_execution_id"),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_request.id", ondelete="RESTRICT"), nullable=False
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    state: Mapped[R27ExecutionState] = mapped_column(
        r27_execution_state_enum,
        server_default=text("'CLAIMED'"),
        default=R27ExecutionState.CLAIMED,
        nullable=False,
    )
    result_code: Mapped[R27ResultCode | None] = mapped_column(r27_result_code_enum, nullable=True)
    claimed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_committed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purge_started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
