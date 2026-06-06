"""S-dcr-3b integration proofs — the worker-async visual page-image diff over HTTP.

Creates two PDF-source versions (real reportlab PDFs) so the controlled-copy render is a watermark
in-process pass (no Gotenberg container), drives ``build_visual_diff`` inline to simulate the worker
(the endpoint's ``.delay`` doesn't run in-test), then asserts the poll + page-stream endpoints. The
Unavailable path uses a fake NON_RENDERABLE sink. Assertions are run-scoped to this run's ids.
"""

from __future__ import annotations

import io
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from reportlab.pdfgen import canvas

from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.diff.visual import build_visual_diff, get_visual_diff
from easysynq_api.services.vault.render import RenderRequest, RenderResult
from easysynq_api.services.vault.render_gotenberg import GotenbergRenderSink
from easysynq_api.tasks.visual_diff import visual_diff as visual_diff_task

from .test_dcr import _grant as _grant_keys
from .test_dcr import _subject
from .test_vault import _auth, _checkin, _create, _grant_doc_perms, _sop_type_id, _upload

pytestmark = pytest.mark.integration


class _UnrenderableSink:
    async def render(self, request: RenderRequest, source_bytes: bytes) -> RenderResult:
        return RenderResult.non_renderable()


def _pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    return buf.getvalue()


async def _two_pdf_versions(
    app_client: AsyncClient, h: dict[str, str], v1: bytes, v2: bytes
) -> tuple[str, str, str]:
    doc = await _create(app_client, h, await _sop_type_id())
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha1 = await _upload(app_client, h, did, v1, ct="application/pdf")
    r1 = await _checkin(app_client, h, did, sha1, change_significance="MINOR")
    assert r1.status_code in (200, 201), r1.text
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha2 = await _upload(app_client, h, did, v2, ct="application/pdf")
    r2 = await _checkin(app_client, h, did, sha2, change_significance="MAJOR")
    assert r2.status_code in (200, 201), r2.text
    versions = (await app_client.get(f"/api/v1/documents/{did}/versions", headers=h)).json()
    by_seq = {v["version_seq"]: v["id"] for v in versions}
    return did, by_seq[1], by_seq[2]


async def _run_worker(from_id: str, to_id: str, sink: object) -> None:
    """Simulate the Celery worker: build the cached visual diff in a fresh session."""
    import uuid as _uuid

    async with get_sessionmaker()() as s:
        vd = await get_visual_diff(s, _uuid.UUID(from_id), _uuid.UUID(to_id))
        assert vd is not None
        await build_visual_diff(s, vd.id, sink)  # type: ignore[arg-type]


async def test_visual_diff_ready_and_page_stream(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The task enqueue is exercised separately; here the worker is driven inline (the task makes its
    # own engine, so eager-execution can't reach the testcontainer DB) — no-op the broker publish.
    monkeypatch.setattr(visual_diff_task, "delay", lambda *a, **k: None)
    subject = _subject("vdiff")
    await _grant_doc_perms(subject)
    h = _auth(token_factory, subject)
    did, v1, v2 = await _two_pdf_versions(app_client, h, _pdf("alpha line"), _pdf("bravo line"))

    # POST → 202 Pending (the .delay won't run in-test); the row is created.
    post = await app_client.post(
        f"/api/v1/documents/{did}/versions/{v2}/visual-diff?from={v1}", headers=h
    )
    assert post.status_code == 202, post.text
    assert post.json()["status"] == "Pending"

    # Simulate the worker (PDF source → in-process watermark render, no Gotenberg).
    await _run_worker(v1, v2, GotenbergRenderSink())

    got = await app_client.get(
        f"/api/v1/documents/{did}/versions/{v2}/visual-diff?from={v1}", headers=h
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["status"] == "Ready"
    assert body["page_count"] == 1
    assert body["pages"][0]["page"] == 0

    # The page PNG streams (the diff layer).
    page = await app_client.get(
        f"/api/v1/documents/{did}/versions/{v2}/visual-diff/page/0?from={v1}&layer=diff", headers=h
    )
    assert page.status_code == 200, page.text
    assert page.headers["content-type"] == "image/png"
    assert page.content[:8] == b"\x89PNG\r\n\x1a\n"  # a real PNG


async def test_visual_diff_unavailable_when_not_renderable(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(visual_diff_task, "delay", lambda *a, **k: None)
    subject = _subject("vdiff-unavail")
    await _grant_doc_perms(subject)
    h = _auth(token_factory, subject)
    did, v1, v2 = await _two_pdf_versions(app_client, h, _pdf("x"), _pdf("y"))
    await app_client.post(f"/api/v1/documents/{did}/versions/{v2}/visual-diff?from={v1}", headers=h)
    # A NON_RENDERABLE sink (a format the renderer rejects) → Unavailable, not a crash.
    await _run_worker(v1, v2, _UnrenderableSink())
    body = (
        await app_client.get(
            f"/api/v1/documents/{did}/versions/{v2}/visual-diff?from={v1}", headers=h
        )
    ).json()
    assert body["status"] == "Unavailable"
    assert body["reason"]


async def test_visual_diff_poll_404_before_requested(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("vdiff-404")
    await _grant_doc_perms(subject)
    h = _auth(token_factory, subject)
    did, v1, v2 = await _two_pdf_versions(app_client, h, _pdf("a"), _pdf("b"))
    # GET before any POST → 404 (compute is a POST; GET is a pure poll).
    r = await app_client.get(
        f"/api/v1/documents/{did}/versions/{v2}/visual-diff?from={v1}", headers=h
    )
    assert r.status_code == 404, r.text


async def test_visual_diff_denied_without_read_draft(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    author = _subject("vdiff-author")
    await _grant_doc_perms(author)
    ha = _auth(token_factory, author)
    did, v1, v2 = await _two_pdf_versions(app_client, ha, _pdf("a"), _pdf("b"))

    reader = _subject("vdiff-reader")
    await _grant_keys(reader, ("document.read",))  # read only — no read_draft
    hr = _auth(token_factory, reader)
    r = await app_client.post(
        f"/api/v1/documents/{did}/versions/{v2}/visual-diff?from={v1}", headers=hr
    )
    assert r.status_code == 403, r.text
