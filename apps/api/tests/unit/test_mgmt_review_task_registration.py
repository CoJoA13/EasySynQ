"""The management-review cadence sweep Celery task is registered AND Beat-scheduled daily (the
tasks/__init__ registration rule + the byte-for-byte task-name match across the decorator, the
beat_schedule entry, and this pin — a mismatch publishes to a dead name)."""

from easysynq_api.tasks import app


def test_mgmt_review_sweep_task_is_registered() -> None:
    assert "easysynq.documents.mgmt_review_sweep" in app.tasks


def test_mgmt_review_sweep_is_beat_scheduled_daily() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.documents.mgmt_review_sweep") == 86400.0
