"""FilesystemSourceProvider + source-path confinement (S-ing-1, doc 09 §3.4/§4.1/§4.2).

The confinement (NG3) and the symlink/never-silently-drop walk semantics are the load-bearing safety
controls — these exercise them against a real tmp tree."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from easysynq_api.services.ingestion.source import FilesystemSourceProvider, resolve_confined


def test_resolve_confined_ok(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    assert resolve_confined(tmp_path, "sub") == (tmp_path / "sub").resolve()
    assert resolve_confined(tmp_path, ".") == tmp_path.resolve()


def test_resolve_confined_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_confined(tmp_path, "../escape")
    with pytest.raises(ValueError):
        resolve_confined(tmp_path, "a/../../escape")


def test_resolve_confined_rejects_absolute(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_confined(tmp_path, "/etc/passwd")


def test_resolve_confined_rejects_symlink_escape(tmp_path: Path) -> None:
    link = tmp_path / "esc"
    link.symlink_to(tmp_path.parent)  # a symlink pointing OUT of the root
    with pytest.raises(ValueError):
        resolve_confined(tmp_path, "esc/anything")


def test_walk_inventories_and_flags_symlinks(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "b.txt").write_text("world!")
    (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")
    provider = FilesystemSourceProvider(tmp_path)
    metas = {m.rel_path: m for batch in provider.walk(batch_size=500) for m in batch}

    assert metas["a.txt"].error is None
    assert metas["a.txt"].ext == "txt"
    assert metas["a.txt"].size_bytes == 5
    assert os.path.join("dir", "b.txt") in metas
    # the file symlink is inventoried (never silently dropped) but marked excluded-by-error
    assert metas["link.txt"].error == "symlink"


def test_walk_batches(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")
    provider = FilesystemSourceProvider(tmp_path)
    assert [len(b) for b in provider.walk(batch_size=2)] == [2, 2, 1]


def test_open_stream_reads_confined(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"abc123")
    provider = FilesystemSourceProvider(tmp_path)
    with provider.open_stream("a.bin") as handle:
        assert handle.read() == b"abc123"


def test_open_stream_follows_in_root_symlink(tmp_path: Path) -> None:
    # An in-root symlink resolves (via resolve_confined) to its in-root target — reading it is
    # allowed.
    target = tmp_path / "real.txt"
    target.write_bytes(b"secret")
    (tmp_path / "link.txt").symlink_to(target)
    provider = FilesystemSourceProvider(tmp_path)
    with provider.open_stream("link.txt") as handle:
        assert handle.read() == b"secret"


def test_open_stream_rejects_out_of_root_symlink(tmp_path: Path) -> None:
    # A symlink whose target is OUTSIDE the confinement root is rejected (NG3).
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.txt"  # outside `root`
    secret.write_bytes(b"nope")
    (root / "esc.txt").symlink_to(secret)
    provider = FilesystemSourceProvider(root)
    with pytest.raises(ValueError), provider.open_stream("esc.txt"):
        pass
