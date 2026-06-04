"""Guard the Celery task registration (S-ing-1): if ``tasks/ingestion.py`` is not imported in
``tasks/__init__.py``, ``create_import_run``'s ``scan_source.delay`` publishes to a name no worker
handles → the run hangs in Created forever. Fails at CI rather than silently in production."""

from __future__ import annotations

import easysynq_api.tasks  # noqa: F401 — importing the package registers every task module
from easysynq_api.tasks.app import app


def test_ingestion_tasks_are_registered() -> None:
    assert "easysynq.ingestion.scan_source" in app.tasks
    assert "easysynq.ingestion.reap_stalled_scans" in app.tasks


def test_reaper_is_beat_scheduled_scan_is_not() -> None:
    tasks = {entry["task"] for entry in app.conf.beat_schedule.values()}
    assert "easysynq.ingestion.reap_stalled_scans" in tasks
    # The scan is .delay-triggered by create_import_run, NOT Beat-scheduled.
    assert "easysynq.ingestion.scan_source" not in tasks
