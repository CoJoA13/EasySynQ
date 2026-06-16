"""Pin: the scheduled restore-test task is registered AND beat-scheduled at the configured cadence
(a task-name / registration / beat-entry mismatch publishes `.delay` to a dead name → the row never
runs — the same trap the other task-registration pins guard)."""

from easysynq_api.config import get_settings
from easysynq_api.tasks.app import app


def test_backup_tasks_are_registered() -> None:
    assert "easysynq.backup.run" in app.tasks  # nightly durable backup
    assert "easysynq.backup.restore_test" in app.tasks  # on-demand G-C drill
    assert "easysynq.backup.scheduled_restore_test" in app.tasks  # the new scheduled drill


def test_scheduled_restore_test_is_beat_scheduled_at_the_configured_interval() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.backup.scheduled_restore_test") == float(
        get_settings().restore_test_interval_seconds
    )


def test_scheduled_restore_test_defaults_weekly() -> None:
    # Heavy drill (scratch DB + full pg_restore + blob re-hash) → deliberately infrequent.
    assert get_settings().restore_test_interval_seconds == 604800
