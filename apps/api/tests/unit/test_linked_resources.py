"""Unit proofs for the pre-render linked-resource scanner (the gotenberg-8.34 coupling).

The scanner flags an Office/RTF/ODF source that structurally references an EXTERNAL linked resource
(http(s)/file/UNC/absolute) — LibreOffice 8.34 converts-but-omits those, so the mirror must keep the
source bytes + ``no_controlled_rendition`` (R26) instead of caching a lossy controlled copy.
Embedded / relative media is fine. The scanner is PURE + fail-open: a corrupt/non-zip input returns
False (the normal render path handles malformed files). All fixtures are built in-memory with stdlib
(no real Office files needed).
"""

from __future__ import annotations

import io
import zipfile

from easysynq_api.services.vault.linked_resources import LinkScan, scan_linked_resources

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ODT = "application/vnd.oasis.opendocument.text"
_RTF = "application/rtf"
_DOC = "application/msword"


def _zip(members: dict[str, bytes]) -> bytes:
    """A minimal in-memory ZIP from {member-name: bytes} (an OOXML/ODF container shape)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _rels(relationships: str) -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + relationships.encode()
        + b"</Relationships>"
    )


# --- OOXML --------------------------------------------------------------------------------


def test_ooxml_external_relationship_is_flagged() -> None:
    """A docx with an external relationship target (TargetMode=External, http target) → True."""
    rels = _rels(
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        'Target="http://example.com/logo.png" TargetMode="External"/>'
    )
    blob = _zip({"word/document.xml": b"<x/>", "word/_rels/document.xml.rels": rels})
    scan = scan_linked_resources(_DOCX, blob)
    assert scan.has_external_links is True
    assert scan.reason is not None and "1 external" in scan.reason


def test_ooxml_file_and_unc_targets_are_flagged() -> None:
    """file:// and UNC external targets are also flagged (count reflects each)."""
    rels = _rels(
        '<Relationship Id="rId1" Target="file:///C:/logos/x.png" TargetMode="External"/>'
        '<Relationship Id="rId2" Target="\\\\server\\share\\y.png" TargetMode="External"/>'
    )
    blob = _zip({"word/_rels/document.xml.rels": rels})
    scan = scan_linked_resources(_DOCX, blob)
    assert scan.has_external_links is True
    assert scan.reason is not None and "2 external" in scan.reason


def test_ooxml_internal_relationship_is_not_flagged() -> None:
    """An embedded image (TargetMode=Internal, relative target) → False (not external)."""
    rels = _rels(
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        'Target="media/image1.png" TargetMode="Internal"/>'
    )
    blob = _zip({"word/document.xml": b"<x/>", "word/_rels/document.xml.rels": rels})
    assert scan_linked_resources(_DOCX, blob) == LinkScan(False)


def test_ooxml_external_mode_but_relative_target_is_not_flagged() -> None:
    """TargetMode=External with a RELATIVE target (no scheme/absolute) → False (not a real link)."""
    rels = _rels('<Relationship Id="rId1" Target="media/image1.png" TargetMode="External"/>')
    blob = _zip({"word/_rels/document.xml.rels": rels})
    assert scan_linked_resources(_DOCX, blob).has_external_links is False


def test_xlsx_external_link_is_flagged() -> None:
    """The scan keys off mime, so an xlsx with an external rel is flagged the same way."""
    rels = _rels('<Relationship Id="rId1" Target="https://x/y.xlsx" TargetMode="External"/>')
    blob = _zip({"xl/externalLinks/_rels/externalLink1.xml.rels": rels})
    assert scan_linked_resources(_XLSX, blob).has_external_links is True


def test_ooxml_external_hyperlink_is_not_flagged() -> None:
    """A web HYPERLINK (Type .../hyperlink, External, http target) renders fine on 8.34 — it is a
    clickable annotation, not a dropped resource — so it must NOT downgrade the doc. Hyperlinks are
    ubiquitous; flagging them would source-only nearly every real document."""
    rels = _rels(
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        'Target="https://example.com/page" TargetMode="External"/>'
    )
    blob = _zip({"word/document.xml": b"<x/>", "word/_rels/document.xml.rels": rels})
    assert scan_linked_resources(_DOCX, blob) == LinkScan(False)


# --- ODF ----------------------------------------------------------------------------------


def _odf_content(href: str) -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<office:document-content "
        b'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        b'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b"<office:body><office:text><draw:frame><draw:image "
        + f'xlink:href="{href}" xlink:type="simple"/>'.encode()
        + b"</draw:frame></office:text></office:body></office:document-content>"
    )


def test_odf_external_href_is_flagged() -> None:
    """An odt whose content.xml has an external xlink:href → True."""
    blob = _zip(
        {
            "content.xml": _odf_content("http://example.com/logo.png"),
            "styles.xml": b"<x/>",
        }
    )
    scan = scan_linked_resources(_ODT, blob)
    assert scan.has_external_links is True
    assert scan.reason is not None and "xlink:href" in scan.reason


def test_odf_relative_href_is_not_flagged() -> None:
    """A relative Pictures/ href is embedded → False."""
    blob = _zip(
        {
            "content.xml": _odf_content("Pictures/100000000.png"),
            "styles.xml": b"<x/>",
        }
    )
    assert scan_linked_resources(_ODT, blob) == LinkScan(False)


def test_odf_external_href_in_styles_is_flagged() -> None:
    """styles.xml (header/footer logos live there) is scanned too."""
    styles = (
        b"<office:document-styles "
        b'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<draw:image xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        b'xlink:href="file:///etc/logo.png"/>'
        b"</office:document-styles>"
    )
    blob = _zip({"content.xml": _odf_content("Pictures/x.png"), "styles.xml": styles})
    assert scan_linked_resources(_ODT, blob).has_external_links is True


def test_odf_text_hyperlink_is_not_flagged() -> None:
    """A ``text:a`` hyperlink anchor with an external xlink:href is a clickable link, not a linked
    resource — NOT flagged (only draw:image / draw:object / linked sections are the 8.34 hazard)."""
    content = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<office:document-content "
        b'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b"<office:body><office:text><text:p>"
        b'<text:a xlink:href="https://example.com/page">click</text:a>'
        b"</text:p></office:text></office:document-content>"
    )
    blob = _zip({"content.xml": content, "styles.xml": b"<x/>"})
    assert scan_linked_resources(_ODT, blob) == LinkScan(False)


# --- RTF ----------------------------------------------------------------------------------


def test_rtf_includepicture_external_is_flagged() -> None:
    r"""An INCLUDEPICTURE field with an http target → True."""
    body = rb'{\rtf1{\field{\*\fldinst INCLUDEPICTURE "http://x/i.png" \\d}}}'
    scan = scan_linked_resources(_RTF, body)
    assert scan.has_external_links is True
    assert scan.reason is not None and "RTF" in scan.reason


def test_rtf_objlink_unc_is_flagged() -> None:
    r"""A linked OLE object (\objlink) with a UNC target → True."""
    body = rb"{\rtf1{\object\objlink{\*\objclass Word.Document.12}\\\\server\\share\\x.doc}}"
    assert scan_linked_resources(_RTF, body).has_external_links is True


def test_rtf_plain_body_is_not_flagged() -> None:
    """A plain RTF body with no linked field → False."""
    body = rb"{\rtf1\ansi\deff0 {\fonttbl{\f0 Times;}}\f0\fs24 Hello world.\par}"
    assert scan_linked_resources(_RTF, body) == LinkScan(False)


def test_rtf_embedded_picture_is_not_flagged() -> None:
    r"""An embedded \pict (hex bytes, no link instruction) → False."""
    body = rb"{\rtf1{\pict\pngblip 89504e470d0a1a0a}}"
    assert scan_linked_resources(_RTF, body).has_external_links is False


# --- Legacy OLE ---------------------------------------------------------------------------


def test_legacy_ole_ascii_file_marker_is_flagged() -> None:
    """A .doc whose bytes contain an ASCII file:// marker → True (heuristic, safe direction)."""
    body = b"\xd0\xcf\x11\xe0" + b"...padding..." + b"file://server/x.docx" + b"...more..."
    scan = scan_linked_resources(_DOC, body)
    assert scan.has_external_links is True
    assert scan.reason is not None and "OLE" in scan.reason


def test_legacy_ole_utf16le_file_marker_is_flagged() -> None:
    """Legacy Office stores text UTF-16LE — the UTF-16LE encoding of file:// is also caught."""
    body = b"\xd0\xcf\x11\xe0" + "file://".encode("utf-16-le") + b"server"
    assert scan_linked_resources(_DOC, body).has_external_links is True


def test_legacy_ole_clean_bytes_are_not_flagged() -> None:
    """A .doc with no link marker → False."""
    body = b"\xd0\xcf\x11\xe0" + (b"\x00\x01\x02\x03" * 64)
    assert scan_linked_resources(_DOC, body) == LinkScan(False)


# --- Non-Office / robustness --------------------------------------------------------------


def test_non_office_mimes_are_not_scanned() -> None:
    """A mime this scanner does not understand returns False without inspecting bytes."""
    for mime in ("text/plain", "text/csv", "application/octet-stream", "text/html"):
        assert scan_linked_resources(mime, b"file://anything http://anything") == LinkScan(False)


def test_corrupt_zip_with_ooxml_mime_fails_open() -> None:
    """Non-zip bytes under an OOXML mime → False (fail-open, no exception)."""
    assert scan_linked_resources(_DOCX, b"this is definitely not a zip") == LinkScan(False)


def test_corrupt_zip_with_odf_mime_fails_open() -> None:
    """Non-zip bytes under an ODF mime → False (fail-open)."""
    assert scan_linked_resources(_ODT, b"\x00\x01\x02 not a zip") == LinkScan(False)


def test_ooxml_malformed_rels_xml_fails_open() -> None:
    """A real zip whose .rels member is malformed XML → False (per-member fail-open, no crash)."""
    blob = _zip({"word/_rels/document.xml.rels": b"<Relationships><not-closed"})
    assert scan_linked_resources(_DOCX, blob) == LinkScan(False)


def test_mime_with_charset_parameter_is_handled() -> None:
    """A mime carrying a ``; charset=`` parameter still routes to the right scanner."""
    rels = _rels('<Relationship Id="rId1" Target="http://x/y" TargetMode="External"/>')
    blob = _zip({"word/_rels/document.xml.rels": rels})
    assert scan_linked_resources(_DOCX + "; charset=binary", blob).has_external_links is True
