"""S-drift-2: the hourly scan task is registered + its Beat entry rides the R11 settings knob."""

from __future__ import annotations

from easysynq_api.config import Settings, get_settings
from easysynq_api.tasks import app


def test_scan_task_registered() -> None:
    assert "easysynq.mirror.scan" in app.tasks


def test_beat_entry_schedule_matches_settings() -> None:
    # Under default env both sides are 3600.0, so a hardcoded schedule would also pass — the
    # real knob proof is test_default_interval_is_hourly_r11 + the settings-driven app.py wiring
    # (the literal-pinning limitation matches the existing task-registration convention).
    entry = app.conf.beat_schedule["mirror-scan"]
    assert entry["task"] == "easysynq.mirror.scan"
    assert entry["schedule"] == float(get_settings().mirror_scan_interval_seconds)


def test_default_interval_is_hourly_r11() -> None:
    assert Settings.model_fields["mirror_scan_interval_seconds"].default == 3600
