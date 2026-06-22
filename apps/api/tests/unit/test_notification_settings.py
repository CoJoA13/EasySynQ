"""SMTP + app_base_url settings exist with safe defaults (email opt-in, no host configured)."""

from __future__ import annotations

import pytest

from easysynq_api.config import Settings

pytestmark = pytest.mark.unit


def test_smtp_defaults_are_safe() -> None:
    s = Settings()
    assert s.smtp_host == ""  # unconfigured → drain treats as not-deliverable
    assert s.smtp_port == 587
    assert s.smtp_use_tls is True
    assert s.smtp_from_address == "noreply@easysynq.local"
    assert s.app_base_url == "http://localhost"
