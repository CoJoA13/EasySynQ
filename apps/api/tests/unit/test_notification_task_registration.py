"""The outbox-drain task is registered AND Beat-scheduled (the tasks/__init__ rule)."""

from easysynq_api.tasks import app


def test_outbox_drain_registered() -> None:
    assert "easysynq.notifications.outbox_drain" in app.tasks


def test_outbox_drain_beat_scheduled() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.notifications.outbox_drain") == 120.0
