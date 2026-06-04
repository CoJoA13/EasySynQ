"""Lazy, guarded libmagic content-sniff (slice S-ing-1, doc 09 §4.1).

``import magic`` (python-magic) is done **lazily inside the function**, never at module top-level,
and
**guarded**: python-magic raises ``ImportError`` when the system ``libmagic1`` is absent (e.g. the
bare
``pytest -m unit`` CI runner, which has no apt step). On ImportError/OSError we fall back to
extension-based ``mimetypes`` — so importing this module for unit collection never touches
libmagic, and
any unit test that does call :func:`sniff_mime` gets the deterministic fallback. The real worker
image
carries ``libmagic1`` (Dockerfile), so production gets true content-sniff."""

from __future__ import annotations

import mimetypes


def _libmagic_sniff(head: bytes) -> str | None:
    """libmagic content-sniff over the leading bytes, or ``None`` if libmagic is unavailable."""
    try:
        import magic
    except ImportError:
        return None
    try:
        result: str = magic.from_buffer(head, mime=True)
    except OSError:  # libmagic present-but-unusable / ctypes load failure
        return None
    return result


def sniff_mime(head: bytes, filename: str) -> str:
    """Best-effort mime for a file's leading bytes; falls back to the filename extension."""
    mime = _libmagic_sniff(head)
    if mime:
        return mime
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"
