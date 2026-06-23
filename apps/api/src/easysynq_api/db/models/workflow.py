"""The minimal approval-workflow cluster (slice S5, doc 14 §7, doc 10, doc 18 C7/M16).

A declarative ``WorkflowDefinition`` (versioned, data-not-code) owns ordered ``WorkflowStage`` rows.
``POST /documents/{id}/submit-review`` instantiates a ``WorkflowInstance`` (pinning the
``definition_version``) + one ``Task``; ``POST /tasks/{id}/decision`` writes a ``TaskOutcome`` (plus
a ``signature_event`` + audit) in one transaction. S5 ships only the single-stage DOCUMENT approval
path; routing/quorum/SLA/escalation reuse the same tables in later slices.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._workflow_enums import (
    TaskOutcomeKind,
    TaskState,
    TaskType,
    WorkflowStageMode,
    WorkflowSubjectType,
    task_outcome_kind_enum,
    task_state_enum,
    task_type_enum,
    workflow_stage_mode_enum,
    workflow_subject_type_enum,
)


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definition"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "key", "version", name="uq_workflow_definition_org_id_key_version"
        ),
        # At most one *effective* definition per (org, key). Standalone boolean predicate —
        # PostgreSQL normalizes ``WHERE effective = true`` to bare ``WHERE effective``, so the
        # declaration uses the bare form on both sides to keep ``alembic check`` clean.
        Index(
            "uq_workflow_definition_effective_per_key",
            "org_id",
            "key",
            unique=True,
            postgresql_where=text("effective"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    subject_type: Mapped[WorkflowSubjectType] = mapped_column(
        workflow_subject_type_enum, nullable=False
    )
    stages: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    entry_conditions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    default_sla: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class WorkflowStage(Base):
    __tablename__ = "workflow_stage"
    __table_args__ = (
        UniqueConstraint("definition_id", "key", name="uq_workflow_stage_definition_id_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definition.id", ondelete="RESTRICT"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[WorkflowStageMode] = mapped_column(workflow_stage_mode_enum, nullable=False)
    assignees: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    quorum: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    transitions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sla: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sod_author_excluded: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    signature: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class WorkflowInstance(Base):
    __tablename__ = "workflow_instance"
    __table_args__ = (
        Index(
            "ix_workflow_instance_org_id_subject_type_subject_id",
            "org_id",
            "subject_type",
            "subject_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definition.id", ondelete="RESTRICT"),
        nullable=False,
    )
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_type: Mapped[WorkflowSubjectType] = mapped_column(
        workflow_subject_type_enum, nullable=False
    )
    # Polymorphic subject (documented_information / DCR / CAPA …) — no FK by design.
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    current_state: Mapped[str] = mapped_column(Text, nullable=False)
    # The entry-context snapshot (e.g. {"severity": "Critical"}) the declarative engine evaluates
    # conditional quorum/assignees/routing against — frozen at instantiate(), since ``subject_id``
    # has
    # no FK (S-wf-engine, doc 10 §2.5). The DOCUMENT single-stage path leaves it NULL.
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)


class Task(Base):
    __tablename__ = "task"
    __table_args__ = (
        Index("ix_task_assignee_user_id_state", "assignee_user_id", "state"),
        Index("ix_task_instance_id", "instance_id"),
        Index(
            "gin_task_candidate_pool", "candidate_pool", postgresql_using="gin"
        ),  # My-Tasks ``candidate_pool @> [me]`` containment
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instance.id", ondelete="RESTRICT"), nullable=False
    )
    stage_key: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    candidate_pool: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    type: Mapped[TaskType] = mapped_column(task_type_enum, nullable=False)
    action_expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[TaskState] = mapped_column(
        task_state_enum, default=TaskState.PENDING, nullable=False
    )
    due_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # S-notify-4: timer_sweep idempotency guards — set by the sweep when each notification fires,
    # preventing double-send on re-runs. The partial index ``ix_task_timer_pending`` (migration-
    # managed, absent from __table_args__) backs the sweep query.
    remind_1_sent_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    remind_2_sent_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    overdue_notified_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalated_1_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    client_token: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaskOutcome(Base):
    __tablename__ = "task_outcome"
    __table_args__ = (
        # One decision per task — the deterministic idempotency backstop behind the
        # SELECT … FOR UPDATE in ``decide()``.
        UniqueConstraint("task_id", name="uq_task_outcome_task_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[TaskOutcomeKind] = mapped_column(task_outcome_kind_enum, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
