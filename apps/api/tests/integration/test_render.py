"""S7b integration proofs — the watermarked controlled-copy rendition in the mirror + the download
endpoint (testcontainers PG + MinIO + Redis; no live Gotenberg — PDF-passthrough sources exercise
convert→overlay→cache→mirror end to end, and the LibreOffice convert path is unit-tested with a
mocked Gotenberg in test_render.py).

Headline: a released PDF document's mirror file is a watermarked PDF carrying the non-removable
§11.3 band. R26: a non-renderable source stays source bytes + ``no_controlled_rendition``. The
rendition is cached (a second sync never re-renders), and ``GET /documents/{id}/download`` presigns
it. The render worker reads/writes as the non-owner ``easysynq_app`` role (S6).
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from sqlalchemy import select

from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.vault.mirror import sync_mirror
from easysynq_api.services.vault.render import RenderRequest, RenderResult, RenderSink
from easysynq_api.services.vault.render_gotenberg import GotenbergRenderSink

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _map_clause, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}")


async def _grant(subj: SimpleNamespace) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)


def _pdf(body: str = "Procedure body") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    c.drawString(72, 700, body)
    c.showPage()
    c.save()
    return buf.getvalue()


def _doc_dir(mirror: Path, identifier: str) -> Path:
    current = mirror / "current"
    matches = [p for p in current.iterdir() if p.is_dir() and p.name.startswith(f"{identifier}_")]
    assert len(matches) == 1, [m.name for m in matches]
    return matches[0]


def _source_file(doc_dir: Path) -> Path:
    files = [f for f in doc_dir.iterdir() if f.name not in ("metadata.json", "CHANGELOG.md")]
    assert len(files) == 1, [f.name for f in files]
    return files[0]


async def _release_manual(
    client: AsyncClient, ha: dict, hb: dict, type_id: str, content: bytes, content_type: str
) -> dict:
    """Drive a doc to Effective with an explicit upload content-type (drive_to_effective hardcodes
    application/pdf)."""
    doc = await _create(client, ha, type_id)
    did = doc["id"]
    await client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(client, ha, did, content, ct=content_type)
    await _checkin(client, ha, did, sha, change_reason="v1", change_significance="MAJOR")
    await _map_clause(client, ha, did)  # S9: submit-review needs ≥1 clause_mapping
    await client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    task_id = await s5.task_for_doc(did)
    await client.post(f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"})
    rel = await client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text
    return doc


async def test_released_mirror_pdf_carries_band(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """[HEADLINE] A released PDF document's mirror file is a watermarked PDF carrying the
    non-removable §11.3 band (Rev + Effective + CONTROLLED COPY + identifier) on its page."""
    mirror = tmp_path / "m"
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())
    ident = doc["identifier"]

    await sync_mirror(mirror_path=mirror, render_sink=GotenbergRenderSink())

    source = _source_file(_doc_dir(mirror, ident))
    assert source.suffix == ".pdf"
    text = PdfReader(str(source)).pages[0].extract_text()
    assert "CONTROLLED COPY" in text
    assert ident in text
    assert "Effective" in text and "Rev" in text


async def test_non_renderable_format_no_controlled_rendition(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """[R26] A non-renderable source (octet-stream) stays source bytes + no_controlled_rendition."""
    mirror = tmp_path / "m"
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await _release_manual(
        app_client, ha, hb, await s5.type_id("SOP"), b"BINARY-CAD-DATA", "application/octet-stream"
    )

    await sync_mirror(mirror_path=mirror, render_sink=GotenbergRenderSink())

    doc_dir = _doc_dir(mirror, doc["identifier"])
    assert _source_file(doc_dir).read_bytes() == b"BINARY-CAD-DATA"  # source kept, no PDF
    meta = json.loads((doc_dir / "metadata.json").read_text())
    assert meta["render_status"] == "unrenderable"
    assert meta["no_controlled_rendition"] is True


async def test_rendition_cached_second_sync_skips_render(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """The first sync renders + caches (rendition_blob_sha256 set); the second never re-renders that
    version (a sink that raises if asked to render it stays silent)."""
    mirror = tmp_path / "m"
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())
    vid = uuid.UUID(doc["current_effective_version_id"])

    await sync_mirror(mirror_path=mirror, render_sink=GotenbergRenderSink())

    async with get_sessionmaker()() as s:
        sha = (
            await s.execute(
                select(DocumentVersion.rendition_blob_sha256).where(DocumentVersion.id == vid)
            )
        ).scalar_one()
    assert sha is not None  # cached after the first sync

    class _FailIfRendered:
        def __init__(self, inner: RenderSink, forbidden: uuid.UUID) -> None:
            self._inner, self._forbidden = inner, forbidden

        async def render(self, request: RenderRequest, source_bytes: bytes) -> RenderResult:
            assert request.version_id != self._forbidden, "re-rendered a cached version"
            return await self._inner.render(request, source_bytes)

    await sync_mirror(mirror_path=mirror, render_sink=_FailIfRendered(GotenbergRenderSink(), vid))
    # the cached PDF is what the mirror serves, and the cache pointer is unchanged (not re-written)
    assert _source_file(_doc_dir(mirror, doc["identifier"])).suffix == ".pdf"
    async with get_sessionmaker()() as s:
        sha2 = (
            await s.execute(
                select(DocumentVersion.rendition_blob_sha256).where(DocumentVersion.id == vid)
            )
        ).scalar_one()
    assert sha2 == sha  # the rendition was reused, not re-rendered into a new blob


async def test_download_endpoint_presigns_controlled_rendition(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """GET /documents/{id}/download presigns the cached watermarked rendition (controlled_copy)."""
    mirror = tmp_path / "m"
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())
    did = doc["id"]

    await sync_mirror(mirror_path=mirror, render_sink=GotenbergRenderSink())  # caches the rendition

    resp = await app_client.get(f"/api/v1/documents/{did}/download", headers=ha)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rendition"] == "controlled_copy"
    assert body["content_type"] == "application/pdf"
    assert body["download_url"].startswith("http")


async def test_download_falls_back_to_source_before_render(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Before the mirror renders, the download endpoint serves the source (rendition=source)."""
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())

    resp = await app_client.get(f"/api/v1/documents/{doc['id']}/download", headers=ha)
    assert resp.status_code == 200, resp.text
    assert resp.json()["rendition"] == "source"  # no rendition_blob_sha256 yet
