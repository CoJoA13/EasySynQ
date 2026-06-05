"""Pure text/filename normalization for dedup + version-families (slice S-ing-3, doc 09 §7).

Shared, side-effect-free helpers the dedup stage builds on:

- ``normalize_text`` — the §7.1 shingling input: NFC + casefold + whitespace-collapse.
- ``normalize_base_name`` — the version-family grouping key: strip version markers (``_v#``, ``vN``,
  ``revX``, ``rN``, ``FINAL``/``DRAFT``/``APPROVED``), dates, ``(N)`` copy markers, and the file
  extension, then collapse separators. NFC-first so composed/decomposed Unicode of the same name
  groups together.
- ``extract_doc_code`` — the §8.2 identity-preserving doc-code (``SOP-PUR-002``, ``WI-WELD-14``,
  ``F-7.5-03``), captured **verbatim (original casing)** so a preserved identifier is byte-exact. It
  is NOT the classifier's ``type_code`` (only the prefix, e.g. ``SOP``); it requires ≥1 digit so an
  ordinary hyphenated word is not mistaken for a code.
- ``parse_version_marker`` — a sortable ``(version_ordinal, status_rank)`` for the §7.2 tie-break
  (``v3`` > ``v2``; ``FINAL``/``APPROVED`` > unmarked > ``DRAFT``).
- ``is_obsolete_filename`` — the §7.3 ``(old)``/``superseded``/``DO NOT USE``/``archive`` pre-flag.

**ReDoS posture (mirrors ``rule_pack``):** every regex is compiled through
``rule_pack.validate_pattern`` (the nested-quantifier rejector + ``_MAX_PATTERN_LENGTH`` cap) and
only
ever run against **length-capped** input (``MAX_FILENAME_LENGTH``/``MAX_HEADER_LENGTH``), so bounded
input bounds backtracking. A unit test feeds an OWASP ReDoS string to prove the cap holds.
"""

from __future__ import annotations

import re
import unicodedata

from .rule_pack import MAX_FILENAME_LENGTH, MAX_HEADER_LENGTH, validate_pattern

# A trailing file extension (alpha-led, 1-8 chars) — stripped before base-name/doc-code matching so
# ".docx" never becomes part of a code. Alpha-led so a numeric tail like "F-7.5-03" is NOT truncated
# at its final ".03".
_EXT_RE = validate_pattern(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")

# Separators (incl _ and .) — collapsed to a single space FIRST so the \b-anchored markers below
# fire (an underscore is a regex word char, so "_v3_final" has no \b around its markers until then).
_SEP_RE = validate_pattern(r"[\s_\-.]+")

# Version / status / copy / date markers stripped from a base-name before grouping (§7.1). Run on
# SEPARATOR-COLLAPSED (space-delimited) input, so every \b lands. All bounded → ReDoS-safe.
_MARKER_RES = [
    validate_pattern(r"\bv\d{1,4}\b"),  # v2 / v10 (leading or after a separator-space)
    validate_pattern(r"\bversion ?\d{1,4}\b"),  # version 3
    validate_pattern(r"\brev(?:ision)? ?[a-z]?\d{0,4}\b"),  # rev / rev a / revision 3
    validate_pattern(r"\br\d{1,4}\b"),  # r2
    validate_pattern(r"\b(?:final|draft|approved|copy|latest)\b"),  # status words
    validate_pattern(r"\(\d{1,3}\)"),  # (1) copy marker (parens survive the sep-collapse)
    validate_pattern(r"\b(?:19|20)\d{2} ?\d{0,2} ?\d{0,2}\b"),  # 2023 01 31 date (seps now spaces)
]

# §8.2 doc-code: a 1-5 char alpha prefix + separator + an alnum/dot/dash/underscore tail (bounded).
# A single bounded char-class tail (no quantified group) so it clears the nested-quantifier check.
# Post-checked in code to require ≥1 digit (a real code has a number; "rep-final" does not).
_DOC_CODE_RE = validate_pattern(r"\b[A-Za-z]{1,5}[-_][A-Za-z0-9][A-Za-z0-9._-]{0,38}\b")
# A version/status suffix appended to a code (``SOP-PUR-002_v3_FINAL`` → trim at ``_v3``) so the
# preserved identifier is the CODE, not code+version. NOT a 4-digit year (an ambiguous code part).
# Anchored with a separator/end lookahead (NOT \b) — a trailing "_" is a regex word char, so
# "_v3_FINAL" needs the lookahead to trim at "_v3" rather than only the final "_FINAL".
_CODE_VERSION_SUFFIX_RE = validate_pattern(
    r"[-_](?:v\d{1,4}|r\d{1,4}|rev(?:ision)?[a-z]?\d{0,4}|final|draft|approved|copy|latest)(?=[-_.\s]|$)"
)

# §7.2 version-marker extraction (each captures a number or rev-letter; on sep-collapsed input).
_VERSION_NUM_RES = [
    validate_pattern(r"\bv(\d{1,4})\b"),
    validate_pattern(r"\bversion ?(\d{1,4})\b"),
    validate_pattern(r"\brev(?:ision)? ?(\d{1,4})\b"),
    validate_pattern(r"\br(\d{1,4})\b"),
]
_REV_LETTER_RE = validate_pattern(r"\brev(?:ision)? ?([a-z])\b")
_FINAL_RE = validate_pattern(r"\b(?:final|approved)\b")
_DRAFT_RE = validate_pattern(r"\bdraft\b")

_OBSOLETE_RE = validate_pattern(r"\(old\)|\bold\b|supersed|do[ _]?not[ _]?use|\barchive|\bobsolete")


def _cap(text: str, limit: int) -> str:
    return text[:limit]


def normalize_text(text: str) -> str:
    """The §7.1 shingling input: NFC + casefold + single-spaced. Stable across Unicode forms."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).casefold()).strip()


def _strip_ext(filename: str) -> str:
    return _EXT_RE.sub("", filename)


def normalize_base_name(filename: str) -> str:
    """The version-family grouping key — ext + version/date/copy markers stripped, casefolded, NFC.

    NFC FIRST so two Unicode compositions of one name produce one key; bounded input (capped at
    ``MAX_FILENAME_LENGTH``) bounds every regex."""
    name = unicodedata.normalize("NFC", _cap(filename, MAX_FILENAME_LENGTH))
    name = _strip_ext(name).casefold()
    name = _SEP_RE.sub(" ", name)  # collapse seps FIRST so the \b-anchored markers fire
    for marker in _MARKER_RES:
        name = marker.sub(" ", name)
    return _SEP_RE.sub(" ", name).strip()


def extract_doc_code(filename: str, header_block: str | None = None) -> str | None:
    """The §8.2 recognized doc-code, captured **verbatim** (original casing), or ``None``.

    Scans the (ext-stripped) filename first, then the header — both length-capped. Requires the
    match to contain ≥1 digit so an ordinary hyphenated word ("rep-final") is not a "code"."""
    for blob in (
        _strip_ext(_cap(filename, MAX_FILENAME_LENGTH)),
        _cap(header_block, MAX_HEADER_LENGTH) if header_block else "",
    ):
        if not blob:
            continue
        for m in _DOC_CODE_RE.finditer(blob):
            code = m.group(0)
            suffix = _CODE_VERSION_SUFFIX_RE.search(code)  # trim a trailing _v3 / _FINAL
            if suffix is not None:
                code = code[: suffix.start()]
            if any(c.isdigit() for c in code):
                return code
    return None


def parse_version_marker(filename: str) -> tuple[int, int]:
    """A sortable ``(version_ordinal, status_rank)`` for the §7.2 tie-break (higher = newer).

    ``version_ordinal`` is the max numeric/rev-letter marker found (``-1`` if none); ``status_rank``
    is 2 for FINAL/APPROVED, 0 for DRAFT, 1 otherwise."""
    name = _SEP_RE.sub(" ", _strip_ext(_cap(filename, MAX_FILENAME_LENGTH)).casefold())
    version = -1
    for rx in _VERSION_NUM_RES:
        for m in rx.finditer(name):
            version = max(version, int(m.group(1)))
    for m in _REV_LETTER_RE.finditer(name):  # revA → 1, revB → 2 …
        version = max(version, ord(m.group(1)) - ord("a") + 1)
    if _FINAL_RE.search(name):
        status = 2
    elif _DRAFT_RE.search(name):
        status = 0
    else:
        status = 1
    return version, status


def is_obsolete_filename(filename: str) -> bool:
    """§7.3 pre-flag: the filename screams obsolete ((old) / superseded / DO NOT USE / archive)."""
    return _OBSOLETE_RE.search(_cap(filename, MAX_FILENAME_LENGTH).casefold()) is not None
