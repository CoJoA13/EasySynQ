"""Pure version-diff domain (no I/O) — the doc 05 §8 redline (slice S-dcr-3a).

``diff_metadata`` compares two versions' frozen ``metadata_snapshot`` field-by-field; ``redline``
produces an inline text redline (line-level LCS via stdlib ``difflib``) of two extracted texts.
The service layer (``services/diff``) does the I/O (load versions, on-demand Tika text
extraction, the provenance header) and calls these.
"""

from __future__ import annotations

from .metadata import SNAPSHOT_FIELDS, FieldDelta, diff_metadata
from .text import Hunk, redline

__all__ = [
    "SNAPSHOT_FIELDS",
    "FieldDelta",
    "Hunk",
    "diff_metadata",
    "redline",
]
