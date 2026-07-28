from __future__ import annotations

import pytest

from easysynq_api.config import Settings
from easysynq_api.services.notifications.delivery import (
    email_delivery_ready,
    smtp_transport_configured,
)


@pytest.mark.parametrize(
    ("org_email_enabled", "smtp_host", "expected"),
    [
        (False, "", False),
        (False, "smtp.example.test", False),
        (True, "", False),
        (True, "smtp.example.test", True),
    ],
)
def test_email_delivery_ready_requires_org_enablement_and_smtp(
    org_email_enabled: bool,
    smtp_host: str,
    expected: bool,
) -> None:
    settings = Settings(smtp_host=smtp_host)
    assert smtp_transport_configured(settings) is bool(smtp_host)
    assert (
        email_delivery_ready(
            org_email_enabled=org_email_enabled,
            settings=settings,
        )
        is expected
    )
