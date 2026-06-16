"""Pre-render structural scan for externally-linked resources (the gotenberg-8.34 coupling).

**Why this exists.** Gotenberg 8.34 changed its LibreOffice conversion so external/local **linked**
resources (``http(s)://``, ``file://``, UNC ``\\\\host\\share``) still convert (HTTP 200) but are
**no longer rendered** into the output PDF. Our worker
(:class:`render_gotenberg.GotenbergRenderSink`) caches any successful convert as the mirror's
CONTROLLED COPY — so a document whose body references a linked logo/include/picture would silently
cache an INCOMPLETE controlled copy. That is a WORM/mirror integrity hazard: the controlled copy
must be a faithful rendition of the controlled source.

So before handing an Office/RTF/ODF source to Gotenberg, the worker runs
:func:`scan_linked_resources`. If the source structurally references an EXTERNAL linked resource,
the version is marked **non-renderable** (R26, doc 04 §11.4) — the mirror keeps the source bytes +
``no_controlled_rendition`` instead of caching a lossy PDF. Embedded media (relative / internal
targets) is fine and ignored.

**House rules (mirrored from ``domain/ingestion/minhash.py``).** This module is **pure** — no I/O,
it takes bytes — and **stdlib only** (``zipfile`` / ``re`` / ``xml.etree.ElementTree``), so it adds
no dependency. Inputs are **untrusted uploads**, so:

* XML parsing **refuses any DTD** (``<!DOCTYPE``) and the default expat parser fetches no external
  entity — closing both XXE and the billion-laughs expansion DoS without a third-party dep.
* Reads are **bounded** (a member-size cap on the OOXML/ODF zip members, a byte cap on RTF/legacy
  scans) so a zip-bomb or a multi-GB body can't exhaust memory.
* Regexes are **ReDoS-safe** (no nested quantifiers; anchored, bounded character classes).
* It is **fail-open**: any parse error (corrupt zip, malformed XML, not-actually-a-zip) returns
  ``LinkScan(False)`` and defers to the normal render path (which handles malformed files). A scan
  failure must never crash the render or block an otherwise-renderable document.

False positives are acceptable by design — they downgrade to a safe source-only mirror entry (R26),
never the reverse — so the legacy-OLE path is a deliberately broad raw-byte marker scan.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

# ── Bounds (zip-bomb / OOM / ReDoS confinement) ───────────────────────────────────────────
# Largest single zip member we will decompress to inspect (a .rels / content.xml is tiny; a
# legitimately huge member is not a relationships part, so capping it can only fail-open).
_MAX_MEMBER_BYTES = 8 * 1024 * 1024
# Largest prefix of an RTF / legacy-OLE body we scan for link markers (linked-field instructions
# and the OLE link monikers live early; a deep scan only adds cost, never correctness).
_MAX_TEXT_SCAN_BYTES = 4 * 1024 * 1024

# ── MIME groupings ────────────────────────────────────────────────────────────────────────
# Only RTF and legacy-OLE are routed by mime; OOXML/ODF are detected by CONTAINER CONTENT (a zip
# member fingerprint) so every present-and-future Office variant the render path sends to
# LibreOffice (the .dotx/.xltx/.potx/.ppsx + .ott/.ots/.otp/.odg family — not in any fixed mime
# allowlist) is inspected, never bypassed. See ``scan_linked_resources``.
_RTF_MIMES = frozenset({"application/rtf", "text/rtf"})
_LEGACY_OLE_MIMES = frozenset(
    {
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }
)

# The OOXML relationships namespace; ``Target``/``TargetMode`` are unprefixed attrs on
# ``Relationship``.
_OOXML_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
# The XLink namespace ODF uses for ``xlink:href`` (images, OLE objects, linked sections).
_XLINK_NS = "http://www.w3.org/1999/xlink"
# ODF hyperlink anchors (``text:a`` / ``draw:a``) carry an ``xlink:href`` too, but a hyperlink is a
# clickable annotation rendered fine by 8.34 — NOT a dropped resource. Exclude those tags so a
# hyperlinked doc isn't mis-flagged; only linked MEDIA/OBJECTS (draw:image, draw:object, linked
# text:section, …) are the hazard.
_ODF_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_ODF_DRAW_NS = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
_ODF_HYPERLINK_TAGS = frozenset({f"{{{_ODF_TEXT_NS}}}a", f"{{{_ODF_DRAW_NS}}}a"})

# A scheme/path is "external" (LibreOffice 8.34 will NOT render it) when it carries ANY URI scheme
# (``http(s)://``, ``file://``, ``ftp://``, ``smb://``, ``webdav://``, …), a POSIX-absolute path, a
# Windows drive path (``X:\`` / ``X:/``), or a UNC path (``\\host\share``). Everything else
# (``Pictures/img.png``, ``media/image1.png``, ``./x``, ``../x``) is a relative / embedded
# reference → safe → ignored. The scheme grammar is RFC-3986 (``ALPHA *( ALPHA / DIGIT / "+" /
# "-" / "." )`` then ``://``) — anchored + bounded, ReDoS-safe.
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_external_target(target: str) -> bool:
    """True if ``target`` points OUTSIDE the package (an absolute/remote ref 8.34 won't render).

    A relative path (``Pictures/x.png``, ``./x``, ``media/image1.png``) → False (embedded)."""
    t = target.strip()
    if not t:
        return False
    if _URL_SCHEME_RE.match(t):
        return True
    if t.startswith("\\\\"):  # UNC \\host\share
        return True
    if t.startswith("/"):  # POSIX-absolute
        return True
    if _WIN_DRIVE_RE.match(t):  # X:\ or X:/
        return True
    return False


@dataclass(frozen=True, slots=True)
class LinkScan:
    """The result of a structural linked-resource scan.

    ``has_external_links`` drives the R26 non-renderable downgrade; ``reason`` is a short human
    string (kind + count) that lands in the mirror's ``metadata.json`` for an auditor."""

    has_external_links: bool
    reason: str | None = None


def scan_linked_resources(mime_type: str, source_bytes: bytes) -> LinkScan:
    """Detect externally-linked resources in an Office/RTF/ODF source (the gotenberg-8.34 hazard).

    Returns ``LinkScan(True, reason)`` when the source structurally references an external/linked
    resource that LibreOffice 8.34 converts-but-omits (→ the caller marks the version R26
    source-only), else ``LinkScan(False)``. Pure + fail-open: any parse error returns
    ``LinkScan(False)``.

    Routing is by mime for the two non-zip families (RTF, legacy OLE2) and by **container content**
    for the zip families (OOXML, ODF). A fixed OOXML/ODF mime allowlist would miss the variants the
    render path *does* convert (``.dotx/.xltx/.potx/.ppsx`` + ``.ott/.ots/.otp/.odg`` — routed via
    :data:`render_gotenberg._OFFICE_EXT` + ``mimetypes.guess_extension``), so those zip bytes are
    fingerprinted directly: ``[Content_Types].xml`` or any ``*.rels`` member → OOXML; else a
    ``content.xml`` member → ODF. Non-zip / unrecognised bytes → ``LinkScan(False)``. (Truly
    non-renderable mimes never reach the scanner — the render sink short-circuits them first — so a
    *renderable* zip handed here is an Office package.)"""
    base = mime_type.split(";")[0].strip().lower() if mime_type else ""
    if base in _RTF_MIMES:
        return _scan_rtf(source_bytes)
    if base in _LEGACY_OLE_MIMES:
        return _scan_legacy_ole(source_bytes)
    if zipfile.is_zipfile(io.BytesIO(source_bytes)):
        return _scan_zip_container(source_bytes)
    return LinkScan(False)


def _scan_zip_container(source_bytes: bytes) -> LinkScan:
    """Fingerprint a renderable zip by member layout → OOXML or ODF scan (else fail-open False)."""
    try:
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as zf:
            names = zf.namelist()
            lower = [n.lower() for n in names]
            is_ooxml = "[content_types].xml" in lower or any(n.endswith(".rels") for n in lower)
            if is_ooxml:
                return _scan_ooxml(zf, names)
            if "content.xml" in lower:
                return _scan_odf(zf)
    except Exception:  # noqa: BLE001 — not a real/usable zip → fail-open
        return LinkScan(False)
    return LinkScan(False)


# ── XML hardening (XXE / billion-laughs confinement, stdlib only) ──────────────────────────────

# A DOCTYPE is the entry point for BOTH XXE (external-entity injection) and the billion-laughs
# entity-expansion DoS. A legitimate OOXML/ODF part NEVER carries a DTD, so we refuse to parse any
# member that declares one (anchored, case-insensitive) and fail-open. ``xml.etree``'s default expat
# parser does not fetch external entities, so blocking the DOCTYPE outright closes the residual
# expansion vector without a third-party dependency (defusedxml is unavailable — stdlib-only, D4).
_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)


def _parse_xml(data: bytes) -> object | None:
    """Parse XML → the root Element, or None on any error / a DTD-bearing body (fail-open).

    Refuses any input containing a ``<!DOCTYPE`` (the XXE / billion-laughs entry point — never
    present in a real OOXML/ODF part) before handing the bytes to the default (non-fetching) expat
    parser."""
    import xml.etree.ElementTree as ET

    if _DOCTYPE_RE.search(data):  # a DTD on an untrusted upload → refuse + fail-open
        return None
    try:
        return ET.fromstring(data)  # noqa: S314 — DTD refused above; expat fetches no external entity
    except Exception:  # noqa: BLE001 — malformed/hostile XML → fail-open, defer to normal render
        return None


def _read_member(zf: zipfile.ZipFile, name: str) -> bytes | None:
    """Read one zip member, capped at ``_MAX_MEMBER_BYTES`` (a relationships/content part is tiny).

    A member that is larger than the cap, or whose declared size exceeds it, is skipped (None) — it
    is not a part we inspect, and an oversized member is a zip-bomb signal we refuse to expand."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_MEMBER_BYTES:
        return None
    try:
        with zf.open(name) as fh:
            data = fh.read(_MAX_MEMBER_BYTES + 1)
    except Exception:  # noqa: BLE001 — corrupt/encrypted member → fail-open
        return None
    if len(data) > _MAX_MEMBER_BYTES:
        return None
    return data


# ── Shared field-code link detector (OOXML field instructions + RTF \fldinst) ─────────────────

# A *linked* (not embedded, not hyperlink) field instruction. Both WordprocessingML
# (``<w:instrText>INCLUDEPICTURE "…"</w:instrText>`` / ``<w:fldSimple w:instr="…">``) and RTF
# (``{\*\fldinst INCLUDEPICTURE …}``) name the same field keywords:
#   • INCLUDEPICTURE / INCLUDETEXT — inherently file-linking field instructions: their target may
#     be a URL, a UNC path, ``C:\x.png`` or ``/x.png`` — ALL dropped by 8.34. Flag on the keyword,
#     independent of the target form (closes the relative/local-absolute false-negative).
#   • LINK — the DDE/OLE-link field. Matched only by callers that already know they are inside a
#     field context (RTF anchors it to ``\fldinst``; OOXML members ARE field XML). The whole-word
#     ``\bLINK\b`` deliberately does NOT match inside ``HYPERLINK``: ``R`` and ``L`` are both word
#     chars, so there is no ``\b`` between them (verified by the ``HYPERLINK``-not-flagged tests).
# Each keyword is bracketed by ``\b`` word boundaries — matches ``>INCLUDEPICTURE`` (tag-tight) and
# `` INCLUDEPICTURE `` (space-delimited) alike; no quantifier → ReDoS-trivial.
_FIELD_KEYWORD_RE = re.compile(r"\b(?:INCLUDEPICTURE|INCLUDETEXT|LINK)\b")
# Linked-OLE-object control words (RTF) — a present marker is a linked (not embedded) object.
_OLE_LINK_CW_RE = re.compile(r"\\obj(?:autlink|link)\b", re.IGNORECASE)


def _has_linked_field(text: str) -> bool:
    """True if ``text`` (a field-context string — an OOXML field member or an RTF \\fldinst window)
    contains a linked field keyword. Pure regex, bounded input, ReDoS-safe."""
    return _FIELD_KEYWORD_RE.search(text) is not None


# Content members whose field instructions can carry a linked INCLUDE*/LINK (WordprocessingML body,
# headers/footers; PresentationML slides; SpreadsheetML cells). Matched case-insensitively by suffix
# /prefix so future part names in the same families are covered.
def _is_ooxml_field_member(name_lower: str) -> bool:
    if not name_lower.endswith(".xml"):
        return False
    if name_lower.endswith("/document.xml") or name_lower == "word/document.xml":
        return True
    # word/header1.xml, word/footer2.xml, …
    base = name_lower.rsplit("/", 1)[-1]
    if base.startswith("header") or base.startswith("footer"):
        return True
    # ppt/slides/slide1.xml, ppt/slideLayouts/…, ppt/notesSlides/…
    if "/slides/" in name_lower or "/slidelayouts/" in name_lower or "/notesslides/" in name_lower:
        return True
    # xl/worksheets/sheet1.xml (cell field instructions are rare but possible)
    if "/worksheets/" in name_lower:
        return True
    return False


# ── OOXML (.docx / .xlsx / .pptx + .dotx/.xltx/.potx/.ppsx variants) ───────────────────────────


def _scan_ooxml(zf: zipfile.ZipFile, names: list[str]) -> LinkScan:
    """Inspect every ``*.rels`` part for a non-hyperlink ``<Relationship TargetMode="External">``
    (External mode alone is the signal — the resource is OUTSIDE the package, even with a relative
    ``Target``), AND scan the WordprocessingML/PresentationML/SpreadsheetML content members for a
    linked field instruction (INCLUDEPICTURE/INCLUDETEXT/LINK) that has no ``.rels`` entry."""
    rel_count = 0
    field_hit = False
    for name in names:
        nl = name.lower()
        if nl.endswith(".rels"):
            data = _read_member(zf, name)
            if data is None:
                continue
            root = _parse_xml(data)
            if root is None:
                continue
            for rel in root.iter():  # type: ignore[attr-defined]
                tag = rel.tag
                if not (tag == "Relationship" or tag == f"{{{_OOXML_RELS_NS}}}Relationship"):
                    continue
                # ``TargetMode="External"`` already means the target lives OUTSIDE the package — a
                # linked image can have a RELATIVE ``Target`` (``../logos/logo.png``); 8.34 drops it
                # all the same. So External mode is the signal; we do NOT additionally require an
                # absolute/scheme target here.
                if (rel.get("TargetMode") or "").strip().lower() != "external":
                    continue
                # A text hyperlink (Type ``.../hyperlink``) is a clickable annotation, NOT a
                # fetched-and-embedded resource — 8.34 still renders it fine, so it is not the
                # hazard. Hyperlinks are ubiquitous; flagging them would source-only nearly every
                # real document and defeat controlled-copy rendering. Exclude them; all else
                # External (image / oleObject / audio / video / data) is a genuine dropped resource
                # and IS flagged.
                if (rel.get("Type") or "").strip().lower().endswith("/hyperlink"):
                    continue
                rel_count += 1
        elif not field_hit and _is_ooxml_field_member(nl):
            data = _read_member(zf, name)
            if data is None:
                continue
            # ``errors="ignore"`` never raises; field keywords are ASCII so a mojibake tail can't
            # hide or fabricate one. Field XML is UTF-8 by the OOXML spec.
            if _has_linked_field(data.decode("utf-8", errors="ignore")):
                field_hit = True
    if rel_count:
        return LinkScan(
            True,
            f"{rel_count} external relationship target(s) — LibreOffice 8.34 omits these",
        )
    if field_hit:
        return LinkScan(
            True,
            "linked field instruction (INCLUDEPICTURE/INCLUDETEXT/LINK) — "
            "LibreOffice 8.34 omits these",
        )
    return LinkScan(False)


# ── ODF (.odt / .ods / .odp) ──────────────────────────────────────────────────────────────────


def _scan_odf(zf: zipfile.ZipFile) -> LinkScan:
    """Inspect ``content.xml`` + ``styles.xml`` for any ``xlink:href`` whose value is an external
    scheme/path. A relative ``Pictures/...`` href is embedded → ignored."""
    href_attr = f"{{{_XLINK_NS}}}href"
    count = 0
    for member in ("content.xml", "styles.xml"):
        data = _read_member(zf, member)
        if data is None:
            continue
        root = _parse_xml(data)
        if root is None:
            continue
        for el in root.iter():  # type: ignore[attr-defined]
            if el.tag in _ODF_HYPERLINK_TAGS:  # a hyperlink anchor renders fine → skip
                continue
            href = el.get(href_attr)
            if href is not None and _is_external_target(href):
                count += 1
    if count:
        return LinkScan(
            True,
            f"{count} external xlink:href target(s) — LibreOffice 8.34 omits these",
        )
    return LinkScan(False)


# ── RTF ────────────────────────────────────────────────────────────────────────────────────

# A linked RTF field is detected by the field KEYWORD inside a field context, independent of the
# target form (the target may be ``C:\x.png``, ``/x.png``, ``\\unc\x``, or ``http://…`` — ALL
# dropped by 8.34). INCLUDEPICTURE/INCLUDETEXT are inherently file-linking instructions (flag on the
# keyword). ``LINK`` matches ONLY when it follows a ``\fldinst`` field-instruction marker within a
# bounded window — so body prose ("click this link http://…") never matches. The gap is a SINGLE
# bounded lazy quantifier (``[\s\S]{0,256}?``) — non-nested, so no catastrophic backtracking. The
# leading word boundary on each keyword means ``\fldinst`` then "HYPERLINK" does NOT match ``LINK``.
_RTF_FLDINST_LINK_RE = re.compile(
    r"\\fldinst[\s\S]{0,256}?\b(?:INCLUDEPICTURE|INCLUDETEXT|LINK)\b",
    re.IGNORECASE,
)


def _scan_rtf(source_bytes: bytes) -> LinkScan:
    """RTF is text; decode latin-1 (every byte maps, never raises) over a capped prefix and look for
    a linked field instruction or a linked-OLE control word. The keyword (not the target form) is
    the signal, anchored to a ``\\fldinst`` field context so body prose with a URL never matches.
    ReDoS-safe (bounded, non-nested)."""
    text = source_bytes[:_MAX_TEXT_SCAN_BYTES].decode("latin-1", errors="ignore")
    if _RTF_FLDINST_LINK_RE.search(text) or _OLE_LINK_CW_RE.search(text):
        return LinkScan(
            True,
            "linked RTF field/object target — LibreOffice 8.34 omits these",
        )
    return LinkScan(False)


# ── Legacy OLE (.doc / .xls / .ppt) ───────────────────────────────────────────────────────────

# The binary OLE2 (CFB) format has no parseable relationships part; scan the raw bytes for link
# monikers. Legacy Office stores text UTF-16LE, so each marker is built in BOTH ASCII and UTF-16LE.
# A hit here is a heuristic (false positives possible) → the SAFE direction (source-only, R26).
_OLE_LINK_MARKERS = ("file://", "http://", "https://", "\\\\")


def _scan_legacy_ole(source_bytes: bytes) -> LinkScan:
    """Bounded raw-byte scan of a legacy OLE2 document for a link moniker (ASCII or UTF-16LE).

    No structural parse (CFB has no relationships part) — a substring hit on a link marker is enough
    to fail-safe to source-only. Capped at ``_MAX_TEXT_SCAN_BYTES``."""
    window = source_bytes[:_MAX_TEXT_SCAN_BYTES]
    for marker in _OLE_LINK_MARKERS:
        if marker.encode("ascii") in window or marker.encode("utf-16-le") in window:
            return LinkScan(
                True,
                "linked OLE object / external reference — LibreOffice 8.34 omits these",
            )
    return LinkScan(False)
