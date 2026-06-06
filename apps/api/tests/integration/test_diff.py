"""S-dcr-3a integration proofs — the version-diff endpoint (metadata + text redline) over HTTP.

Drives the real vault check-in flow (test_vault helpers) to create two versions with real uploaded
content, then injects a deterministic fake TextExtractor (decode the fetched bytes) so the text
redline is proven WITHOUT a live Tika. The fake is always restored in a finally (it's a module-level
seam — leaving it set would pollute other files). Assertions are run-scoped to this run's ids.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient

from easysynq_api.services.diff import set_text_extractor
from easysynq_api.services.diff.extractor import TikaTextExtractor

from .test_dcr import _grant as _grant_keys
from .test_dcr import _subject
from .test_vault import _auth, _checkin, _create, _grant_doc_perms, _sop_type_id, _upload

pytestmark = pytest.mark.integration


class _BytesDecodeExtractor:
    """A deterministic fake: the 'extracted text' IS the source bytes (utf-8). Lets the redline be
    proven against the actual uploaded content without Tika."""

    async def extract_text(
        self, *, data: bytes, mime_type: str | None, filename: str
    ) -> str | None:
        return data.decode("utf-8", "replace")


class _UnavailableExtractor:
    async def extract_text(
        self, *, data: bytes, mime_type: str | None, filename: str
    ) -> str | None:
        return None


async def _two_versions(
    app_client: AsyncClient, h: dict[str, str], v1_bytes: bytes, v2_bytes: bytes
) -> tuple[str, str, str]:
    """Create a doc + two checked-in versions with the given content; return (doc, v1, v2) ids."""
    doc = await _create(app_client, h, await _sop_type_id())
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha1 = await _upload(app_client, h, did, v1_bytes, ct="text/plain")
    r1 = await _checkin(app_client, h, did, sha1, change_significance="MINOR")
    assert r1.status_code in (200, 201), r1.text
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha2 = await _upload(app_client, h, did, v2_bytes, ct="text/plain")
    r2 = await _checkin(app_client, h, did, sha2, change_significance="MAJOR")
    assert r2.status_code in (200, 201), r2.text
    versions = (await app_client.get(f"/api/v1/documents/{did}/versions", headers=h)).json()
    by_seq = {v["version_seq"]: v["id"] for v in versions}
    return did, by_seq[1], by_seq[2]


async def test_diff_metadata_and_text(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("diff")
    await _grant_doc_perms(subject)
    h = _auth(token_factory, subject)
    did, v1, v2 = await _two_versions(
        app_client, h, b"line one\nline two\n", b"line one\nline two CHANGED\n"
    )

    set_text_extractor(_BytesDecodeExtractor())
    try:
        r = await app_client.get(f"/api/v1/documents/{did}/versions/{v2}/diff?from={v1}", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["document_id"] == did
        assert body["from"]["version_seq"] == 1
        assert body["to"]["version_seq"] == 2
        # text redline reflects the uploaded content.
        assert body["text_diff"]["status"] == "ok"
        ops = [(hk["op"], hk["text"]) for hk in body["text_diff"]["hunks"]]
        assert ("equal", "line one") in ops
        assert ("delete", "line two") in ops
        assert ("insert", "line two CHANGED") in ops
        # metadata_diff is the frozen-snapshot field set (identifier unchanged across versions).
        fields = {d["field"] for d in body["metadata_diff"]}
        assert "identifier" in fields and "title" in fields
        ident = next(d for d in body["metadata_diff"] if d["field"] == "identifier")
        assert ident["changed"] is False

        # Self-diff → all-equal text, nothing changed.
        rs = await app_client.get(
            f"/api/v1/documents/{did}/versions/{v2}/diff?from={v2}", headers=h
        )
        assert rs.status_code == 200, rs.text
        self_body = rs.json()
        assert [h2["op"] for h2 in self_body["text_diff"]["hunks"]] == ["equal"]
        assert all(d["changed"] is False for d in self_body["metadata_diff"])
    finally:
        set_text_extractor(TikaTextExtractor())


async def test_diff_text_unavailable_degrades(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("diff-unavail")
    await _grant_doc_perms(subject)
    h = _auth(token_factory, subject)
    did, v1, v2 = await _two_versions(app_client, h, b"a\n", b"b\n")

    set_text_extractor(_UnavailableExtractor())
    try:
        body = (
            await app_client.get(f"/api/v1/documents/{did}/versions/{v2}/diff?from={v1}", headers=h)
        ).json()
        # Text degrades to unavailable; the metadata diff still works.
        assert body["text_diff"]["status"] == "unavailable"
        assert "metadata_diff" in body
    finally:
        set_text_extractor(TikaTextExtractor())


async def test_diff_cross_document_is_404(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("diff-xdoc")
    await _grant_doc_perms(subject)
    h = _auth(token_factory, subject)
    did_a, _va1, va2 = await _two_versions(app_client, h, b"x\n", b"y\n")
    _did_b, vb1, _vb2 = await _two_versions(app_client, h, b"p\n", b"q\n")
    # Diffing doc A's version against a from-version that belongs to doc B → 404.
    r = await app_client.get(f"/api/v1/documents/{did_a}/versions/{va2}/diff?from={vb1}", headers=h)
    assert r.status_code == 404, r.text


async def test_diff_denied_without_read_draft(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # An actor with document.read but NOT document.read_draft must be denied — the diff exposes
    # non-released version content (the §1.2 read_draft boundary).
    author = _subject("diff-author")
    await _grant_doc_perms(author)
    ha = _auth(token_factory, author)
    did, v1, v2 = await _two_versions(app_client, ha, b"a\n", b"b\n")

    reader = _subject("diff-reader")
    await _grant_keys(reader, ("document.read",))  # read only — no read_draft
    hr = _auth(token_factory, reader)
    r = await app_client.get(f"/api/v1/documents/{did}/versions/{v2}/diff?from={v1}", headers=hr)
    assert r.status_code == 403, r.text
