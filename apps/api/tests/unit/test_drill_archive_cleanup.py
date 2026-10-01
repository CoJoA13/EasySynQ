"""Drill cleanup is deterministic and handles interrupted ciphertext/sidecar writes.

Legacy plaintext names remain accepted; unrelated durable/drill artifacts stay untouched.
"""

from pathlib import Path

import pytest

from easysynq_api.services.backup.drill import _unlink_transient_archive


def _touch(d: Path, name: str) -> None:
    (d / name).write_text("plaintext-dump-bytes")


@pytest.mark.parametrize("suffix", [".tar", ".tar.enc"])
def test_removes_tar_and_sidecar_for_the_stamp(tmp_path: Path, suffix: str) -> None:
    stamp = "deadbeefdeadbeefdeadbeefdeadbeef"
    _touch(tmp_path, f"easysynq-backup-{stamp}{suffix}")
    _touch(tmp_path, f"easysynq-backup-{stamp}{suffix}.sha256")
    _unlink_transient_archive(str(tmp_path), stamp)
    assert sorted(p.name for p in tmp_path.iterdir()) == []


@pytest.mark.parametrize("suffix", [".tar", ".tar.enc"])
def test_removes_partial_tar_when_sidecar_never_written(tmp_path: Path, suffix: str) -> None:
    """An interrupted archive write is cleaned by stamp even before its sidecar exists."""
    stamp = "fdeadbeefdeadbeefdeadbeefdeadbee"
    _touch(tmp_path, f"easysynq-backup-{stamp}{suffix}")  # no sidecar — archive write interrupted
    _unlink_transient_archive(str(tmp_path), stamp)
    assert not (tmp_path / f"easysynq-backup-{stamp}{suffix}").exists()


def test_is_noop_when_nothing_present(tmp_path: Path) -> None:
    # missing_ok — a pack_archive that failed BEFORE creating the tar leaves nothing, and cleanup
    # must not raise.
    _unlink_transient_archive(str(tmp_path), "00000000000000000000000000000000")
    assert list(tmp_path.iterdir()) == []


def test_leaves_other_archives_untouched(tmp_path: Path) -> None:
    """Cleanup is scoped to THIS drill's stamp — a durable archive and another drill's residue in
    the same directory are not touched."""
    stamp = "11111111111111111111111111111111"
    _touch(tmp_path, f"easysynq-backup-{stamp}.tar")
    durable = "easysynq-backup-20260615T120000Z-bbbbbbbb.tar.enc"
    other_drill = "easysynq-backup-22222222222222222222222222222222.tar"
    _touch(tmp_path, durable)
    _touch(tmp_path, other_drill)
    _unlink_transient_archive(str(tmp_path), stamp)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == sorted([durable, other_drill]), remaining
