"""first_name derivation (pure) — recipient loading itself is exercised in integration."""

from __future__ import annotations

import pytest

from easysynq_api.services.notifications.recipients import _first_name

pytestmark = pytest.mark.unit


def test_first_name_from_display_name() -> None:
    assert _first_name("Priya Sharma") == "Priya"


def test_first_name_empty_falls_back() -> None:
    assert _first_name("") == "there"
    assert _first_name(None) == "there"
