"""The Notification family ORM models (S-notify-1, doc 10 §9, R53).

Operational/mutable tables (like ``task`` — NO WORM, no hash chain, no blob bytes): ``notification``
(durable in-app awareness, read/unread), ``notification_email`` (the email delivery ledger, 0..1 per
notification), ``notification_template`` (versioned, global, seeded; render source of truth), and
``notification_preference`` (the per-user master email toggle, absence ⇒ enabled). See spec §3.
S-notify-3a adds digest markers, per-class digest modes, quiet hours, and email-kind columns.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Time,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._notification_enums import (
    NotificationDigestMode,
    NotificationEmailKind,
    NotificationEmailStatus,
    notification_digest_mode_enum,
    notification_email_kind_enum,
    notification_email_status_enum,
)


class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (
        Index(
            "ix_notification_recipient_unread",
            "recipient_user_id",
            "read_at",
            "created_at",
        ),
        # The dedup partial-unique INDEX is created in the migration + excluded in env.py (an
        # IS-NOT-NULL predicate round-trips wrong if declared here — the 0024 lesson, spec §3.1).
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id", ondelete="RESTRICT", name="fk_notification_org_id_organization"
        ),
        nullable=False,
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.id", ondelete="RESTRICT", name="fk_notification_recipient_user_id_app_user"
        ),
        nullable=False,
    )
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="RESTRICT", name="fk_notification_task_id_task"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    deep_link: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "notification_template.id",
            ondelete="RESTRICT",
            name="fk_notification_template_id_notification_template",
        ),
        nullable=True,
    )
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    read_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # S-notify-3a: digest scheduling markers (NULL ⇒ not yet enrolled in a digest window).
    digest_due_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    digested_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NotificationEmail(Base):
    __tablename__ = "notification_email"
    __table_args__ = (Index("ix_notification_email_status_next", "status", "next_attempt_at"),)
    # The partial-unique index uq_notification_email_one_per_notification (WHERE notification_id IS
    # NOT NULL) is created in the migration + excluded in env.py (same IS-NOT-NULL lesson as
    # uq_notification_dedup_task; declared here it round-trips wrong — S-notify-3a/0064).

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id", ondelete="RESTRICT", name="fk_notification_email_org_id_organization"
        ),
        nullable=False,
    )
    # S-notify-3a: nullable (digest emails have no single source notification).
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "notification.id",
            ondelete="RESTRICT",
            name="fk_notification_email_notification_id_notification",
        ),
        nullable=True,
    )
    recipient_email: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[NotificationEmailStatus] = mapped_column(
        notification_email_status_enum,
        server_default=text("'PENDING'"),
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    next_attempt_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # S-notify-3a: digest-email fields.
    email_kind: Mapped[NotificationEmailKind] = mapped_column(
        notification_email_kind_enum, server_default=text("'single'"), nullable=False
    )
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.id",
            ondelete="RESTRICT",
            name="fk_notification_email_recipient_user_id_app_user",
        ),
        nullable=True,
    )
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class NotificationTemplate(Base):
    __tablename__ = "notification_template"
    __table_args__ = (
        Index(
            "uq_notification_template_one_effective",
            "event_key",
            "locale",
            unique=True,
            postgresql_where=text("is_effective"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    locale: Mapped[str] = mapped_column(Text, server_default=text("'en'"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_effective: Mapped[bool] = mapped_column(Boolean, server_default=true(), nullable=False)
    in_app_title: Mapped[str] = mapped_column(Text, nullable=False)
    in_app_body: Mapped[str] = mapped_column(Text, nullable=False)
    email_subject: Mapped[str] = mapped_column(Text, nullable=False)
    email_body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationPreference(Base):
    __tablename__ = "notification_preference"
    __table_args__ = (
        # Bare token → ck_notification_preference_digest_hour via the db/base.py naming convention.
        CheckConstraint("digest_hour >= 0 AND digest_hour <= 23", name="digest_hour"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.id", ondelete="RESTRICT", name="fk_notification_preference_user_id_app_user"
        ),
        primary_key=True,
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, server_default=true(), nullable=False)
    # S-notify-3a: per-class digest modes (NULL ⇒ code default = IMMEDIATE).
    digest_mode_action_required: Mapped[NotificationDigestMode | None] = mapped_column(
        notification_digest_mode_enum, nullable=True
    )
    digest_mode_awareness: Mapped[NotificationDigestMode | None] = mapped_column(
        notification_digest_mode_enum, nullable=True
    )
    digest_mode_critical: Mapped[NotificationDigestMode | None] = mapped_column(
        notification_digest_mode_enum, nullable=True
    )
    digest_mode_admin_ops: Mapped[NotificationDigestMode | None] = mapped_column(
        notification_digest_mode_enum, nullable=True
    )
    # S-notify-3a: digest scheduling (hour 0-23 UTC by default; timezone for display/offset).
    digest_hour: Mapped[int] = mapped_column(SmallInteger, server_default=text("8"), nullable=False)
    timezone: Mapped[str] = mapped_column(Text, server_default=text("'UTC'"), nullable=False)
    # S-notify-3a: quiet hours (NULL ⇒ no quiet window configured).
    quiet_start: Mapped[datetime.time | None] = mapped_column(Time, nullable=True)
    quiet_end: Mapped[datetime.time | None] = mapped_column(Time, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now()
    )
