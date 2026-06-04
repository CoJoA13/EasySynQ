"""S-pack-2 integration proofs — evidence-pack external delivery (doc 06 §7.4, UJ-7).

The full guest journey over HTTP: seal a pack → mint a time-boxed share link → a PUBLIC, no-auth
landing + ZIP/PDF download (streamed through the API, audited, ``Referrer-Policy: no-referrer``) →
revoke (immediate) → expiry → authz/validation guards. Isolation mirrors ``test_packs``: a per-test
Process scopes resolution; teardown is in ``finally`` in FK-RESTRICT order (share-links + portfolio
blob first, then pack → records → blobs → authz).
"""

from __future__ import annotations

import datetime
import io
import uuid
import zipfile
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._pack_enums import PackStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.evidence_pack import EvidencePack
from easysynq_api.db.models.pack_share_link import PackShareLink
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.packs import build, build_and_cache_portfolio
from easysynq_api.services.packs import repository as packs_repo

from .test_packs import _PACK_PERMS, _link_process, _make_process, _teardown
from .test_records import _capture, _grant, _subject, _upload_evidence
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def _seal_with_portfolio(pack_id: uuid.UUID) -> None:
    """Drive both build stages directly (Stage 1 seal + Stage 2 portfolio) — not via Celery."""
    async with get_sessionmaker()() as s:
        pack = await packs_repo.get_pack(s, pack_id, for_update=True)
        assert pack is not None
        pack.status = PackStatus.BUILDING
        pack.build_started_at = datetime.datetime.now(datetime.UTC)
        await s.commit()
    async with get_sessionmaker()() as s:
        await build(s, pack_id)
    async with get_sessionmaker()() as s:
        await build_and_cache_portfolio(s, pack_id)


async def _audit_count(pack_id: uuid.UUID, event_type: EventType) -> int:
    async with get_sessionmaker()() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.object_id == pack_id, AuditEvent.event_type == event_type)
            )
            or 0
        )


async def _set_expiry(link_id: uuid.UUID, when: datetime.datetime) -> None:
    async with get_sessionmaker()() as s:
        link = await s.get(PackShareLink, link_id)
        assert link is not None
        link.expires_at = when
        await s.commit()


async def _delivery_teardown(
    *, record_ids: list[str], pack_id: uuid.UUID | None, process_id: uuid.UUID
) -> None:
    if pack_id is not None:
        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_id)
            portfolio_sha = pack.portfolio_blob_sha256 if pack is not None else None
            # share-links → evidence_pack is RESTRICT, so drop the links before the pack.
            await s.execute(delete(PackShareLink).where(PackShareLink.pack_id == pack_id))
            if portfolio_sha is not None:
                await s.execute(delete(Blob).where(Blob.sha256 == portfolio_sha))
            await s.commit()
    await _teardown(record_ids=record_ids, pack_id=pack_id, scope_id=None, process_id=process_id)


async def _make_sealed_pack(
    app_client: AsyncClient, h: dict[str, str], process_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    """An INCLUDED-record pack, sealed with its PDF portfolio. Returns (pack_id, record_id)."""
    sha = await _upload_evidence(app_client, h, f"ev-{uuid.uuid4().hex}".encode())
    rid = (
        await _capture(
            app_client,
            h,
            record_type="EVIDENCE",
            title="included",
            evidence=[{"sha256": sha, "content_type": "application/pdf"}],
        )
    ).json()["id"]
    await _link_process(app_client, h, rid, process_id)
    created = await app_client.post(
        "/api/v1/evidence-packs",
        headers=h,
        json={"title": "Delivery pack", "scope_kind": "PROCESS", "process_ids": [str(process_id)]},
    )
    assert created.status_code == 201, created.text
    pack_uuid = uuid.UUID(created.json()["id"])
    await _seal_with_portfolio(pack_uuid)
    return pack_uuid, rid


async def test_pack_share_full_delivery_flow(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("share")
    user_id = await _grant(subject, _PACK_PERMS)
    h = _auth(token_factory, subject)
    process_id = await _make_process(user_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    rid = ""
    try:
        pack_uuid, rid = await _make_sealed_pack(app_client, h, process_id)

        # The portfolio cache has a real blob row (blob-row-iff-bytes — the backup sweep is safe).
        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            assert pack is not None and pack.portfolio_blob_sha256 is not None
            blob = await s.get(Blob, pack.portfolio_blob_sha256)
            assert blob is not None and blob.mime_type == "application/pdf" and not blob.worm_locked

        # Mint a share link.
        shared = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share",
            headers=h,
            json={"recipient": "Olsen (external auditor)"},
        )
        assert shared.status_code == 201, shared.text
        body = shared.json()
        token = body["token"]
        assert token and body["state"] == "ACTIVE" and body["expires_at"]
        assert body["share_url"].endswith(f"/api/v1/evidence-packs/shared?t={token}")

        # PUBLIC landing — no auth header, surfaces the pack summary (R28 honesty).
        landing = await app_client.get("/api/v1/evidence-packs/shared", params={"t": token})
        assert landing.status_code == 200, landing.text
        assert "Evidence Pack" in landing.text
        assert landing.headers["referrer-policy"] == "no-referrer"

        # PUBLIC ZIP download — streamed through the API, audited, attachment.
        dz = await app_client.get(
            "/api/v1/evidence-packs/shared/download", params={"t": token, "format": "zip"}
        )
        assert dz.status_code == 200, dz.text
        assert dz.headers["referrer-policy"] == "no-referrer"
        assert "attachment" in dz.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(dz.content)) as zf:
            assert {"cover.txt", "manifest.json", "exclusion_report.json"} <= set(zf.namelist())
            assert any(n.startswith(f"records/{rid}/") for n in zf.namelist())

        # PUBLIC PDF portfolio — live-stamped, streamed.
        dp = await app_client.get(
            "/api/v1/evidence-packs/shared/download", params={"t": token, "format": "pdf"}
        )
        assert dp.status_code == 200, dp.text
        assert dp.content[:4] == b"%PDF"

        # The grant accounting + the audit trail (PACK_SHARED once, PACK_DOWNLOADED twice).
        links = await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}/share-links", headers=h)
        assert links.status_code == 200
        assert links.json()[0]["download_count"] == 2
        assert await _audit_count(pack_uuid, EventType.PACK_SHARED) == 1
        assert await _audit_count(pack_uuid, EventType.PACK_DOWNLOADED) == 2

        # Revoke → the public link is dead IMMEDIATELY (the next request re-checks the DB row).
        link_id = body["id"]
        rv = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share-links/{link_id}/revoke",
            headers=h,
            json={"reason": "audit concluded"},
        )
        assert rv.status_code == 200 and rv.json()["state"] == "REVOKED"
        after = await app_client.get(
            "/api/v1/evidence-packs/shared/download", params={"t": token, "format": "zip"}
        )
        assert after.status_code == 403, after.text
        land2 = await app_client.get("/api/v1/evidence-packs/shared", params={"t": token})
        assert land2.status_code == 403
        # Revoking twice is a 409.
        rv2 = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share-links/{link_id}/revoke",
            headers=h,
            json={"reason": "again"},
        )
        assert rv2.status_code == 409
    finally:
        await _delivery_teardown(
            record_ids=[rid] if rid else [], pack_id=pack_uuid, process_id=process_id
        )


async def test_pack_share_expiry_garbage_and_guards(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("share")
    user_id = await _grant(subject, _PACK_PERMS)
    h = _auth(token_factory, subject)
    process_id = await _make_process(user_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    try:
        # Sharing a not-yet-sealed (DRAFT) pack is 409.
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=h,
            json={"title": "draft", "scope_kind": "PROCESS", "process_ids": [str(process_id)]},
        )
        draft_id = uuid.UUID(created.json()["id"])
        early = await app_client.post(
            f"/api/v1/evidence-packs/{draft_id}/share", headers=h, json={}
        )
        assert early.status_code == 409, early.text

        # Sharing a missing pack is 404.
        missing = await app_client.post(
            f"/api/v1/evidence-packs/{uuid.uuid4()}/share", headers=h, json={}
        )
        assert missing.status_code == 404

        # Seal the pack, mint a link, then expire it in the DB → the SAME token is now 403 EXPIRED
        # (the DB ``expires_at`` is authoritative; the signature is valid but the grant is dead).
        await _seal_with_portfolio(draft_id)
        pack_uuid = draft_id
        shared = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share", headers=h, json={"ttl_days": 7}
        )
        assert shared.status_code == 201, shared.text
        token = shared.json()["token"]
        await _set_expiry(
            uuid.UUID(shared.json()["id"]),
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=5),
        )
        expired = await app_client.get(
            "/api/v1/evidence-packs/shared/download", params={"t": token, "format": "zip"}
        )
        assert expired.status_code == 403, expired.text

        # A garbage/forged token is 403 (never a 500).
        garbage = await app_client.get(
            "/api/v1/evidence-packs/shared/download", params={"t": "not-a-real-token"}
        )
        assert garbage.status_code == 403
    finally:
        await _delivery_teardown(record_ids=[], pack_id=pack_uuid, process_id=process_id)


async def test_pack_share_requires_generate_permission(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # A caller with only report.export (not report.evidence_pack.generate) cannot share.
    subject = _subject("share")
    await _grant(subject, ("report.export",))
    h = _auth(token_factory, subject)
    r = await app_client.post(f"/api/v1/evidence-packs/{uuid.uuid4()}/share", headers=h, json={})
    assert r.status_code == 403, r.text
