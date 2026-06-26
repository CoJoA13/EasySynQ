"""SLA policy reference table for task-timer escalation (slice S-notify-4, doc 10 §9.5).

One row per (org, task_type) — seed-managed reference data. Holds the INTERVAL offsets the
``timer_sweep`` Beat uses to enqueue reminder / overdue / escalation notifications:

* ``remind_1_before``  — send the first "due soon" notification this long before ``task.due_at``.
* ``remind_2_before``  — send the second "due soon" notification this long before ``task.due_at``.
* ``escalate_1_after`` — escalate the task to the manager/QM this long AFTER ``task.due_at``.

Created by migration 0065. The app role holds SELECT-only (REVOKE block in 0065 counters the
0010 ``ALTER DEFAULT PRIVILEGES`` auto-grant).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Interval, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._workflow_enums import TaskType, task_type_enum


class SlaPolicy(Base):
    __tablename__ = "sla_policy"
    __table_args__ = (UniqueConstraint("org_id", "task_type", name="uq_sla_policy_org_task_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id", ondelete="RESTRICT", name="fk_sla_policy_org_id_organization"
        ),
        nullable=False,
    )
    task_type: Mapped[TaskType] = mapped_column(task_type_enum, nullable=False)
    remind_1_before: Mapped[datetime.timedelta | None] = mapped_column(Interval, nullable=True)
    remind_2_before: Mapped[datetime.timedelta | None] = mapped_column(Interval, nullable=True)
    escalate_1_after: Mapped[datetime.timedelta | None] = mapped_column(Interval, nullable=True)
    escalate_2_after: Mapped[datetime.timedelta | None] = mapped_column(Interval, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
