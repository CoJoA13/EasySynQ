"""The §4.2 filters/quarantine classifier (slice S-ing-1, doc 09 §4.2) — pure.

``classify`` is a deterministic ladder over ``(filename, ext, size_bytes)`` plus the optional
content-derived refinements ``mime`` and ``encrypted``. It is called twice by the scan: first
*pre-read*
(``mime=None``, ``encrypted=False``) — so junk / empty / temp-backup / unsupported-by-ext /
oversize /
archive are decided from name+size and the file is never opened; then, for the survivors,
*post-read*
with the sniffed ``mime`` + the cheap ``encrypted`` header signal, so the mime-based unsupported
leg and
the ``needs_password`` leg can fire before the bytes are staged. Every file gets a verdict —
nothing is
silently dropped (doc 09 §4.2).

Deliberate S-ing-1 deviations from §4.2 (documented in the plan): archives are **quarantined** with
a
reserved ``expand`` hook (not expanded one level — avoids new ``py7zr``/``rarfile`` deps + zip-bomb
risk); only **cheap header-level** encryption is flagged (the deep probe is slice-2 extraction).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Disposition = Literal["included", "excluded", "quarantine"]

# Exact junk filenames (case-insensitive) + the Office lock-file prefix ``~$``.
_JUNK_NAMES = frozenset({"thumbs.db", ".ds_store", "desktop.ini"})
# Temp/backup by extension (``*~`` is handled by suffix).
_TEMP_EXTS = frozenset({"tmp", "bak"})
# Unsupported binaries — never documented information (doc 09 §4.2).
_UNSUPPORTED_EXTS = frozenset({"exe", "iso", "dll", "dmg", "msi", "so", "dylib", "com"})
_UNSUPPORTED_MIMES = frozenset(
    {
        "application/x-dosexec",
        "application/x-iso9660-image",
        "application/x-mach-binary",
        "application/x-sharedlib",
        "application/x-executable",
    }
)
# Archives — quarantined with a reserved expand hook (S-ing-1 deviation; NOT OOXML, which keeps its
# ``docx``/``xlsx`` ext, so it is never matched here even though libmagic may report
# ``application/zip``).
_ARCHIVE_EXTS = frozenset({"zip", "7z", "rar", "tar", "gz", "bz2", "xz", "tgz", "tbz2"})


@dataclass(frozen=True, slots=True)
class ScanFlags:
    """The §4.2 verdict persisted into ``import_file.scan_flags`` (as ``to_dict()``)."""

    disposition: Disposition
    reason: str | None = None
    detail: str = ""

    @property
    def included_candidate(self) -> bool:
        return self.disposition == "included"

    def to_dict(self) -> dict[str, str | None]:
        return {"disposition": self.disposition, "reason": self.reason, "detail": self.detail}


_INCLUDED = ScanFlags(disposition="included")


def classify(
    filename: str,
    ext: str | None,
    size_bytes: int,
    oversize_bytes: int,
    *,
    mime: str | None = None,
    encrypted: bool = False,
) -> ScanFlags:
    """The §4.2 ladder — first match wins. ``mime``/``encrypted`` refine the verdict post-read; when
    omitted (pre-read) only name+size legs can fire."""
    name = filename.lower()
    ext_l = ext.lower() if ext else None

    # 1. junk → excluded
    if name in _JUNK_NAMES or name.startswith("~$"):
        return ScanFlags("excluded", "junk", detail=filename)
    # 2. empty → excluded (checked before temp/backup so a 0-byte .tmp reads as "empty", the calmer
    # verdict)
    if size_bytes == 0:
        return ScanFlags("excluded", "empty")
    # 3. temp/backup → quarantine
    if (ext_l in _TEMP_EXTS) or filename.endswith("~"):
        return ScanFlags("quarantine", "temp_backup", detail=filename)
    # 4. unsupported binary → excluded (by ext, or — post-read — by sniffed mime)
    if (ext_l in _UNSUPPORTED_EXTS) or (mime is not None and mime in _UNSUPPORTED_MIMES):
        return ScanFlags("excluded", "unsupported_binary", detail=mime or (ext_l or ""))
    # 5. oversize → quarantine (decided from stat size, so the file is never read)
    if size_bytes > oversize_bytes:
        return ScanFlags("quarantine", "oversize", detail=f"{size_bytes} bytes")
    # 6. archive → quarantine (reserved expand hook; S-ing-1 deviation)
    if ext_l in _ARCHIVE_EXTS:
        return ScanFlags("quarantine", "archive", detail=ext_l or "")
    # 7. encrypted (cheap header signal) → quarantine
    if encrypted:
        return ScanFlags("quarantine", "needs_password")
    # 8. else → included candidate
    return _INCLUDED
