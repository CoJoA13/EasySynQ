"""The review-sweep Celery task is registered AND Beat-scheduled (the tasks/__init__ rule)."""

from easysynq_api.tasks import app


def test_review_sweep_task_is_registered() -> None:
    assert "easysynq.documents.review_sweep" in app.tasks


def test_review_sweep_is_beat_scheduled_daily() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.documents.review_sweep") == 86400.0
