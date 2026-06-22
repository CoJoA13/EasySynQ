"""The notification models import + register cleanly (the alembic-check __init__ rule)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_notification_models_are_registered() -> None:
    from easysynq_api.db import models

    assert models.Notification.__tablename__ == "notification"
    assert models.NotificationEmail.__tablename__ == "notification_email"
    assert models.NotificationTemplate.__tablename__ == "notification_template"
    assert models.NotificationPreference.__tablename__ == "notification_preference"
    assert models.NotificationEmailStatus.PENDING.value == "PENDING"


def test_email_status_values() -> None:
    from easysynq_api.db.models._notification_enums import NOTIFICATION_EMAIL_STATUS_VALUES

    assert NOTIFICATION_EMAIL_STATUS_VALUES == ("PENDING", "SENT", "FAILED", "SUPPRESSED")
