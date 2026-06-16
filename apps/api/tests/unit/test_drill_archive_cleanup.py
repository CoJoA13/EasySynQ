"""Pin: the restore drill's transient-archive cleanup removes the plaintext .tar by its
DETERMINISTIC stamp — so a ``pack_archive`` that fails partway (disk-full / NFS mid-tar or
mid-sidecar), which never returns a path, still gets cleaned up (Codex P2 on #155).

The drill never encrypts; only ``build_durable_backup`` writes a retained, encrypted archive. So a
stranded drill ``.tar`` would accumulate PLAINTEXT db dumps in the backup directory, bypassing the
encryption operators expect — exactly the bypass the P1 fix set out to close, now closed on the
failure path too.
"""

from pathlib import Path

from easysynq_api.services.backup.drill import _unlink_transient_archive


def _touch(d: Path, name: str) -> None:
    (d / name).write_text("plaintext-dump-bytes")


def test_removes_tar_and_sidecar_for_the_stamp(tmp_path: Path) -> None:
    stamp = "deadbeefdeadbeefdeadbeefdeadbeef"
    _touch(tmp_path, f"easysynq-backup-{stamp}.tar")
    _touch(tmp_path, f"easysynq-backup-{stamp}.tar.sha256")
    _unlink_transient_archive(str(tmp_path), stamp)
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_removes_partial_tar_when_sidecar_never_written(tmp_path: Path) -> None:
    """The Codex P2 case: pack_archive wrote the .tar then raised before the .sha256 sidecar — the
    partial plaintext .tar is still cleaned (it is named by the deterministic stamp)."""
    stamp = "fdeadbeefdeadbeefdeadbeefdeadbee"
    _touch(tmp_path, f"easysynq-backup-{stamp}.tar")  # no sidecar — pack_archive raised mid-way
    _unlink_transient_archive(str(tmp_path), stamp)
    assert not (tmp_path / f"easysynq-backup-{stamp}.tar").exists()


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
