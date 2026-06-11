"""The ack-sweep Celery task is registered AND Beat-scheduled (the tasks/__init__ rule)."""

from easysynq_api.tasks import app


def test_ack_sweep_task_is_registered() -> None:
    assert "easysynq.ack.sweep" in app.tasks


def test_ack_sweep_is_beat_scheduled_daily() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.ack.sweep") == 86400.0
