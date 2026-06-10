"""S-drift-3: the daily D1 verify task is registered + its Beat entry rides the settings knobs."""

from __future__ import annotations

from easysynq_api.config import Settings, get_settings
from easysynq_api.services.common.pg_locks import LOCK_BLOB_VERIFY, LOCK_MIRROR_SYNC
from easysynq_api.tasks import app


def test_verify_task_registered() -> None:
    assert "easysynq.blob.verify" in app.tasks


def test_beat_entry_schedule_matches_settings() -> None:
    entry = app.conf.beat_schedule["blob-verify"]
    assert entry["task"] == "easysynq.blob.verify"
    assert entry["schedule"] == float(get_settings().blob_verify_interval_seconds)


def test_default_knobs() -> None:
    assert Settings.model_fields["blob_verify_interval_seconds"].default == 86400
    assert Settings.model_fields["blob_verify_sample_size"].default == 500


def test_lock_is_distinct_from_mirror_sync() -> None:
    # Blob verify never touches the mirror; sharing LOCK_MIRROR_SYNC would couple unrelated
    # cadences (an hourly mirror scan starving the daily verify and vice versa).
    assert LOCK_BLOB_VERIFY != LOCK_MIRROR_SYNC
