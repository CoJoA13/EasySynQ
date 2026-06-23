"""Notification tasks are registered AND Beat-scheduled (the tasks/__init__ rule)."""

from easysynq_api.tasks import app


def test_outbox_drain_registered() -> None:
    assert "easysynq.notifications.outbox_drain" in app.tasks


def test_outbox_drain_beat_scheduled() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.notifications.outbox_drain") == 120.0


def test_digest_sweep_registered() -> None:
    assert "easysynq.notifications.digest_sweep" in app.tasks


def test_digest_sweep_beat_scheduled() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.notifications.digest_sweep") == 3600.0


def test_timer_sweep_registered() -> None:
    assert "easysynq.notifications.timer_sweep" in app.tasks


def test_timer_sweep_beat_scheduled() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.notifications.timer_sweep") == 300.0
