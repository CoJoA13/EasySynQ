"""Pure identifier + revision-label formatting (doc 04 §7).

The human identifier is ``{TYPE}-{AREA}-{SEQ:03d}`` (e.g. ``SOP-PUR-014``); the revision is
**not** part of it — revision is version metadata projected from ``version_seq`` (``Rev A``,
``Rev B``, …). Both are deterministic and unit-tested; the atomic ``{SEQ}`` allocation lives in
the repository (``numbering_counter``).
"""

from __future__ import annotations


def format_identifier(
    type_code: str, seq: int, area_code: str | None = None, *, pad: int = 3
) -> str:
    """``{TYPE}-{AREA}-{SEQ:0{pad}d}``; the area segment is omitted when no area is given."""
    parts = [type_code]
    if area_code:
        parts.append(area_code)
    parts.append(f"{seq:0{pad}d}")
    return "-".join(parts)


def _to_letters(n: int) -> str:
    """1→A, 2→B, … 26→Z, 27→AA (bijective base-26)."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def revision_label(version_seq: int, style: str = "letter") -> str:
    """Project a revision label from the monotonic ``version_seq`` (S3 uses ``letter``;
    ``major_minor`` needs significance history and is a later, scheme-configurable concern)."""
    if style == "numeric":
        return str(version_seq)
    return f"Rev {_to_letters(version_seq)}"
