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
_OOXML_MIMES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
_ODF_MIMES = frozenset(
    {
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
    }
)
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

# A scheme/path is "external" (LibreOffice 8.34 will NOT render it) when it is an absolute URL,
# a file:// URL, a POSIX-absolute path, a Windows drive path (``X:\`` / ``X:/``), or a UNC path
# (``\\host\share``). Everything else (``Pictures/img.png``, ``media/image1.png``, ``./x``,
# ``../x``) is a relative / embedded reference → safe → ignored.
_URL_SCHEME_RE = re.compile(r"^(?:https?|file)://", re.IGNORECASE)
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

    A mime this scanner does not understand (txt, csv, html, pdf, already-non-renderable, unknown)
    returns ``LinkScan(False)`` — only the formats LibreOffice rewrites are inspected."""
    base = mime_type.split(";")[0].strip().lower() if mime_type else ""
    if base in _OOXML_MIMES:
        return _scan_ooxml(source_bytes)
    if base in _ODF_MIMES:
        return _scan_odf(source_bytes)
    if base in _RTF_MIMES:
        return _scan_rtf(source_bytes)
    if base in _LEGACY_OLE_MIMES:
        return _scan_legacy_ole(source_bytes)
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


# ── OOXML (.docx / .xlsx / .pptx) ────────────────────────────────────────────────────────────


def _scan_ooxml(source_bytes: bytes) -> LinkScan:
    """Inspect every ``*.rels`` part for a ``<Relationship TargetMode="External" Target="...">``
    whose target is an external scheme/path. Embedded media is ``TargetMode="Internal"`` (or absent)
    with a relative target → ignored."""
    try:
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as zf:
            rels = [n for n in zf.namelist() if n.lower().endswith(".rels")]
            count = 0
            for name in rels:
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
                    if (rel.get("TargetMode") or "").strip().lower() != "external":
                        continue
                    if _is_external_target(rel.get("Target") or ""):
                        count += 1
    except Exception:  # noqa: BLE001 — not a real zip / corrupt → fail-open
        return LinkScan(False)
    if count:
        return LinkScan(
            True,
            f"{count} external relationship target(s) — LibreOffice 8.34 omits these",
        )
    return LinkScan(False)


# ── ODF (.odt / .ods / .odp) ──────────────────────────────────────────────────────────────────


def _scan_odf(source_bytes: bytes) -> LinkScan:
    """Inspect ``content.xml`` + ``styles.xml`` for any ``xlink:href`` whose value is an external
    scheme/path. A relative ``Pictures/...`` href is embedded → ignored."""
    href_attr = f"{{{_XLINK_NS}}}href"
    try:
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as zf:
            count = 0
            for member in ("content.xml", "styles.xml"):
                data = _read_member(zf, member)
                if data is None:
                    continue
                root = _parse_xml(data)
                if root is None:
                    continue
                for el in root.iter():  # type: ignore[attr-defined]
                    href = el.get(href_attr)
                    if href is not None and _is_external_target(href):
                        count += 1
    except Exception:  # noqa: BLE001 — not a real zip / corrupt → fail-open
        return LinkScan(False)
    if count:
        return LinkScan(
            True,
            f"{count} external xlink:href target(s) — LibreOffice 8.34 omits these",
        )
    return LinkScan(False)


# ── RTF ────────────────────────────────────────────────────────────────────────────────────

# Linked field instructions / link control words, each followed (within a bounded window) by an
# external target. The gap is a SINGLE bounded lazy quantifier over any char (``[\s\S]{0,512}?``) —
# not nested, so there is no catastrophic backtracking; bounded to 512 chars (an RTF field's target
# sits right after the instruction). It must allow the quotes/braces an RTF field wraps its target
# in (``INCLUDEPICTURE "http://..."``). INCLUDEPICTURE/INCLUDETEXT/LINK are field instructions;
# ``\objautlink``/``\objlink`` are linked-OLE-object control words. The UNC alternative is four
# regex-backslashes → matches the two literal backslashes of ``\\host``.
_RTF_LINKED_RE = re.compile(
    r"(?:INCLUDEPICTURE|INCLUDETEXT|\bLINK\b|\\objautlink|\\objlink)[\s\S]{0,512}?"
    r"(?:https?://|file://|\\\\)",
    re.IGNORECASE,
)


def _scan_rtf(source_bytes: bytes) -> LinkScan:
    """RTF is text; decode latin-1 (every byte maps, never raises) over a capped prefix and look for
    a linked field instruction / control word followed by an external target. ReDoS-safe (bounded,
    non-nested)."""
    try:
        text = source_bytes[:_MAX_TEXT_SCAN_BYTES].decode("latin-1", errors="ignore")
    except Exception:  # noqa: BLE001 — defensive; latin-1 decode does not raise
        return LinkScan(False)
    if _RTF_LINKED_RE.search(text):
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
