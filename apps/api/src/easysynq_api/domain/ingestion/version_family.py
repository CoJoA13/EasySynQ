"""The §7.2 deterministic canonical-pick + family ordering (slice S-ing-3, doc 09 §7.2/§7.3).

Pure, side-effect-free. ``order_members`` ranks a cluster/family's files best-first by the §7.2
ordered tie-breakers, made **provably TOTAL** with a final ``rel_path`` then ``file_id`` key so two
compute passes over identical data always pick the same canonical/effective and emit the same order
(the ``import_dupe_cluster``/``import_version_family`` DELETE-then-INSERT idempotency contract — the
spec's "deterministic, explainable" claim, §3.1).

The ordered tie-breakers (best first):
1. version marker (``v3`` > ``v2``; ``FINAL``/``APPROVED`` > unmarked > ``DRAFT``).
2. recency — the embedded modified-date (a raw extractor string, parsed defensively), falling back
   to the filesystem ``mtime``; an unparseable/absent value is "no signal" (oldest), never garbage.
3. format — the editable SOURCE (``.docx`` …) over a ``.pdf`` rendition.
4. path — a file under ``/Current/`` or ``/Released/`` over ``/Archive/`` or ``/Old/``.
5. (stability) lexically-lowest ``rel_path``, then ``file_id`` — guarantees a total order so an
   all-tie exact-dup resolves identically across re-deliveries.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid
from collections.abc import Sequence

# Editable source formats preferred over a PDF rendition (§7.2 tie-break 3).
_EDITABLE_EXTS = frozenset(
    {"docx", "doc", "xlsx", "xls", "pptx", "ppt", "odt", "ods", "odp", "rtf", "md", "txt"}
)
_CURRENT_TOKENS = ("/current/", "/released/", "/effective/")
_ARCHIVE_TOKENS = ("/archive/", "/old/", "/obsolete/", "/superseded/")


@dataclasses.dataclass(frozen=True, slots=True)
class FileForPick:
    """The minimal file metadata the §7.2 canonical pick needs."""

    file_id: uuid.UUID
    filename: str
    rel_path: str
    ext: str | None
    mtime: datetime.datetime | None
    embedded_modified: str | None  # the raw extractor 'modified' string (parsed defensively)
    version: int  # parse_version_marker(filename)[0]
    status_rank: int  # parse_version_marker(filename)[1]


def _parse_embedded(value: str | None) -> datetime.datetime | None:
    """Parse a raw extractor 'modified' string defensively (ISO-8601, tolerating a trailing Z).
    Returns ``None`` on anything unparseable — never raises, never a sortable garbage value."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _recency_epoch(f: FileForPick) -> float:
    """A sortable recency: embedded modified-date if parseable, else mtime, else 0.0 (oldest)."""
    when = _parse_embedded(f.embedded_modified) or f.mtime
    if when is None:
        return 0.0
    try:
        return when.timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0


def _format_rank(ext: str | None) -> int:
    """editable source = 2 (best), unknown = 1, pdf = 0 (a rendition, §7.2 tie-break 3)."""
    e = (ext or "").lower().lstrip(".")
    if e in _EDITABLE_EXTS:
        return 2
    if e == "pdf":
        return 0
    return 1


def _path_rank(rel_path: str) -> int:
    """/Current/ or /Released/ = 2 (best), neutral = 1, /Archive/ or /Old/ = 0 (§7.2 tie-4)."""
    p = "/" + rel_path.lower().strip("/") + "/"
    if any(tok in p for tok in _CURRENT_TOKENS):
        return 2
    if any(tok in p for tok in _ARCHIVE_TOKENS):
        return 0
    return 1


def sort_key(f: FileForPick) -> tuple[int, int, float, int, int, str, str]:
    """The §7.2 TOTAL order as an ASCENDING key — smallest is the canonical/effective (best). The
    leading signals are negated so higher version/recency/format/path sort first; ``rel_path`` then
    ``file_id`` make it total (an all-tie cluster still resolves deterministically)."""
    return (
        -f.version,
        -f.status_rank,
        -_recency_epoch(f),
        -_format_rank(f.ext),
        -_path_rank(f.rel_path),
        f.rel_path,
        str(f.file_id),
    )


def order_members(files: Sequence[FileForPick]) -> list[FileForPick]:
    """The cluster/family files ranked best-first (``[0]`` is the canonical / effective pick)."""
    return sorted(files, key=sort_key)
