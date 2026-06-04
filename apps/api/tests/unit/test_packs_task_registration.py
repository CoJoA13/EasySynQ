"""Guard the Celery task registration (S-pack-1): if ``tasks/packs.py`` is not imported in
``tasks/__init__.py``, ``generate_pack``'s ``.delay`` publishes a message by a name no worker
registered → the pack stays BUILDING forever. Fails at CI rather than silently in production."""

from __future__ import annotations

import easysynq_api.tasks  # noqa: F401 — importing the package registers every task module
from easysynq_api.tasks.app import app


def test_pack_tasks_are_registered() -> None:
    assert "easysynq.packs.build_evidence_pack" in app.tasks
    assert "easysynq.packs.reap_stalled_builds" in app.tasks


def test_reaper_is_beat_scheduled() -> None:
    tasks = {entry["task"] for entry in app.conf.beat_schedule.values()}
    assert "easysynq.packs.reap_stalled_builds" in tasks
    # The build is .delay-triggered, NOT Beat-scheduled.
    assert "easysynq.packs.build_evidence_pack" not in tasks
