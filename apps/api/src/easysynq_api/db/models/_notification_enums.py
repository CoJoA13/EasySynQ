"""Native-PG enum bindings for the Notification family (S-notify-1, doc 10 §9, R53).

Only ONE enum: the email delivery-ledger status (a closed set). ``event_key`` and ``subject_type``
on the notification rows are deliberately TEXT (not enums) so later slices add events/subjects with
no ALTER TYPE migration; their canonical values live in the code registry
(``services/notifications/constants.py``). Created by the Alembic migration; referenced here with
``create_type=False``.
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
