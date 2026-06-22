"""Native-PG enum bindings for the Notification family (S-notify-1, doc 10 §9, R53).

Enums: the email delivery-ledger status (closed set), the per-class digest mode, and the email-row
kind (single vs digest). ``event_key`` and ``subject_type`` on the notification rows are TEXT (not
enums) so later slices add events/subjects with no ALTER TYPE migration; their canonical values live
in the code registry (``services/notifications/constants.py``). Created by Alembic migrations;
referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class NotificationEmailStatus(enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


notification_email_status_enum = SAEnum(
    NotificationEmailStatus,
    name="notification_email_status",
    values_callable=_vals,
    create_type=False,
)

NOTIFICATION_EMAIL_STATUS_VALUES = tuple(_vals(NotificationEmailStatus))


class NotificationDigestMode(enum.Enum):
    IMMEDIATE = "immediate"
    DAILY = "daily"
    OFF = "off"


class NotificationEmailKind(enum.Enum):
    SINGLE = "single"
    DIGEST = "digest"


notification_digest_mode_enum = SAEnum(
    NotificationDigestMode,
    name="notification_digest_mode",
    values_callable=_vals,
    create_type=False,
)
notification_email_kind_enum = SAEnum(
    NotificationEmailKind,
    name="notification_email_kind",
    values_callable=_vals,
    create_type=False,
)

NOTIFICATION_DIGEST_MODE_VALUES = tuple(_vals(NotificationDigestMode))
NOTIFICATION_EMAIL_KIND_VALUES = tuple(_vals(NotificationEmailKind))
