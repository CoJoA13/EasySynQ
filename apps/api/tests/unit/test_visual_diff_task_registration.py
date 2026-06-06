"""Guard the Celery task registration (S-dcr-3b): if ``tasks/visual_diff.py`` is not imported in
``tasks/__init__.py``, the visual-diff endpoint's ``.delay`` publishes a message by a name no worker
registered → the ``visual_diff`` row stays Pending forever. Fails at CI, not silently in prod."""

from __future__ import annotations

import easysynq_api.tasks  # noqa: F401 — importing the package registers every task module
from easysynq_api.tasks.app import app


def test_visual_diff_task_is_registered() -> None:
    assert "easysynq.visual_diff" in app.tasks


def test_visual_diff_is_not_beat_scheduled() -> None:
    # The build is .delay-triggered by the POST endpoint, NOT Beat-scheduled.
    tasks = {entry["task"] for entry in app.conf.beat_schedule.values()}
    assert "easysynq.visual_diff" not in tasks
