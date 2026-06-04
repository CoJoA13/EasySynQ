"""The ``SourceProvider`` seam + ``FileMeta`` (slice S-ing-1, doc 09 §3.4, §4.1).

``SourceProvider`` is the reserved pluggability seam (doc 09 §3.4): v1 ships the
``FilesystemSourceProvider`` (``services/ingestion/source.py``); future
``SharePointSourceProvider`` /
``GoogleDriveSourceProvider`` / ``S3SourceProvider`` are drop-in implementations of this Protocol
with
no pipeline rewrite (NG6). The Protocol is pure interface; ``FileMeta`` is the §4.1 walk record.

The walker yields ``FileMeta`` for **every** entry — including symlinks and unstat-able files, which
carry a non-NULL ``error`` so the scan records an *excluded* inventory row for them rather than
silently dropping them (doc 09 §4.2: "nothing is silently dropped").
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import BinaryIO, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FileMeta:
    """One walked source entry (doc 09 §4.1). ``rel_path`` is relative to the source root — no host
    secrets (doc 09 §4.1). ``error`` is set by the walker for entries it refuses to read (a
    symlink, or
    an entry that could not be stat-ed) so the scan can record an excluded row without opening
    them."""

    rel_path: str
    filename: str
    ext: str | None
    size_bytes: int
    mtime: datetime.datetime | None
    ctime: datetime.datetime | None
    error: str | None = None  # e.g. "symlink" or "unreadable:<msg>" → excluded, never read/hashed


@runtime_checkable
class SourceProvider(Protocol):
    """The reserved source seam (doc 09 §3.4). ``walk`` yields lazy batches so neither the walk nor
    the
    orchestration ever holds a whole large tree in RAM; ``open_stream`` opens a single confined
    entry
    for the one-pass hash + stage read."""

    def walk(self, *, batch_size: int) -> Iterator[list[FileMeta]]: ...

    def open_stream(self, rel_path: str) -> AbstractContextManager[BinaryIO]: ...
