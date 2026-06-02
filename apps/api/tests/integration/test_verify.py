"""S7c integration proofs — the public /verify page (CURRENT / SUPERSEDED / UNKNOWN) + the verify
QR wired into the mirror rendition + `mirror rebuild` re-rendering (testcontainers PG/MinIO/Redis).

The token is minted with the app's configured signing key (conftest points it at a tmp file), and
the /verify endpoint verifies with the same key — so mint and verify agree, exactly as the shared
`secrets` volume makes the worker (mint) and api (verify) agree on the real stack.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from reportlab.pdfgen import canvas

from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.vault import verify_token
from easysynq_api.services.vault.mirror import sync_mirror
from easysynq_api.services.vault.render_gotenberg import GotenbergRenderSink

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}")


async def _grant(subj: SimpleNamespace) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)


def _pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    c.drawString(72, 700, "body")
    c.showPage()
    c.save()
    return buf.getvalue()


async def _token_for(document_id: str, version_id: str) -> str:
    async with get_sessionmaker()() as s:
        version = await s.get(DocumentVersion, uuid.UUID(version_id))
        assert version is not None
        digest = version.source_blob_sha256
    return verify_token.mint(uuid.UUID(document_id), uuid.UUID(version_id), digest)


async def test_verify_current(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())
    token = await _token_for(doc["id"], doc["current_effective_version_id"])

    r = await app_client.get(f"/api/v1/verify?t={token}")
    assert r.status_code == 200
    assert "CURRENT" in r.text
    assert doc["identifier"] in r.text


async def test_verify_superseded(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())
    did = doc["id"]
    v1_token = await _token_for(did, doc["current_effective_version_id"])

    # Revise → release v2; the v1 token must now read SUPERSEDED.
    await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    sha2 = await _upload(app_client, ha, did, b"v2-content")
    await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MINOR")
    await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    task_id = await s5.task_for_doc(did)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})

    r = await app_client.get(f"/api/v1/verify?t={v1_token}")
    assert r.status_code == 200
    assert "SUPERSEDED" in r.text


async def test_verify_unknown(app_client: AsyncClient) -> None:
    """A syntactically bad token fails the signature check → UNKNOWN."""
    r = await app_client.get("/api/v1/verify?t=not-a-real-token")
    assert r.status_code == 200
    assert "UNKNOWN" in r.text


async def test_verify_unknown_valid_signature_unknown_version(app_client: AsyncClient) -> None:
    """The OTHER UNKNOWN branch: a VALIDLY-signed token whose version doesn't exist → UNKNOWN
    (claims mismatch, not a signature failure)."""
    import hashlib

    token = verify_token.mint(uuid.uuid4(), uuid.uuid4(), hashlib.sha256(b"x").hexdigest())
    r = await app_client.get(f"/api/v1/verify?t={token}")
    assert r.status_code == 200
    assert "UNKNOWN" in r.text


async def test_verify_superseded_after_obsolete(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """SUPERSEDED also fires when the document is fully obsoleted (no current Effective version)."""
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())
    did = doc["id"]
    token = await _token_for(did, doc["current_effective_version_id"])

    obs = await app_client.post(
        f"/api/v1/documents/{did}/obsolete", headers=ha, json={"reason": "withdrawn"}
    )
    assert obs.status_code == 200, obs.text

    r = await app_client.get(f"/api/v1/verify?t={token}")
    assert r.status_code == 200
    assert "SUPERSEDED" in r.text


async def test_mirror_rendition_carries_verify_qr(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """The mirror PDF for a released doc embeds the verify QR (verify_url wired end to end)."""
    from pypdf import PdfReader

    mirror = tmp_path / "m"
    await _grant(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())

    await sync_mirror(mirror_path=mirror, render_sink=GotenbergRenderSink())

    current = mirror / "current"
    doc_dir = next(
        p for p in current.iterdir() if p.is_dir() and p.name.startswith(doc["identifier"])
    )
    pdf = next(f for f in doc_dir.iterdir() if f.suffix == ".pdf")
    page = PdfReader(str(pdf)).pages[0]
    # The "Scan the QR" hint is drawn ONLY when verify_url is set, so it proves the token reached
    # the rendition (not merely that some image exists); the image is the QR.
    assert "Scan the QR" in page.extract_text()
    xobjects = page.get("/Resources", {}).get("/XObject")
    assert xobjects is not None
    assert any(xobjects[k].get("/Subtype") == "/Image" for k in xobjects), (
        "no verify QR in mirror PDF"
    )


async def test_mirror_rebuild_force_re_renders(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mirror rebuild` (force) clears cached renditions and re-renders — so a template change (e.g.
    the S7c QR) reaches existing renditions instead of being a content-addressed cache hit."""
    from sqlalchemy import update

    from easysynq_api.cli import mirror as mirror_cli

    monkeypatch.setenv("MIRROR_PATH", str(tmp_path / "m"))
    from easysynq_api.config import get_settings

    get_settings.cache_clear()
    try:
        await _grant(subj)
        ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
        doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())
        vid = uuid.UUID(doc["current_effective_version_id"])

        await mirror_cli._sync(force=False)  # renders + caches
        async with get_sessionmaker()() as s:
            version = await s.get(DocumentVersion, vid)
            assert version is not None
            sha, source_sha = version.rendition_blob_sha256, version.source_blob_sha256
        assert sha is not None and sha != source_sha

        # Point the cache at the WRONG (but real) blob — the source — to simulate a stale pointer
        # (a bogus sha would violate the rendition→blob FK). Only force-rebuild restores it.
        async with get_sessionmaker()() as s:
            await s.execute(
                update(DocumentVersion)
                .where(DocumentVersion.id == vid)
                .values(rendition_blob_sha256=source_sha)
            )
            await s.commit()

        await mirror_cli._sync(force=True)  # clears all cached renditions + re-renders
        async with get_sessionmaker()() as s:
            again = await s.get(DocumentVersion, vid)
            assert again is not None
        assert again.rendition_blob_sha256 == sha  # re-rendered, overwriting the stale pointer
    finally:
        get_settings.cache_clear()
