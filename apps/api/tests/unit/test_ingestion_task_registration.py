"""Guard the Celery task registration (S-ing-1/2): if ``tasks/ingestion.py`` is not imported in
``tasks/__init__.py``, ``.delay`` publishes to a name no worker handles → the run hangs forever.
Fails at CI rather than silently in production. S-ing-2 adds the extract/classify chain tasks."""

from __future__ import annotations

import easysynq_api.tasks  # noqa: F401 — importing the package registers every task module
from easysynq_api.tasks.app import app


def test_ingestion_tasks_are_registered() -> None:
    assert "easysynq.ingestion.scan_source" in app.tasks
    assert "easysynq.ingestion.extract_source" in app.tasks
    assert "easysynq.ingestion.classify_source" in app.tasks
    assert "easysynq.ingestion.reap_stalled_runs" in app.tasks


def test_reaper_is_beat_scheduled_pipeline_is_not() -> None:
    tasks = {entry["task"] for entry in app.conf.beat_schedule.values()}
    assert "easysynq.ingestion.reap_stalled_runs" in tasks
    # The scan/extract/classify chain is .delay-triggered (auto-chained), NOT Beat-scheduled.
    assert "easysynq.ingestion.scan_source" not in tasks
    assert "easysynq.ingestion.extract_source" not in tasks
    assert "easysynq.ingestion.classify_source" not in tasks
