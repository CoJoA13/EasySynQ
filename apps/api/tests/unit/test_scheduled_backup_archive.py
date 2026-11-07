"""Real tar/encryption/filesystem replay, with only external source capture replaced."""

import fcntl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from easysynq_api.config import Settings
from easysynq_api.services.backup import archive, crypto, drill

POLICY = UUID(int=425)
FAMILY = UUID(int=1)
NOW = datetime(2026, 10, 1, 2, tzinfo=UTC)


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch):
    captures = []

    def capture(_dsn, path):
        captures.append(path)
        path.write_bytes(b"PGDMP synthetic scheduled backup")
        return {"organization": 1}, []

    monkeypatch.setattr(drill, "_capture_and_dump", capture)
    monkeypatch.setattr(drill, "_latest_checkpoint_bundle", lambda *_: None)
    monkeypatch.setattr(drill.realm_export, "export_realm", lambda **_: {"realm": "synthetic"})
    monkeypatch.setattr(drill.config_snapshot, "build_config_snapshot", lambda *_: {"test": True})
    return captures


def build(
    destination,
    key="synthetic-scheduled-key",
    now=NOW,
    eligible=lambda _: True,
    due=lambda _: True,
    family=FAMILY,
):
    from easysynq_api.services.backup.scheduled_archive import build_scheduled_backup

    return build_scheduled_backup(
        Settings(backup_encryption_key=key),
        destination=str(destination),
        policy_id=POLICY,
        family_id=family,
        clock=lambda: now,
        is_due=due,
        reusable=lambda started, current: eligible(started),
    )


def test_redelivery_reuses_authenticated_complete_archive(source, tmp_path):
    first = build(tmp_path)
    again = build(tmp_path, now=NOW + timedelta(minutes=1))
    assert first == again
    assert first[0] == NOW
    assert len(source) == 1
    path = Path(first[1]["archive"])
    assert archive.verify_archive(path)
    assert drill._newest_retained_archive(str(tmp_path)) == path
    plain = crypto.decrypt_archive(path, tmp_path / "inspect.tar", secret="synthetic-scheduled-key")
    assert archive.read_manifest(plain)["config"]["scheduled_attempt"] == {
        "family": FAMILY.hex,
        "started_at": NOW.isoformat(),
    }


def test_old_completed_attempt_does_not_consume_later_catchup(source, tmp_path):
    monday = NOW - timedelta(days=3)
    old = build(tmp_path, now=monday)
    fresh = build(tmp_path, eligible=lambda started: started >= NOW)
    replay = build(
        tmp_path, now=NOW + timedelta(seconds=30), eligible=lambda started: started >= NOW
    )
    assert old[1]["archive"] != fresh[1]["archive"]
    assert Path(old[1]["archive"]).exists()
    assert replay == fresh
    assert fresh[0] == NOW
    assert len(source) == 2


@pytest.mark.parametrize(
    "first_key,next_key",
    [
        ("", "synthetic-new-key"),
        ("synthetic-old-key", "synthetic-new-key"),
        ("synthetic-old-key", ""),
    ],
)
def test_replay_requires_current_mode_and_current_key(source, tmp_path, first_key, next_key):
    old = build(tmp_path, key=first_key)
    new = build(tmp_path, key=next_key)
    assert new[1]["archive"] != old[1]["archive"]
    assert new[1]["encrypted"] is bool(next_key)
    assert build(tmp_path, key=next_key) == new
    assert len(source) == 2


@pytest.mark.parametrize("damage", ["sidecar", "ciphertext", "short-header"])
def test_incomplete_or_damaged_candidate_is_never_reused(source, tmp_path, damage):
    old = build(tmp_path)
    path = Path(old[1]["archive"])
    if damage == "sidecar":
        path.with_name(path.name + ".sha256").unlink()
    else:
        path.write_bytes(b"ESQBKP" if damage == "short-header" else b"corrupt")
        archive.write_sidecar(path)
    new = build(tmp_path)
    assert new != old
    assert len(source) == 2
    assert archive.verify_archive(Path(new[1]["archive"]))


def test_busy_policy_lock_skips_without_capture_and_keeps_inode(source, tmp_path):
    lock = tmp_path / f".easysynq-backup-{POLICY.hex}.lock"
    with lock.open("a+b") as handle:
        inode = lock.stat().st_ino
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert build(tmp_path) is None
        assert build(tmp_path, family=UUID(int=2)) is None
        assert not source
    assert build(tmp_path) is not None
    assert lock.stat().st_ino == inode


def test_not_due_after_lock_does_not_capture(source, tmp_path):
    assert build(tmp_path, due=lambda _: False) is None
    assert not source


def test_new_family_cannot_reuse_old_configuration(source, tmp_path):
    old = build(tmp_path)
    new = build(tmp_path, family=UUID(int=2))
    assert old != new
    assert len(source) == 2


def test_filename_collision_never_replaces_completed_archive(source, tmp_path, monkeypatch):
    from easysynq_api.services.backup import scheduled_archive

    monkeypatch.setattr(scheduled_archive, "uuid4", lambda: UUID(int=1))
    old = build(tmp_path)
    path = Path(old[1]["archive"])
    original = path.read_bytes()
    with pytest.raises(archive.BackupError, match="already exists"):
        build(tmp_path, eligible=lambda _: False)
    assert path.read_bytes() == original
    assert archive.verify_archive(path)
