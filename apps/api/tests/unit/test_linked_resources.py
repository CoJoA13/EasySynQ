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

import pytest

from easysynq_api.services.vault.linked_resources import LinkScan, scan_linked_resources

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ODT = "application/vnd.oasis.opendocument.text"
_RTF = "application/rtf"
_DOC = "application/msword"
# Variant mimes deliberately ABSENT from any fixed allowlist — routing is by container content, so
# these must still be scanned (P1-c / P1-d).
_DOTX = "application/vnd.openxmlformats-officedocument.wordprocessingml.template"
_ODG = "application/vnd.oasis.opendocument.graphics"


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


def test_ooxml_external_mode_relative_target_is_flagged() -> None:
    """TargetMode=External with a RELATIVE target (e.g. a linked image ``../logos/logo.png``) IS
    flagged: External mode already means the resource lives OUTSIDE the package, so 8.34 drops it
    regardless of target form (P1-a). Only the target form differs from a scheme/UNC link."""
    rels = _rels('<Relationship Id="rId1" Target="../logos/logo.png" TargetMode="External"/>')
    blob = _zip({"word/_rels/document.xml.rels": rels})
    assert scan_linked_resources(_DOCX, blob).has_external_links is True


def test_xlsx_external_link_is_flagged() -> None:
    """The scan keys off mime, so an xlsx with an external rel is flagged the same way."""
    rels = _rels('<Relationship Id="rId1" Target="https://x/y.xlsx" TargetMode="External"/>')
    blob = _zip({"xl/externalLinks/_rels/externalLink1.xml.rels": rels})
    assert scan_linked_resources(_XLSX, blob).has_external_links is True


def test_ooxml_ftp_scheme_external_rel_is_flagged() -> None:
    """An ftp:// (non-http/file) external rel is flagged — External mode is the signal, and the
    generic URI-scheme rule (P2-c) means even the ODF xlink path treats ftp/smb/webdav as
    external."""
    rels = _rels('<Relationship Id="rId1" Target="ftp://server/logo.png" TargetMode="External"/>')
    blob = _zip({"word/document.xml": b"<x/>", "word/_rels/document.xml.rels": rels})
    assert scan_linked_resources(_DOCX, blob).has_external_links is True


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


def test_ooxml_includepicture_field_without_rels_is_flagged() -> None:
    """A WordprocessingML INCLUDEPICTURE field instruction lives in the document part with NO
    external .rels entry — it must still be flagged by scanning the content member (P1-b)."""
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:body><w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        b'<w:r><w:instrText>INCLUDEPICTURE "https://x/i.png" \\* MERGEFORMAT</w:instrText></w:r>'
        b'<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:body></w:document>'
    )
    # A minimal .rels with NO external relationship (so the rels path contributes nothing).
    rels = _rels(
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    blob = _zip({"word/document.xml": doc, "word/_rels/document.xml.rels": rels})
    scan = scan_linked_resources(_DOCX, blob)
    assert scan.has_external_links is True
    assert scan.reason is not None and "field" in scan.reason


def test_ooxml_fldsimple_includetext_field_is_flagged() -> None:
    r"""A ``<w:fldSimple w:instr="INCLUDETEXT ...">`` (the compact field form) is flagged too."""
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p>"
        b'<w:fldSimple w:instr=" INCLUDETEXT &quot;C:\\share\\boilerplate.docx&quot; ">'
        b"</w:fldSimple></w:p></w:body></w:document>"
    )
    blob = _zip({"word/document.xml": doc, "[Content_Types].xml": b"<Types/>"})
    assert scan_linked_resources(_DOCX, blob).has_external_links is True


def test_ooxml_hyperlink_field_is_not_flagged() -> None:
    """A HYPERLINK field instruction is a clickable link (8.34 renders it) — NOT flagged. ``LINK``
    is a substring of ``HYPERLINK`` but the whole-word boundary prevents a false match (P1-b)."""
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:body><w:p><w:r><w:instrText>HYPERLINK "https://example.com/page"</w:instrText></w:r>'
        b"</w:p></w:body></w:document>"
    )
    blob = _zip({"word/document.xml": doc, "[Content_Types].xml": b"<Types/>"})
    assert scan_linked_resources(_DOCX, blob) == LinkScan(False)


def test_ooxml_body_text_link_word_is_not_flagged() -> None:
    """Fix 1: a body run ``<w:t>LINK</w:t>`` (ordinary prose, NOT a field instruction) must NOT be
    flagged — ``LINK`` only matches inside an ``instrText`` element / ``instr="…"`` attribute."""
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p><w:r><w:t>LINK</w:t></w:r></w:p></w:body></w:document>"
    )
    blob = _zip({"word/document.xml": doc, "[Content_Types].xml": b"<Types/>"})
    assert scan_linked_resources(_DOCX, blob) == LinkScan(False)


def test_ooxml_instrtext_link_dde_field_is_flagged() -> None:
    """Fix 1: a DDE LINK field (``<w:instrText> LINK Excel.Sheet …</w:instrText>``) — ``LINK``
    inside a field-instruction element — IS flagged."""
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:body><w:p><w:r><w:instrText> LINK Excel.Sheet.12 "C:\\\\book.xlsx" "Sheet1!R1C1"'
        b"</w:instrText></w:r></w:p></w:body></w:document>"
    )
    blob = _zip({"word/document.xml": doc, "[Content_Types].xml": b"<Types/>"})
    scan = scan_linked_resources(_DOCX, blob)
    assert scan.has_external_links is True
    assert scan.reason is not None and "field" in scan.reason


def test_ooxml_lowercase_includepicture_field_is_flagged() -> None:
    """Fix 2: OOXML field keywords are case-insensitive — a lowercase ``includepicture`` instruction
    must still be flagged."""
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:body><w:p><w:r><w:instrText>includepicture "https://x/i.png"</w:instrText></w:r>'
        b"</w:p></w:body></w:document>"
    )
    blob = _zip({"word/document.xml": doc, "[Content_Types].xml": b"<Types/>"})
    assert scan_linked_resources(_DOCX, blob).has_external_links is True


def test_ooxml_with_dummy_content_xml_still_scans_rels() -> None:
    """Round-6 P1: a crafted OOXML package carrying an inert top-level ``content.xml`` must NOT
    bypass the OOXML ``.rels`` scan. Both fingerprints present → both scans run (OR-ed), so the real
    external relationship is still caught (the symmetric flip of the round-4 ODF-routing fix)."""
    rels = _rels('<Relationship Id="rId1" Target="https://x/logo.png" TargetMode="External"/>')
    blob = _zip(
        {
            "content.xml": b"<x/>",  # inert dummy ODF-looking member
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<x/>",
            "word/_rels/document.xml.rels": rels,
        }
    )
    assert scan_linked_resources(_DOCX, blob).has_external_links is True


def test_ooxml_variant_mime_external_rel_is_flagged_by_container() -> None:
    """A .dotx template carries a variant mime ABSENT from any fixed allowlist; routing by container
    content (a present ``.rels`` member) still scans it, so its external rel is flagged (P1-c)."""
    rels = _rels('<Relationship Id="rId1" Target="https://x/logo.png" TargetMode="External"/>')
    blob = _zip({"word/document.xml": b"<x/>", "word/_rels/document.xml.rels": rels})
    assert scan_linked_resources(_DOTX, blob).has_external_links is True


def test_ooxml_split_instrtext_includepicture_runs_is_flagged() -> None:
    """Round-3 P1: Word can split ONE field instruction across adjacent ``<w:instrText>`` runs
    (``INCLUDE`` in one, ``PICTURE "…"`` in the next). The scanner now PARSES the part and
    concatenates instrText text in document order, so the rejoined ``INCLUDEPICTURE`` keyword is
    flagged — a raw-text regex over the split runs would have missed it."""
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:body><w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        b'<w:r><w:instrText xml:space="preserve">INCLUDE</w:instrText></w:r>'
        b'<w:r><w:instrText xml:space="preserve">PICTURE "https://x/i.png"</w:instrText></w:r>'
        b'<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:body></w:document>'
    )
    blob = _zip({"word/document.xml": doc, "[Content_Types].xml": b"<Types/>"})
    scan = scan_linked_resources(_DOCX, blob)
    assert scan.has_external_links is True
    assert scan.reason is not None and "field" in scan.reason


@pytest.mark.parametrize("part", ["word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"])
def test_ooxml_includepicture_in_story_part_is_flagged(part: str) -> None:
    """Round-3 P2: footnote/endnote/comment story parts are rendered by LibreOffice and can carry
    field instructions, so they are field-scanned too (previously skipped → a false-negative)."""
    story = (
        b'<?xml version="1.0"?>'
        b'<w:stories xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:p><w:r><w:instrText>INCLUDEPICTURE "https://x/note.png"</w:instrText></w:r></w:p>'
        b"</w:stories>"
    )
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p/></w:body></w:document>"
    )
    blob = _zip({"word/document.xml": doc, part: story, "[Content_Types].xml": b"<Types/>"})
    assert scan_linked_resources(_DOCX, blob).has_external_links is True


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
    """A relative ``Pictures/`` href that IS a packed zip member is embedded → False (Fix 4: an
    embedded image is a real member, so it passes the membership test)."""
    blob = _zip(
        {
            "content.xml": _odf_content("Pictures/100000000.png"),
            "Pictures/100000000.png": b"\x89PNG embedded bytes",
            "styles.xml": b"<x/>",
        }
    )
    assert scan_linked_resources(_ODT, blob) == LinkScan(False)


def test_odf_relative_href_not_a_member_is_flagged() -> None:
    """Fix 4: a RELATIVE href to a sibling file that is NOT a zip member (``../assets/logo.png``) is
    a linked external relative path — 8.34 drops it just like an absolute/scheme link → flagged."""
    blob = _zip(
        {
            "content.xml": _odf_content("../assets/logo.png"),
            "styles.xml": b"<x/>",
        }
    )
    scan = scan_linked_resources(_ODT, blob)
    assert scan.has_external_links is True
    assert scan.reason is not None and "xlink:href" in scan.reason


def test_odf_embedded_subdocument_directory_is_not_flagged() -> None:
    """Round-3 P2: an embedded ODF subdocument (a chart / OLE object) is referenced as
    ``./Object 1`` but stored as a DIRECTORY (``Object 1/content.xml`` …) with no exact ``Object 1``
    zip entry. A directory prefix of a package member counts as embedded → NOT flagged, so an
    ordinary ODT/ODG-with-chart is not false-positively downgraded to source-only."""
    blob = _zip(
        {
            "content.xml": _odf_content("./Object 1"),
            "Object 1/content.xml": b"<x/>",
            "Object 1/styles.xml": b"<x/>",
            "styles.xml": b"<x/>",
        }
    )
    assert scan_linked_resources(_ODT, blob) == LinkScan(False)


def test_odf_with_embedded_rels_still_uses_odf_scan() -> None:
    """Round-4 P1: an ODF that embeds an OOXML/OLE object carries that object's ``.rels`` member.
    Routing must key off the TOP-LEVEL ``content.xml`` (the unambiguous ODF signal) FIRST, so the
    ODT's own external draw:image is still ODF-scanned and flagged — not diverted to the OOXML
    branch (which would never inspect the top-level ``content.xml``/``styles.xml``)."""
    blob = _zip(
        {
            "content.xml": _odf_content("http://example.com/logo.png"),
            "styles.xml": b"<x/>",
            # an embedded OOXML object brings its own .rels — must NOT divert routing to OOXML
            "Object 1/word/_rels/document.xml.rels": _rels(
                '<Relationship Id="rId1" Target="media/i.png" TargetMode="Internal"/>'
            ),
        }
    )
    assert scan_linked_resources(_ODT, blob).has_external_links is True


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
    blob = _zip(
        {
            "content.xml": _odf_content("Pictures/x.png"),
            "Pictures/x.png": b"embedded",  # a real member so only the styles.xml href triggers
            "styles.xml": styles,
        }
    )
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


def test_odf_variant_mime_external_draw_image_is_flagged_by_container() -> None:
    """An .odg drawing carries a variant ODF mime absent from any fixed allowlist; container
    content routing (a present ``content.xml``, no .rels) still scans its external draw:image
    href (P1-d)."""
    blob = _zip(
        {
            "content.xml": _odf_content("http://example.com/diagram.png"),
            "styles.xml": b"<x/>",
        }
    )
    assert scan_linked_resources(_ODG, blob).has_external_links is True


def test_odf_smb_scheme_href_is_flagged() -> None:
    """A non-http(s)/file URI scheme (smb://) is external too (P2-c) → flagged."""
    blob = _zip(
        {
            "content.xml": _odf_content("smb://server/share/x.png"),
            "styles.xml": b"<x/>",
        }
    )
    assert scan_linked_resources(_ODT, blob).has_external_links is True


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


def test_rtf_includepicture_windows_drive_target_is_flagged() -> None:
    r"""INCLUDEPICTURE with a Windows-drive LOCAL target (C:\logos\x.png) is flagged — the keyword
    (not the target form) is the signal; 8.34 drops a local-absolute linked picture too (P2-a)."""
    body = rb'{\rtf1{\field{\*\fldinst INCLUDEPICTURE "C:\\logos\\x.png" \\d}}}'
    assert scan_linked_resources(_RTF, body).has_external_links is True


def test_rtf_includepicture_posix_target_is_flagged() -> None:
    r"""INCLUDEPICTURE with a POSIX-absolute LOCAL target (/srv/x.png) is flagged too (P2-a)."""
    body = rb'{\rtf1{\field{\*\fldinst INCLUDEPICTURE "/srv/x.png" \\d}}}'
    assert scan_linked_resources(_RTF, body).has_external_links is True


def test_rtf_fldinst_hyperlink_with_link_in_url_is_not_flagged() -> None:
    r"""Fix 3: ``\fldinst {HYPERLINK "https://example.com/link"}`` must NOT match — the ``link`` is
    buried in the URL ARGUMENT, not the field COMMAND token, and a hyperlink must keep rendering."""
    body = rb'{\rtf1{\field{\*\fldinst HYPERLINK "https://example.com/link"}}}'
    assert scan_linked_resources(_RTF, body) == LinkScan(False)


def test_rtf_fldinst_link_command_is_flagged() -> None:
    r"""Fix 3: ``{\*\fldinst LINK Excel.Sheet.12 …}`` — ``LINK`` as the field COMMAND token directly
    after ``\fldinst`` (separated only by field-wrapper chars) — IS flagged."""
    body = rb'{\rtf1{\field{\*\fldinst LINK Excel.Sheet.12 "C:\\book.xlsx" "Sheet1!R1C1"}}}'
    assert scan_linked_resources(_RTF, body).has_external_links is True


def test_rtf_fldinst_control_words_before_command_is_flagged() -> None:
    r"""Round-4 P1: Word emits charformat/language control words inside the field group
    (``\fldinst \lang1033\rtlch INCLUDEPICTURE "http://..."``). The gap skips bounded RTF control
    words before the command token, so the linked field is still flagged."""
    body = rb'{\rtf1{\field{\*\fldinst \lang1033\rtlch INCLUDEPICTURE "http://x/i.png" \\d}}}'
    assert scan_linked_resources(_RTF, body).has_external_links is True


def test_rtf_fldinst_hyperlink_with_control_words_is_not_flagged() -> None:
    r"""Round-4: control words before a HYPERLINK command must not cause a false match — the gap is
    only RTF noise (whitespace/braces/control words), so it cannot cross the ``HYPERLINK`` command
    token to reach the ``link`` buried in the URL argument."""
    body = rb'{\rtf1{\field{\*\fldinst \lang1033 HYPERLINK "https://example.com/link"}}}'
    assert scan_linked_resources(_RTF, body) == LinkScan(False)


def test_rtf_body_prose_link_without_fldinst_is_not_flagged() -> None:
    r"""Body prose containing the word "link" and a URL, with NO \fldinst field context, is NOT
    flagged (P2-b) — LINK only matches inside an RTF field instruction."""
    body = rb"{\rtf1\ansi\deff0 Please click this link http://example.com to continue.\par}"
    assert scan_linked_resources(_RTF, body) == LinkScan(False)


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


def test_legacy_ole_uppercase_ascii_file_marker_is_flagged() -> None:
    """Fix 5: URI schemes are case-insensitive — an uppercase ASCII ``FILE://`` is caught."""
    body = b"\xd0\xcf\x11\xe0" + b"...padding..." + b"FILE://server/x" + b"...more..."
    assert scan_linked_resources(_DOC, body).has_external_links is True


def test_legacy_ole_uppercase_http_marker_is_flagged() -> None:
    """Fix 5: an uppercase ``HTTP://`` moniker is caught too."""
    body = b"\xd0\xcf\x11\xe0" + b"see HTTP://example.com/x for more"
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


def test_ooxml_many_clean_members_are_bounded_and_not_flagged() -> None:
    """Fix 6: a zip with FAR more clean ``.rels`` parts than the member budget, none external, is
    NOT flagged and the loop is bounded (it stops once the cumulative member budget is spent)."""
    clean = _rels(
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    members = {"[Content_Types].xml": b"<Types/>"}
    members.update({f"word/parts/p{i}.xml.rels": clean for i in range(2000)})
    assert scan_linked_resources(_DOCX, _zip(members)) == LinkScan(False)


def test_ooxml_short_circuits_on_first_external_rel() -> None:
    """Fix 6: an external rel in an EARLY member flags immediately even amid thousands of parts (the
    short-circuit returns on the first hit without scanning the rest)."""
    ext = _rels('<Relationship Id="rId1" Target="https://x/logo.png" TargetMode="External"/>')
    clean = _rels('<Relationship Id="rId1" Target="styles.xml"/>')
    members = {"word/_rels/document.xml.rels": ext}
    members.update({f"word/parts/p{i}.xml.rels": clean for i in range(2000)})
    assert scan_linked_resources(_DOCX, _zip(members)).has_external_links is True


def test_ooxml_late_external_rel_after_many_field_parts_is_flagged() -> None:
    """Round-4 P1: a package with more field-scannable content parts than the member budget, with an
    external ``.rels`` LATER in archive order, must still flag. The scan reads ``.rels`` parts FIRST
    (few + tiny, the most reliable signal), so the budget can't starve them based on archive order —
    in archive order the 600 slides come before the relationship part."""
    ext = _rels('<Relationship Id="rId1" Target="https://x/logo.png" TargetMode="External"/>')
    members: dict[str, bytes] = {f"ppt/slides/slide{i}.xml": b"<p:sld/>" for i in range(600)}
    members["ppt/slides/_rels/slide1.xml.rels"] = ext  # AFTER the 600 slides in archive order
    assert scan_linked_resources(_DOCX, _zip(members)).has_external_links is True


def test_ooxml_field_code_scanned_despite_many_rels() -> None:
    """Round-5 P2: a package with more clean ``.rels`` parts than the member budget must still get
    its field-code content parts scanned. Relationship and field-code parts have INDEPENDENT member
    budgets, so >512 benign ``.rels`` cannot starve the INCLUDEPICTURE/LINK field scan (the
    symmetric case of the round-4 late-rels fix — document.xml here sits AFTER the 700 rels)."""
    clean = _rels(
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    doc = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:body><w:p><w:r><w:instrText>INCLUDEPICTURE "https://x/i.png"</w:instrText></w:r>'
        b"</w:p></w:body></w:document>"
    )
    members: dict[str, bytes] = {f"word/parts/p{i}.xml.rels": clean for i in range(700)}
    members["word/document.xml"] = doc  # AFTER the 700 rels in archive order
    members["[Content_Types].xml"] = b"<Types/>"
    assert scan_linked_resources(_DOCX, _zip(members)).has_external_links is True


def test_mime_with_charset_parameter_is_handled() -> None:
    """A mime carrying a ``; charset=`` parameter still routes to the right scanner."""
    rels = _rels('<Relationship Id="rId1" Target="http://x/y" TargetMode="External"/>')
    blob = _zip({"word/_rels/document.xml.rels": rels})
    assert scan_linked_resources(_DOCX + "; charset=binary", blob).has_external_links is True
