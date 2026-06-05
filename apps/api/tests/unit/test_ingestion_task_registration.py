"""Guard the Celery task registration (S-ing-1/2/3): if ``tasks/ingestion.py`` is not imported in
``tasks/__init__.py``, ``.delay`` publishes to a name no worker handles → the run hangs forever.
Fails at CI rather than silently in production. S-ing-3 adds the dedup/propose chain tasks."""

from __future__ import annotations

import easysynq_api.tasks  # noqa: F401 — importing the package registers every task module
from easysynq_api.tasks.app import app

_CHAIN_TASKS = (
    "easysynq.ingestion.scan_source",
    "easysynq.ingestion.extract_source",
    "easysynq.ingestion.classify_source",
    "easysynq.ingestion.dedup_source",
    "easysynq.ingestion.propose_source",
)


def test_ingestion_tasks_are_registered() -> None:
    for name in _CHAIN_TASKS:
        assert name in app.tasks, name
    assert "easysynq.ingestion.reap_stalled_runs" in app.tasks


def test_reaper_is_beat_scheduled_pipeline_is_not() -> None:
    tasks = {entry["task"] for entry in app.conf.beat_schedule.values()}
    assert "easysynq.ingestion.reap_stalled_runs" in tasks
    # The scan→extract→classify→dedup→propose chain is .delay-triggered, NOT Beat-scheduled.
    for name in _CHAIN_TASKS:
        assert name not in tasks, name
