"""S-pack-2 integration proofs — evidence-pack external delivery (doc 06 §7.4, UJ-7).

The full guest journey over HTTP: seal a pack → mint a time-boxed share link → a PUBLIC, no-auth
landing + ZIP/PDF download (streamed through the API, audited, ``Referrer-Policy: no-referrer``) →
revoke (immediate) → expiry → authz/validation guards. Isolation mirrors ``test_packs``: a per-test
Process scopes resolution; teardown is in ``finally`` in FK-RESTRICT order (share-links + portfolio
blob first, then pack → records → blobs → authz).
"""

from __future__ import annotations

import asyncio
import datetime
import importlib
import io
import uuid
import zipfile
from collections.abc import Callable

import httpx
import pytest
from botocore.exceptions import ClientError
from httpx import AsyncClient
from sqlalchemy import delete, func, select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._pack_enums import PackStatus
from easysynq_api.db.models._record_enums import RecordDispositionState
from easysynq_api.db.models._retention_enums import DispositionAction
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.disposition_event import DispositionEvent
from easysynq_api.db.models.evidence_blob import EvidenceBlob
from easysynq_api.db.models.evidence_pack import EvidencePack
from easysynq_api.db.models.pack_share_link import PackShareLink
from easysynq_api.db.models.pending_blob_purge import PendingBlobPurge
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.worm_destroy_request import WormDestroyRequest
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.packs import build, build_and_cache_portfolio
from easysynq_api.services.packs import repository as packs_repo
from easysynq_api.services.records import disposition
from easysynq_api.services.records import repository as records_repo
from easysynq_api.services.vault import storage

from ._owner_db import owner_delete_disposition_events
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


async def test_pack_artifact_liveness_preserves_an_unaffected_live_owner(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Issue #361: clearing an affected pack pointer is not authority to erase another live owner.

    This contrives byte-identical ownership by an ARCHIVE_COLD Record, whose state is DISPOSED but
    whose content must remain intact. The shared ZIP is attached evidence; the shared portfolio is
    its structured rendition. The ZIP stays alive but loses the destroyed pack Record's route
    attachment, and the portfolio remains needed until the archived owner's pointer is cleared.
    Treating every DISPOSED Record as erased makes both liveness legs fail.
    """
    subject = _subject("pack-r27-live-owner")
    user_id = await _grant(subject, _PACK_PERMS)
    h = _auth(token_factory, subject)
    process_id = await _make_process(user_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    rid = ""
    shared_record_id = ""
    try:
        pack_uuid, rid = await _make_sealed_pack(app_client, h, process_id)
        shared = await _capture(
            app_client,
            h,
            record_type="COMPETENCE",
            title="independent rendition owner",
            form_field_values={"owner": "independent"},
        )
        assert shared.status_code == 201, shared.text
        shared_record_id = shared.json()["id"]

        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            live_record = await s.get(Record, uuid.UUID(shared_record_id))
            assert pack is not None and pack.pack_record_id is not None
            assert pack.zip_blob_sha256 is not None and pack.portfolio_blob_sha256 is not None
            assert live_record is not None
            pack_record = await s.get(Record, pack.pack_record_id)
            assert pack_record is not None
            zip_sha = pack.zip_blob_sha256
            portfolio_sha = pack.portfolio_blob_sha256

            # ARCHIVE_COLD is a custody transition: despite DISPOSED state, its attached evidence
            # and structured rendition remain lawful owners. Contrive byte-identical ownership of
            # both pack artifact families, then archive the independent Record.
            s.add(
                EvidenceBlob(
                    org_id=live_record.org_id,
                    record_id=live_record.id,
                    blob_sha256=zip_sha,
                    content_type="application/zip",
                    created_by=user_id,
                )
            )
            live_record.structured_pdf_blob_sha256 = portfolio_sha
            disposition._write_tombstone(
                s,
                live_record,
                action=DispositionAction.ARCHIVE_COLD,
                policy_id=live_record.retention_policy_id,
                approved_by=None,
            )
            await s.flush()
            assert live_record.disposition_state is RecordDispositionState.DISPOSED

            # The archived owner keeps shared ZIP bytes alive, but the destroyed pack Record must
            # still lose its own attachment or the generic Record route can mint a new URL to bytes
            # retained for that other owner.
            event = disposition._write_tombstone(
                s,
                pack_record,
                action=DispositionAction.DESTROY,
                policy_id=pack_record.retention_policy_id,
                approved_by=None,
            )
            assert (
                await disposition._mark_record_evidence_for_purge(
                    s,
                    pack_record,
                    disposition_event=event,
                )
                == []
            )
            assert await records_repo.get_evidence_blob(s, pack_record.id, zip_sha) is None
            assert await records_repo.get_evidence_blob(s, live_record.id, zip_sha) is not None
            assert await s.get(Blob, zip_sha) is not None
            assert live_record.structured_pdf_blob_sha256 == portfolio_sha

            # Remove the pack's ownership exactly as invalidation does, but leave an unrelated
            # archived Record pointing at the same physical content.
            pack.portfolio_blob_sha256 = None
            await s.flush()
            assert await records_repo.blob_needed_by_any_live_owner(s, portfolio_sha)

            live_record.structured_pdf_blob_sha256 = None
            await s.flush()
            assert not await records_repo.blob_needed_by_any_live_owner(s, portfolio_sha)
            await s.rollback()
    finally:
        record_ids = [rid] if rid else []
        if shared_record_id:
            record_ids.append(shared_record_id)
        await _delivery_teardown(
            record_ids=record_ids,
            pack_id=pack_uuid,
            process_id=process_id,
        )


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


async def test_sealed_pack_refuses_serving_after_member_destroyed(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Batch 6 (R27/R28): a record DESTROYED after a pack is sealed must not stay reachable via the
    pack's cached ZIP/portfolio. The public share (landing + download) fails-closed to 403 and the
    authenticated download 409s. An ordinary policy tombstone does NOT inherit R27 authority to
    revoke/purge the independently retained pack: its SEALED header, pointers, and share row remain
    intact while the conservative serve guard withholds delivery."""
    subject = _subject("destroy-serve")
    user_id = await _grant(subject, _PACK_PERMS)
    h = _auth(token_factory, subject)
    process_id = await _make_process(user_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    rid = ""
    try:
        pack_uuid, rid = await _make_sealed_pack(app_client, h, process_id)
        shared = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share",
            headers=h,
            json={"recipient": "auditor"},
        )
        token = shared.json()["token"]
        link_id = uuid.UUID(shared.json()["id"])
        async with get_sessionmaker()() as s:
            original_pack = await s.get(EvidencePack, pack_uuid)
            assert original_pack is not None
            original_pointers = (
                original_pack.zip_blob_sha256,
                original_pack.portfolio_blob_sha256,
            )
        # BEFORE the destroy both serve normally.
        assert (
            await app_client.get("/api/v1/evidence-packs/shared", params={"t": token})
        ).status_code == 200
        assert (
            await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}/download", headers=h)
        ).status_code == 200

        # WORM-destroy the INCLUDED record (write its DESTROY disposition tombstone).
        async with get_sessionmaker()() as s:
            record = await s.get(Record, uuid.UUID(rid))
            assert record is not None
            disposition._write_tombstone(
                s,
                record,
                action=DispositionAction.DESTROY,
                policy_id=record.retention_policy_id,
                approved_by=None,
            )
            await s.commit()

        # AFTER: the public paths fail-closed (403 UNAVAILABLE); the authenticated download 409s.
        assert (
            await app_client.get("/api/v1/evidence-packs/shared", params={"t": token})
        ).status_code == 403
        pub = await app_client.get(
            "/api/v1/evidence-packs/shared/download", params={"t": token, "format": "zip"}
        )
        assert pub.status_code == 403, pub.text
        auth = await app_client.get(f"/api/v1/evidence-packs/{pack_uuid}/download", headers=h)
        assert auth.status_code == 409, auth.text
        assert auth.json()["code"] == "pack_evidence_destroyed"
        # Minting a NEW share for the now-tainted pack is refused BEFORE it commits a dead token
        # (else PACK_SHARED audits a link the serve gate would reject on first use).
        mint = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share", headers=h, json={"recipient": "auditor2"}
        )
        assert mint.status_code == 409, mint.text
        assert mint.json()["code"] == "pack_evidence_destroyed"
        async with get_sessionmaker()() as s:
            retained = await s.get(EvidencePack, pack_uuid)
            link = await s.get(PackShareLink, link_id)
            assert retained is not None and link is not None
            assert retained.status is PackStatus.SEALED
            assert (retained.zip_blob_sha256, retained.portfolio_blob_sha256) == original_pointers
            assert retained.invalidated_at is None
            assert retained.invalidated_by_disposition_event_id is None
            assert link.revoked_at is None
    finally:
        await owner_delete_disposition_events([uuid.UUID(rid)] if rid else [])
        await _delivery_teardown(
            record_ids=[rid] if rid else [], pack_id=pack_uuid, process_id=process_id
        )


async def test_r27_destroy_invalidates_and_purges_pack_derivatives(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Issue #361: the real two-person R27 path removes every sealed derivative copy.

    The proof spans the public/authenticated/generic routes, durable lineage, share revocation,
    Blob rows, physical objects, and URLs minted before the legal order.
    """
    requester_subject = _subject("pack-r27-a")
    approver_subject = _subject("pack-r27-b")
    requester_id = await _grant(requester_subject, (*_PACK_PERMS, "record.dispose"))
    await _grant(approver_subject, ("record.read", "record.dispose"))
    requester_headers = _auth(token_factory, requester_subject)
    approver_headers = _auth(token_factory, approver_subject)
    process_id = await _make_process(requester_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    rid = ""
    pack_record_id: uuid.UUID | None = None
    try:
        pack_uuid, rid = await _make_sealed_pack(app_client, requester_headers, process_id)
        shared = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share",
            headers=requester_headers,
            json={"recipient": "legal auditor"},
        )
        assert shared.status_code == 201, shared.text
        share_id = uuid.UUID(shared.json()["id"])
        share_token = shared.json()["token"]

        dedicated = await app_client.get(
            f"/api/v1/evidence-packs/{pack_uuid}/download", headers=requester_headers
        )
        assert dedicated.status_code == 200, dedicated.text
        dedicated_url = dedicated.json()["download_url"]

        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            assert pack is not None
            assert pack.pack_record_id is not None
            assert pack.zip_blob_sha256 is not None
            assert pack.portfolio_blob_sha256 is not None
            pack_record_id = pack.pack_record_id
            zip_sha = pack.zip_blob_sha256
            portfolio_sha = pack.portfolio_blob_sha256
            zip_blob = await s.get(Blob, zip_sha)
            portfolio_blob = await s.get(Blob, portfolio_sha)
            assert zip_blob is not None and portfolio_blob is not None
            zip_location = (zip_blob.bucket, zip_blob.object_key)
            portfolio_location = (portfolio_blob.bucket, portfolio_blob.object_key)

        generic = await app_client.get(
            f"/api/v1/records/{pack_record_id}/evidence/{zip_sha}/download",
            headers=requester_headers,
        )
        assert generic.status_code == 200, generic.text
        generic_url = generic.json()["download_url"]
        async with httpx.AsyncClient() as object_client:
            assert (await object_client.get(dedicated_url)).status_code == 200
            assert (await object_client.get(generic_url)).status_code == 200

        requested = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests",
            headers=requester_headers,
            json={"legal_basis": "court order PACK-361"},
        )
        assert requested.status_code == 201, requested.text
        request_id = uuid.UUID(requested.json()["id"])
        approved = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{request_id}/approve",
            headers=approver_headers,
            json={"reason": "execute derivative erasure"},
        )
        assert approved.status_code == 200, approved.text

        detail = await app_client.get(
            f"/api/v1/evidence-packs/{pack_uuid}", headers=requester_headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "UNAVAILABLE"
        assert detail.json()["zip_blob_sha256"] is None
        assert detail.json()["invalidated_at"] is not None
        source_event_id = uuid.UUID(detail.json()["invalidated_by_disposition_event_id"])

        assert (
            await app_client.get("/api/v1/evidence-packs/shared", params={"t": share_token})
        ).status_code == 403
        assert (
            await app_client.get(
                "/api/v1/evidence-packs/shared/download",
                params={"t": share_token, "format": "zip"},
            )
        ).status_code == 403
        unavailable = await app_client.get(
            f"/api/v1/evidence-packs/{pack_uuid}/download", headers=requester_headers
        )
        assert unavailable.status_code == 409
        assert unavailable.json()["detail"] == "UNAVAILABLE"
        regenerate = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/generate", headers=requester_headers
        )
        assert regenerate.status_code == 409
        assert regenerate.json()["code"] == "pack_unavailable"
        remint = await app_client.post(
            f"/api/v1/evidence-packs/{pack_uuid}/share",
            headers=requester_headers,
            json={"recipient": "late auditor"},
        )
        assert remint.status_code == 409
        assert (
            await app_client.get(
                f"/api/v1/records/{pack_record_id}/evidence/{zip_sha}/download",
                headers=requester_headers,
            )
        ).status_code == 404

        async with httpx.AsyncClient() as object_client:
            assert (await object_client.get(dedicated_url)).status_code in {403, 404}
            assert (await object_client.get(generic_url)).status_code in {403, 404}

        assert not (await storage.head(zip_location[1], bucket=zip_location[0])).exists
        assert not (await storage.head(portfolio_location[1], bucket=portfolio_location[0])).exists
        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            assert pack is not None
            assert pack.status is PackStatus.UNAVAILABLE
            assert pack.zip_blob_sha256 is None
            assert pack.portfolio_blob_sha256 is None
            assert pack.invalidated_by_disposition_event_id == source_event_id

            link = await s.get(PackShareLink, share_id)
            assert link is not None
            assert link.revoked_at is not None
            assert link.revoked_by is not None
            assert link.revoke_reason == "R27 legal erasure invalidated the Evidence Pack"

            pack_record = await s.get(Record, pack_record_id)
            assert pack_record is not None
            assert pack_record.disposition_state is RecordDispositionState.DISPOSED
            root = await s.get(DispositionEvent, source_event_id)
            assert root is not None and root.record_id == uuid.UUID(rid)
            derived = await s.scalar(
                select(DispositionEvent).where(
                    DispositionEvent.record_id == pack_record_id,
                    DispositionEvent.derived_from_disposition_event_id == source_event_id,
                )
            )
            assert derived is not None
            assert derived.requested_by == root.requested_by
            assert derived.approved_by == root.approved_by
            assert derived.legal_basis == root.legal_basis
            assert await s.get(Blob, zip_sha) is None
            assert await s.get(Blob, portfolio_sha) is None
            assert (
                await s.scalar(
                    select(func.count())
                    .select_from(PendingBlobPurge)
                    .where(PendingBlobPurge.record_id.in_([uuid.UUID(rid), pack_record_id]))
                )
                == 0
            )
        assert await _audit_count(pack_uuid, EventType.PACK_INVALIDATED) == 1
        assert await _audit_count(pack_uuid, EventType.PACK_SHARE_REVOKED) == 1
    finally:
        record_ids = [uuid.UUID(rid)] if rid else []
        if pack_record_id is not None:
            record_ids.append(pack_record_id)
        if record_ids:
            async with get_sessionmaker()() as s:
                await s.execute(
                    delete(PendingBlobPurge).where(PendingBlobPurge.record_id.in_(record_ids))
                )
                if rid:
                    await s.execute(
                        delete(WormDestroyRequest).where(
                            WormDestroyRequest.record_id == uuid.UUID(rid)
                        )
                    )
                await s.commit()
        await _delivery_teardown(
            record_ids=[rid] if rid else [], pack_id=pack_uuid, process_id=process_id
        )


async def test_r27_destroy_closes_over_nested_pack_records(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A pack copied into another pack propagates R27 invalidation to the outer artifact.

    Registered pack Records are ordinary EVIDENCE Records. Linking the inner pack Record to a
    second Process makes its ZIP an INCLUDED member of the outer pack; invalidating only the direct
    inner pack would leave that derivative copy reachable through the outer pack and old URLs.
    """
    requester_subject = _subject("pack-r27-nested-a")
    approver_subject = _subject("pack-r27-nested-b")
    requester_id = await _grant(requester_subject, (*_PACK_PERMS, "record.dispose"))
    await _grant(approver_subject, ("record.read", "record.dispose"))
    requester_headers = _auth(token_factory, requester_subject)
    approver_headers = _auth(token_factory, approver_subject)
    inner_process_id = await _make_process(requester_id, f"Inner-{uuid.uuid4().hex[:8]}")
    outer_process_id = await _make_process(requester_id, f"Outer-{uuid.uuid4().hex[:8]}")
    inner_pack_id: uuid.UUID | None = None
    outer_pack_id: uuid.UUID | None = None
    inner_pack_record_id: uuid.UUID | None = None
    outer_pack_record_id: uuid.UUID | None = None
    rid = ""
    try:
        inner_pack_id, rid = await _make_sealed_pack(
            app_client,
            requester_headers,
            inner_process_id,
        )
        async with get_sessionmaker()() as s:
            inner_pack = await s.get(EvidencePack, inner_pack_id)
            assert inner_pack is not None and inner_pack.pack_record_id is not None
            inner_pack_record_id = inner_pack.pack_record_id

        await _link_process(
            app_client,
            requester_headers,
            str(inner_pack_record_id),
            outer_process_id,
        )
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=requester_headers,
            json={
                "title": "Outer nested delivery pack",
                "scope_kind": "PROCESS",
                "process_ids": [str(outer_process_id)],
            },
        )
        assert created.status_code == 201, created.text
        outer_pack_id = uuid.UUID(created.json()["id"])
        await _seal_with_portfolio(outer_pack_id)

        artifact_locations: dict[str, tuple[str, str]] = {}
        async with get_sessionmaker()() as s:
            inner_pack = await s.get(EvidencePack, inner_pack_id)
            outer_pack = await s.get(EvidencePack, outer_pack_id)
            assert inner_pack is not None and outer_pack is not None
            assert inner_pack.pack_record_id == inner_pack_record_id
            assert outer_pack.pack_record_id is not None
            outer_pack_record_id = outer_pack.pack_record_id
            assert inner_pack_record_id in await packs_repo.pack_dependency_record_ids(
                s,
                outer_pack,
            )
            for pack in (inner_pack, outer_pack):
                assert pack.zip_blob_sha256 is not None
                assert pack.portfolio_blob_sha256 is not None
                for sha in (pack.zip_blob_sha256, pack.portfolio_blob_sha256):
                    blob = await s.get(Blob, sha)
                    assert blob is not None
                    artifact_locations[sha] = (blob.bucket, blob.object_key)

        requested = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests",
            headers=requester_headers,
            json={"legal_basis": "court order PACK-361-NESTED"},
        )
        assert requested.status_code == 201, requested.text
        approved = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{requested.json()['id']}/approve",
            headers=approver_headers,
            json={"reason": "erase every nested derivative"},
        )
        assert approved.status_code == 200, approved.text

        for pack_id in (inner_pack_id, outer_pack_id):
            detail = await app_client.get(
                f"/api/v1/evidence-packs/{pack_id}",
                headers=requester_headers,
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["status"] == "UNAVAILABLE"
            assert detail.json()["zip_blob_sha256"] is None

        assert inner_pack_record_id is not None and outer_pack_record_id is not None
        async with get_sessionmaker()() as s:
            inner_pack = await s.get(EvidencePack, inner_pack_id)
            outer_pack = await s.get(EvidencePack, outer_pack_id)
            assert inner_pack is not None and outer_pack is not None
            assert inner_pack.status is PackStatus.UNAVAILABLE
            assert outer_pack.status is PackStatus.UNAVAILABLE
            assert (
                inner_pack.invalidated_by_disposition_event_id
                == outer_pack.invalidated_by_disposition_event_id
            )
            source_event_id = inner_pack.invalidated_by_disposition_event_id
            assert source_event_id is not None
            derived_record_ids = set(
                (
                    await s.execute(
                        select(DispositionEvent.record_id).where(
                            DispositionEvent.derived_from_disposition_event_id == source_event_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert derived_record_ids == {inner_pack_record_id, outer_pack_record_id}
            for record_id in (inner_pack_record_id, outer_pack_record_id):
                record = await s.get(Record, record_id)
                assert record is not None
                assert record.disposition_state is RecordDispositionState.DISPOSED
            for sha in artifact_locations:
                assert await s.get(Blob, sha) is None

        for sha, (bucket, object_key) in artifact_locations.items():
            assert not (await storage.head(object_key, bucket=bucket)).exists, sha
    finally:
        record_ids = [uuid.UUID(rid)] if rid else []
        if inner_pack_record_id is not None:
            record_ids.append(inner_pack_record_id)
        if outer_pack_record_id is not None:
            record_ids.append(outer_pack_record_id)
        if record_ids:
            async with get_sessionmaker()() as s:
                await s.execute(
                    delete(PendingBlobPurge).where(PendingBlobPurge.record_id.in_(record_ids))
                )
                if rid:
                    await s.execute(
                        delete(WormDestroyRequest).where(
                            WormDestroyRequest.record_id == uuid.UUID(rid)
                        )
                    )
                await s.commit()
        await _delivery_teardown(
            record_ids=[],
            pack_id=outer_pack_id,
            process_id=outer_process_id,
        )
        await _delivery_teardown(
            record_ids=[rid] if rid else [],
            pack_id=inner_pack_id,
            process_id=inner_process_id,
        )


async def test_r27_pack_purge_storage_failure_is_reaper_recoverable(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Derived ZIP/portfolio markers retain source request/event lineage after a storage fault."""
    requester_subject = _subject("pack-r27-reap-a")
    approver_subject = _subject("pack-r27-reap-b")
    requester_id = await _grant(requester_subject, (*_PACK_PERMS, "record.dispose"))
    await _grant(approver_subject, ("record.read", "record.dispose"))
    requester_headers = _auth(token_factory, requester_subject)
    approver_headers = _auth(token_factory, approver_subject)
    process_id = await _make_process(requester_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    pack_record_id: uuid.UUID | None = None
    rid = ""
    try:
        pack_uuid, rid = await _make_sealed_pack(app_client, requester_headers, process_id)
        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            assert pack is not None
            assert pack.pack_record_id is not None
            assert pack.zip_blob_sha256 is not None
            assert pack.portfolio_blob_sha256 is not None
            pack_record_id = pack.pack_record_id
            zip_sha = pack.zip_blob_sha256
            portfolio_sha = pack.portfolio_blob_sha256
            zip_blob = await s.get(Blob, zip_sha)
            portfolio_blob = await s.get(Blob, portfolio_sha)
            assert zip_blob is not None and portfolio_blob is not None
            locations = {
                zip_sha: (zip_blob.bucket, zip_blob.object_key),
                portfolio_sha: (portfolio_blob.bucket, portfolio_blob.object_key),
            }

        request_id = uuid.UUID(
            (
                await app_client.post(
                    f"/api/v1/records/{rid}/worm-destroy-requests",
                    headers=requester_headers,
                    json={"legal_basis": "court order PACK-361-REAPER"},
                )
            ).json()["id"]
        )

        async def _storage_down(*_args: object, **_kwargs: object) -> None:
            raise ClientError(
                {"Error": {"Code": "ServiceUnavailable", "Message": "simulated outage"}},
                "DeleteObject",
            )

        monkeypatch.setattr(storage, "purge_object", _storage_down)
        approved = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{request_id}/approve",
            headers=approver_headers,
            json={},
        )
        assert approved.status_code == 200, approved.text

        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            assert pack is not None and pack.status is PackStatus.UNAVAILABLE
            assert await s.get(Blob, zip_sha) is None
            assert await s.get(Blob, portfolio_sha) is None
            derived_markers = list(
                (
                    await s.execute(
                        select(PendingBlobPurge).where(PendingBlobPurge.record_id == pack_record_id)
                    )
                )
                .scalars()
                .all()
            )
            assert {marker.sha256 for marker in derived_markers} == {zip_sha, portfolio_sha}
            assert {marker.bypass_governance for marker in derived_markers} == {False, True}
            for marker in derived_markers:
                assert marker.worm_destroy_request_id == request_id
                assert marker.disposition_event_id is not None
                event = await s.get(DispositionEvent, marker.disposition_event_id)
                assert event is not None
                assert event.derived_from_disposition_event_id is not None

        for sha, (bucket, key) in locations.items():
            assert (await storage.head(key, bucket=bucket)).exists, sha

        monkeypatch.undo()
        async with get_sessionmaker()() as s:
            summary = await disposition.reap_pending_blob_purges(s)
        assert summary["refused"] == 0
        for sha, (bucket, key) in locations.items():
            assert not (await storage.head(key, bucket=bucket)).exists, sha
        async with get_sessionmaker()() as s:
            assert (
                await s.scalar(
                    select(func.count())
                    .select_from(PendingBlobPurge)
                    .where(PendingBlobPurge.record_id.in_([uuid.UUID(rid), pack_record_id]))
                )
                == 0
            )
    finally:
        record_ids = [uuid.UUID(rid)] if rid else []
        if pack_record_id is not None:
            record_ids.append(pack_record_id)
        if record_ids:
            async with get_sessionmaker()() as s:
                await s.execute(
                    delete(PendingBlobPurge).where(PendingBlobPurge.record_id.in_(record_ids))
                )
                if rid:
                    await s.execute(
                        delete(WormDestroyRequest).where(
                            WormDestroyRequest.record_id == uuid.UUID(rid)
                        )
                    )
                await s.commit()
        await _delivery_teardown(
            record_ids=[rid] if rid else [], pack_id=pack_uuid, process_id=process_id
        )


async def test_r27_direct_pack_record_destroy_reuses_source_event(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Destroying the registered pack Record invalidates its pack without a duplicate event."""
    requester_subject = _subject("pack-r27-direct-a")
    approver_subject = _subject("pack-r27-direct-b")
    requester_id = await _grant(requester_subject, (*_PACK_PERMS, "record.dispose"))
    await _grant(approver_subject, ("record.read", "record.dispose"))
    requester_headers = _auth(token_factory, requester_subject)
    approver_headers = _auth(token_factory, approver_subject)
    process_id = await _make_process(requester_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    pack_record_id: uuid.UUID | None = None
    rid = ""
    try:
        pack_uuid, rid = await _make_sealed_pack(app_client, requester_headers, process_id)
        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            assert pack is not None and pack.pack_record_id is not None
            pack_record_id = pack.pack_record_id

        request_id = (
            await app_client.post(
                f"/api/v1/records/{pack_record_id}/worm-destroy-requests",
                headers=requester_headers,
                json={"legal_basis": "court order PACK-RECORD"},
            )
        ).json()["id"]
        approved = await app_client.post(
            f"/api/v1/records/{pack_record_id}/worm-destroy-requests/{request_id}/approve",
            headers=approver_headers,
            json={},
        )
        assert approved.status_code == 200, approved.text

        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            assert pack is not None
            assert pack.status is PackStatus.UNAVAILABLE
            assert pack.invalidated_by_disposition_event_id is not None
            events = list(
                (
                    await s.execute(
                        select(DispositionEvent).where(DispositionEvent.record_id == pack_record_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(events) == 1
            assert events[0].id == pack.invalidated_by_disposition_event_id
            assert events[0].derived_from_disposition_event_id is None
            member = await s.get(Record, uuid.UUID(rid))
            assert member is not None
            assert member.disposition_state is not RecordDispositionState.DISPOSED
    finally:
        record_ids = [uuid.UUID(rid)] if rid else []
        if pack_record_id is not None:
            record_ids.append(pack_record_id)
        if record_ids:
            async with get_sessionmaker()() as s:
                await s.execute(
                    delete(PendingBlobPurge).where(PendingBlobPurge.record_id.in_(record_ids))
                )
                if pack_record_id is not None:
                    await s.execute(
                        delete(WormDestroyRequest).where(
                            WormDestroyRequest.record_id == pack_record_id
                        )
                    )
                await s.commit()
        await _delivery_teardown(
            record_ids=[rid] if rid else [], pack_id=pack_uuid, process_id=process_id
        )


async def test_pack_build_and_r27_destroy_serialize_without_a_last_copy(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If build wins the RW lock it seals first, then the waiting R27 transaction invalidates it."""
    requester_subject = _subject("pack-r27-race-a")
    approver_subject = _subject("pack-r27-race-b")
    requester_id = await _grant(requester_subject, (*_PACK_PERMS, "record.dispose"))
    await _grant(approver_subject, ("record.read", "record.dispose"))
    requester_headers = _auth(token_factory, requester_subject)
    approver_headers = _auth(token_factory, approver_subject)
    process_id = await _make_process(requester_id, f"Proc-{uuid.uuid4().hex[:8]}")
    pack_uuid: uuid.UUID | None = None
    pack_record_id: uuid.UUID | None = None
    rid = ""
    build_task: asyncio.Task[None] | None = None
    approve_task: asyncio.Task[httpx.Response] | None = None
    release_build = asyncio.Event()
    try:
        sha = await _upload_evidence(
            app_client, requester_headers, f"race-{uuid.uuid4().hex}".encode()
        )
        rid = (
            await _capture(
                app_client,
                requester_headers,
                record_type="EVIDENCE",
                title="race source",
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        await _link_process(app_client, requester_headers, rid, process_id)
        created = await app_client.post(
            "/api/v1/evidence-packs",
            headers=requester_headers,
            json={"title": "Race pack", "scope_kind": "PROCESS", "process_ids": [str(process_id)]},
        )
        assert created.status_code == 201, created.text
        pack_uuid = uuid.UUID(created.json()["id"])
        async with get_sessionmaker()() as s:
            pack = await packs_repo.get_pack(s, pack_uuid, for_update=True)
            assert pack is not None
            pack.status = PackStatus.BUILDING
            pack.build_started_at = datetime.datetime.now(datetime.UTC)
            await s.commit()

        request_id = (
            await app_client.post(
                f"/api/v1/records/{rid}/worm-destroy-requests",
                headers=requester_headers,
                json={"legal_basis": "court order PACK-RACE"},
            )
        ).json()["id"]

        build_module = importlib.import_module("easysynq_api.services.packs.build")
        locks_module = importlib.import_module("easysynq_api.services.packs.locks")
        real_gather = build_module._gather_record_files
        real_exclusive = locks_module.lock_pack_erasure_exclusive
        build_paused = asyncio.Event()
        exclusive_entered = asyncio.Event()
        exclusive_acquired = asyncio.Event()

        async def _pause_gather(
            session: object, record_id: uuid.UUID
        ) -> tuple[bool, list[tuple[str, bytes]], list[str]]:
            if record_id == uuid.UUID(rid):
                build_paused.set()
                await release_build.wait()
            return await real_gather(session, record_id)

        async def _observe_exclusive(session: object, org_id: uuid.UUID) -> None:
            exclusive_entered.set()
            await real_exclusive(session, org_id)
            exclusive_acquired.set()

        monkeypatch.setattr(build_module, "_gather_record_files", _pause_gather)
        monkeypatch.setattr(locks_module, "lock_pack_erasure_exclusive", _observe_exclusive)

        async def _run_build() -> None:
            async with get_sessionmaker()() as s:
                await build_module.build(s, pack_uuid)

        build_task = asyncio.create_task(_run_build())
        await asyncio.wait_for(build_paused.wait(), timeout=10)
        approve_task = asyncio.create_task(
            app_client.post(
                f"/api/v1/records/{rid}/worm-destroy-requests/{request_id}/approve",
                headers=approver_headers,
                json={},
            )
        )
        await asyncio.wait_for(exclusive_entered.wait(), timeout=10)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(exclusive_acquired.wait(), timeout=0.2)
        assert not approve_task.done()

        release_build.set()
        approved = await asyncio.wait_for(approve_task, timeout=20)
        await asyncio.wait_for(build_task, timeout=20)
        assert approved.status_code == 200, approved.text

        async with get_sessionmaker()() as s:
            pack = await s.get(EvidencePack, pack_uuid)
            assert pack is not None
            assert pack.status is PackStatus.UNAVAILABLE
            assert pack.zip_blob_sha256 is None
            assert pack.pack_record_id is not None
            pack_record_id = pack.pack_record_id
            pack_record = await s.get(Record, pack_record_id)
            assert pack_record is not None
            assert pack_record.disposition_state is RecordDispositionState.DISPOSED
    finally:
        release_build.set()
        pending_tasks = [
            task for task in (approve_task, build_task) if task is not None and not task.done()
        ]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        record_ids = [uuid.UUID(rid)] if rid else []
        if pack_record_id is None and pack_uuid is not None:
            async with get_sessionmaker()() as s:
                pack = await s.get(EvidencePack, pack_uuid)
                if pack is not None and pack.pack_record_id is not None:
                    pack_record_id = pack.pack_record_id
        if pack_record_id is not None:
            record_ids.append(pack_record_id)
        if record_ids:
            async with get_sessionmaker()() as s:
                await s.execute(
                    delete(PendingBlobPurge).where(PendingBlobPurge.record_id.in_(record_ids))
                )
                if rid:
                    await s.execute(
                        delete(WormDestroyRequest).where(
                            WormDestroyRequest.record_id == uuid.UUID(rid)
                        )
                    )
                await s.commit()
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
