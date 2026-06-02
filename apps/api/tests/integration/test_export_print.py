"""S7d integration proofs — the per-request export/print stamped rendition + intent audit
(testcontainers PG/MinIO/Redis; PDF-passthrough so no live Gotenberg, as S7b/S7c).

A released doc is driven to Effective, the mirror sync caches its watermarked controlled-copy PDF
(``rendition_blob_sha256``), and ``GET /documents/{id}/{export,print}`` overlays a FRESH per-request
banner + "{verb} {ts} by {user}" on that cached base, streams it as ``application/pdf``, and writes
an EXPORTED/PRINTED ``audit_event``. Export is gated on the SoD-sensitive ``document.export``
(granted to no seeded role — granted here via an override); print on ``document.print_controlled``.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.vault.mirror import sync_mirror
from easysynq_api.services.vault.render_gotenberg import GotenbergRenderSink

from . import s5_helpers as s5
from .test_vault import _auth, _create, _ensure_user

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}")


def _pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    c.drawString(72, 700, "body")
    c.showPage()
    c.save()
    return buf.getvalue()


async def _grant(subj: SimpleNamespace) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)


async def _grant_perm(subject: str, key: str) -> None:
    """Grant one permission at SYSTEM scope via override — document.export/print_controlled are in
    no seeded lifecycle bundle (export is deliberately ungranted, sod_sensitive)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
        scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
            )
        )
        await s.commit()


async def _audit_rows(version_id: str, event_type: EventType) -> list[AuditEvent]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == uuid.UUID(version_id),
                        AuditEvent.event_type == event_type,
                    )
                )
            )
            .scalars()
            .all()
        )


async def _effective_with_rendition(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace, mirror: Path
) -> tuple[dict, dict[str, str]]:
    """Drive a doc to Effective and run a mirror sync so its controlled-copy PDF is cached. Returns
    (doc, author headers)."""
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())
    await sync_mirror(mirror_path=mirror, render_sink=GotenbergRenderSink())
    return doc, ha


async def test_export_streams_uncontrolled_stamp_and_audits(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """[HEADLINE] /export streams a fresh application/pdf attachment stamped UNCONTROLLED WHEN
    PRINTED + "Exported {ts} by {user}", and writes exactly one EXPORTED audit row."""
    await _grant(subj)
    await _grant_perm(subj.a, "document.export")
    doc, ha = await _effective_with_rendition(app_client, token_factory, subj, tmp_path / "m")

    r = await app_client.get(f"/api/v1/documents/{doc['id']}/export", headers=ha)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.content[:5] == b"%PDF-"
    text = PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    assert "UNCONTROLLED WHEN PRINTED" in text
    assert subj.a in text  # "Exported {ts} by {display_name=subject}"

    rows = await _audit_rows(doc["current_effective_version_id"], EventType.EXPORTED)
    assert len(rows) == 1
    assert rows[0].after is not None and rows[0].after["intent"] == "export"
    assert rows[0].after["copy_status"] == "UNCONTROLLED IF PRINTED"


async def test_print_streams_controlled_copy_stamp_and_audits(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """/print streams an inline application/pdf stamped "CONTROLLED COPY — valid on {date} only" +
    "Printed {ts} by {user}", and writes a PRINTED audit row."""
    await _grant(subj)
    await _grant_perm(subj.a, "document.print_controlled")
    doc, ha = await _effective_with_rendition(app_client, token_factory, subj, tmp_path / "m")

    r = await app_client.get(f"/api/v1/documents/{doc['id']}/print", headers=ha)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert "inline" in r.headers.get("content-disposition", "")
    text = PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    # "valid on … only" is unique to the print banner (the base already says CONTROLLED COPY).
    assert "valid on" in text and "only" in text
    assert subj.a in text

    rows = await _audit_rows(doc["current_effective_version_id"], EventType.PRINTED)
    assert len(rows) == 1
    assert rows[0].after is not None and rows[0].after["intent"] == "print"


async def test_export_forbidden_without_permission(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """A user holding the full lifecycle set but NOT document.export is denied (403) — the
    deliberate authority tightening over /download (which only needs document.read)."""
    await _grant(subj)  # lifecycle only — document.export is in no bundle
    doc, ha = await _effective_with_rendition(app_client, token_factory, subj, tmp_path / "m")

    r = await app_client.get(f"/api/v1/documents/{doc['id']}/export", headers=ha)
    assert r.status_code == 403, r.text


async def test_export_409_when_no_controlled_rendition(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """With no cached controlled-copy PDF (no mirror sync run → rendition_blob_sha256 is NULL), the
    export path is 409 no_controlled_rendition (R26/pending — fetch the source via /download)."""
    await _grant(subj)
    await _grant_perm(subj.a, "document.export")
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), _pdf())

    r = await app_client.get(f"/api/v1/documents/{doc['id']}/export", headers=ha)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "no_controlled_rendition"
    # R26/§11.4: the 409 carries the "uncontrolled when printed" notice + a source-download pointer
    # (the click-through UI is the SPA's job; the API surfaces everything it needs).
    assert "UNCONTROLLED" in body["notice"]
    assert body["source_download"].endswith("/download")


async def test_print_forbidden_without_print_permission(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """/print gates on document.print_controlled (a DIFFERENT key than export) — a user with the
    full lifecycle set but not print_controlled is denied 403, catching a gate-key miswiring."""
    await _grant(subj)  # lifecycle only — document.print_controlled is not in that set
    doc, ha = await _effective_with_rendition(app_client, token_factory, subj, tmp_path / "m")

    r = await app_client.get(f"/api/v1/documents/{doc['id']}/print", headers=ha)
    assert r.status_code == 403, r.text


async def test_export_404_when_no_effective_version(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """A Draft document (no Effective version) → 404, distinct from the 409 (Effective exists, no
    rendition cached) — both are documented responses and must not collapse into each other."""
    await _grant(subj)
    await _grant_perm(subj.a, "document.export")
    ha = _auth(token_factory, subj.a)
    doc = await _create(app_client, ha, await s5.type_id("SOP"))  # Draft — never released

    r = await app_client.get(f"/api/v1/documents/{doc['id']}/export", headers=ha)
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "not_found"
