"""The ``FilesystemSourceProvider`` — the v1 ``SourceProvider`` impl (slice S-ing-1, doc 09 §3.4,
§4.1).

Walks a read-only mounted source tree with ``os.walk(followlinks=False)`` (NOT ``Path.rglob`` /
``Path.walk`` — those follow directory symlinks and loop forever on a self-referential link inside
the
mount, the Py3.12 trap). File symlinks and unstat-able entries are emitted with a non-NULL
``FileMeta.error`` so the scan records an *excluded* row for them rather than silently dropping them
(doc 09 §4.2). ``open_stream`` opens a single confined entry with ``O_NOFOLLOW`` for the one-pass
hash + stage read.

:func:`resolve_confined` is the NG3 confinement primitive (shared with the service's run-creation
validation): a candidate path must resolve WITHIN the source root — defeating ``../../etc``
traversal
**and** symlink-escape (``resolve()`` follows links out of the root and fails the containment
check)."""

from __future__ import annotations

import datetime
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from ...domain.ingestion.source import FileMeta


def resolve_confined(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root`` and assert containment. Raises ``ValueError`` on any
    escape
    (absolute path, ``..`` traversal, or a symlink that resolves outside the root)."""
    base = root.resolve()
    candidate = (base / relative).resolve()
    if candidate != base and not candidate.is_relative_to(base):
        raise ValueError(f"path escapes the import source root: {relative!r}")
    return candidate


def _ext(filename: str) -> str | None:
    suffix = os.path.splitext(filename)[1].lstrip(".").lower()
    return suffix or None


def _utc(ts: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.UTC)


class FilesystemSourceProvider:
    """Walks + reads a confined, read-only filesystem source root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def walk(self, *, batch_size: int) -> Iterator[list[FileMeta]]:
        batch: list[FileMeta] = []
        # followlinks=False: never descend a directory symlink (the loop trap). Deterministic order
        # so
        # a resumed scan re-walks identically. onerror is swallowed — an unreadable dir surfaces via
        # its files being absent, but every *file* we do reach gets a row (nothing silently
        # dropped).
        for dirpath, dirnames, filenames in os.walk(self._root, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames):
                abs_path = Path(dirpath) / name
                batch.append(self._meta(abs_path, name))
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
        if batch:
            yield batch

    def _meta(self, abs_path: Path, name: str) -> FileMeta:
        rel = str(abs_path.relative_to(self._root))
        ext = _ext(name)
        try:
            st = abs_path.lstat()  # lstat — do not follow, so a symlink is detected, not traversed
        except OSError as exc:
            return FileMeta(
                rel, name, ext, 0, None, None, error=f"unreadable:{exc.strerror or exc}"
            )
        if stat.S_ISLNK(st.st_mode):
            return FileMeta(rel, name, ext, 0, None, None, error="symlink")
        return FileMeta(
            rel_path=rel,
            filename=name,
            ext=ext,
            size_bytes=st.st_size,
            mtime=_utc(st.st_mtime),
            ctime=_utc(st.st_ctime),
        )

    @contextmanager
    def open_stream(self, rel_path: str) -> Iterator[BinaryIO]:
        """Open a confined entry read-only, refusing to follow a leaf symlink (TOCTOU defence on top
        of the walker's lstat-skip + :func:`resolve_confined`)."""
        abs_path = resolve_confined(self._root, rel_path)
        fd = os.open(abs_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            yield handle
