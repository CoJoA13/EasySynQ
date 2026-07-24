"""Guard the S-rec-3 Celery task registration: if ``tasks/records.py``'s new task is not reachable
from ``tasks/__init__.py``, ``capture``'s ``.delay`` publishes a message by a name no worker
registered → the structured-PDF rendition never builds. Fails at CI rather than silently."""

from __future__ import annotations

import easysynq_api.tasks  # noqa: F401 — importing the package registers every task module
from easysynq_api.tasks.app import app


def test_structured_pdf_task_is_registered() -> None:
    assert "easysynq.records.build_structured_pdf" in app.tasks


def test_reap_pending_blob_purges_task_is_registered_and_beat_scheduled() -> None:
    # Batch 5 crash backstop: if the reaper task is not registered, its Beat entry publishes to a
    # name no worker handles → every stranded WORM-erasure marker silently accumulates (leaked bytes
    # never erased). Guard both the registration and the schedule.
    assert "easysynq.records.reap_pending_blob_purges" in app.tasks
    tasks = {entry["task"] for entry in app.conf.beat_schedule.values()}
    assert "easysynq.records.reap_pending_blob_purges" in tasks


def test_structured_pdf_is_not_beat_scheduled() -> None:
    # The rendition build is .delay-triggered after capture, NOT Beat-scheduled (best-effort, no
    # reaper — it is derived + rebuildable; GET /records/{id}/rendition 409s until it lands).
    tasks = {entry["task"] for entry in app.conf.beat_schedule.values()}
    assert "easysynq.records.build_structured_pdf" not in tasks
    # The retention sweep IS Beat-scheduled (S-rec-2) — sanity that the schedule is non-trivial.
    assert "easysynq.records.retention_sweep" in tasks
