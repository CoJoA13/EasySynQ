"""S-rec-2 integration proofs — records retention & disposition over HTTP + the Beat sweep, against
testcontainer Postgres + MinIO + Redis.

Strict shared-DB isolation contract: every record is captured under a per-test OVERRIDE retention
policy (``retention_policy_id=…`` at capture) so no ``applies_to`` matching collides across tests;
the sweep is driven directly via ``sweep_due_records(session, now=…)`` (never the Beat task), and a
test makes only its OWN back-dated records due (others use the P10Y RETAIN_PERMANENT default, never
swept). Assertions are scoped to the test's own record id; teardown deletes the disposition_event +
worm_destroy_request + evidence_blob + record + documented_information rows before the pinned policy
(the FK RESTRICT chain). Records disposition rides on a SYSTEM ``record.dispose`` override (authz is
proven in S2)."""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Callable

import pytest
from botocore.exceptions import ClientError
from httpx import AsyncClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._retention_enums import DispositionAction, RetentionBasis
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.disposition_event import DispositionEvent
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.evidence_blob import EvidenceBlob
from easysynq_api.db.models.pending_blob_purge import PendingBlobPurge
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.retention_policy import RetentionPolicy
from easysynq_api.db.models.storage_config import StorageConfig
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.models.worm_destroy_request import WormDestroyRequest
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.records import disposition, sweep_due_records
from easysynq_api.services.vault import storage

from ._owner_db import owner_delete_disposition_events
from .test_records import _capture, _grant, _subject, _upload_evidence
from .test_vault import _auth

pytestmark = pytest.mark.integration

_DISPOSITION_PERMS = ("record.read", "record.create", "record.dispose")


# --- helpers -----------------------------------------------------------------------------


async def _org_id(user_id: uuid.UUID) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, user_id)
        assert user is not None
        return user.org_id


async def _seed_policy(
    org_id: uuid.UUID,
    *,
    action: DispositionAction,
    review_required: bool,
    duration: str = "P1D",
) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        pol = RetentionPolicy(
            org_id=org_id,
            name=f"P-{uuid.uuid4().hex[:10]}",
            basis=RetentionBasis.CAPTURED_AT,
            duration=duration,
            disposition_action=action,
            review_required=review_required,
        )
        s.add(pol)
        await s.commit()
        return pol.id


async def _backdate(record_id: str, *, days: int) -> None:
    """Move the record's retention_basis_date into the past so its clock has elapsed at today."""
    when = datetime.date.today() - datetime.timedelta(days=days)
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Record)
            .where(Record.id == uuid.UUID(record_id))
            .values(retention_basis_date=when)
        )
        await s.commit()


async def _run_sweep(now: datetime.datetime | None = None) -> dict[str, int]:
    async with get_sessionmaker()() as s:
        return await sweep_due_records(s, now=now)


async def _state(record_id: str) -> tuple[str, bool]:
    async with get_sessionmaker()() as s:
        rec = await s.get(Record, uuid.UUID(record_id))
        assert rec is not None
        return rec.disposition_state.value, rec.legal_hold


async def _count_events(record_id: str, event_type: EventType) -> int:
    async with get_sessionmaker()() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == uuid.UUID(record_id),
                    AuditEvent.event_type == event_type,
                )
            )
            or 0
        )


async def _disposition_events(record_id: str) -> list[DispositionEvent]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(DispositionEvent).where(
                        DispositionEvent.record_id == uuid.UUID(record_id)
                    )
                )
            )
            .scalars()
            .all()
        )


async def _set_self_disposition(org_id: uuid.UUID, value: bool) -> None:
    """Flip the org's SoD-6 relaxation flag (system_config.allow_self_disposition)."""
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        assert cfg is not None  # OPERATIONAL install seeds a system_config row
        cfg.allow_self_disposition = value
        await s.commit()


async def _set_object_lock_mode(org_id: uuid.UUID, mode: str) -> None:
    async with get_sessionmaker()() as s:
        cfg = await s.scalar(select(StorageConfig).where(StorageConfig.org_id == org_id))
        if cfg is None:
            s.add(StorageConfig(org_id=org_id, object_lock_mode=mode))
        else:
            cfg.object_lock_mode = mode
        await s.commit()


async def _cleanup(policy_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as s:
        pinned = list(
            (await s.execute(select(Record.id).where(Record.retention_policy_id == policy_id)))
            .scalars()
            .all()
        )
        if pinned:
            # disposition_event is append-only for the app role (0072 REVOKE UPDATE,DELETE) → its
            # teardown DELETE must run as the OWNER, not the app role this session connects as.
            await owner_delete_disposition_events(pinned)
            await s.execute(
                delete(WormDestroyRequest).where(WormDestroyRequest.record_id.in_(pinned))
            )
            await s.execute(delete(EvidenceBlob).where(EvidenceBlob.record_id.in_(pinned)))
            await s.execute(delete(Record).where(Record.id.in_(pinned)))
            await s.execute(
                delete(DocumentedInformation).where(DocumentedInformation.id.in_(pinned))
            )
        await s.execute(delete(RetentionPolicy).where(RetentionPolicy.id == policy_id))
        await s.commit()


# --- the sweep ---------------------------------------------------------------------------


async def test_sweep_flips_and_auto_disposes_low_risk(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """ACTIVE → DUE_FOR_REVIEW + auto-DISPOSED (ARCHIVE_COLD, review_required=false): a state-only
    tombstone (no byte purge), the record + its history persist, two SYSTEM audit events emitted."""
    subject = _subject("disp")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="comp",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        await _backdate(rid, days=30)

        await _run_sweep()

        state, _hold = await _state(rid)
        assert state == "DISPOSED"
        events = await _disposition_events(rid)
        assert len(events) == 1
        assert events[0].action is DispositionAction.ARCHIVE_COLD
        assert events[0].approved_by is None  # system auto-dispose
        assert events[0].is_worm_destroy is False
        assert await _count_events(rid, EventType.RECORD_DISPOSITION_DUE) == 1
        assert await _count_events(rid, EventType.RECORD_DISPOSED) == 1
        # The tombstone: the record row itself still exists (metadata + history preserved).
        get = await app_client.get(f"/api/v1/records/{rid}", headers=h)
        assert get.status_code == 200
    finally:
        await _cleanup(policy_id)


async def test_sweep_review_required_stops_then_human_disposes(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """review_required=true → the sweep stops at DUE_FOR_REVIEW; a human PATCH then disposes it. The
    disposer is a DISTINCT actor from the capturer (SoD-6, S-rec-4)."""
    capturer = _subject("disp")
    user_id = await _grant(capturer, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    disposer = _subject("disp-reviewer")
    await _grant(disposer, _DISPOSITION_PERMS)
    h_disposer = _auth(token_factory, disposer)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=True
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="comp",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        await _backdate(rid, days=30)

        await _run_sweep()
        state, _ = await _state(rid)
        assert state == "DUE_FOR_REVIEW"  # awaits human approval
        assert await _count_events(rid, EventType.RECORD_DISPOSED) == 0

        # A distinct human approves the disposition (SoD-6: not the capturer).
        patch = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=h_disposer, json={"to_state": "DISPOSED"}
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["disposition_state"] == "DISPOSED"
        assert await _count_events(rid, EventType.RECORD_DISPOSED) == 1
    finally:
        await _cleanup(policy_id)


async def test_sweep_destroy_worm_unexpired_stays_due(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A DESTROY whose evidence WORM lock has not expired: the sweep flips to DUE_FOR_REVIEW but
    does NOT auto-destroy (no bypass in the sweep) — it leaves the record for a later sweep."""
    subject = _subject("disp")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    policy_id = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=False)
    try:
        sha = await _upload_evidence(app_client, h, f"e-{uuid.uuid4().hex}".encode())
        rid = (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="cal",
                retention_policy_id=str(policy_id),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        await _backdate(rid, days=30)

        await _run_sweep()
        state, _ = await _state(rid)
        assert state == "DUE_FOR_REVIEW"  # WORM lock unexpired → not destroyed
        assert await _count_events(rid, EventType.RECORD_DISPOSITION_DUE) == 1
        assert await _count_events(rid, EventType.RECORD_DISPOSED) == 0
        assert await _disposition_events(rid) == []
    finally:
        await _cleanup(policy_id)


# --- legal hold --------------------------------------------------------------------------


async def test_legal_hold_blocks_sweep_and_dispose(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("disp")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="comp",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]

        # reason is mandatory.
        bad = await app_client.post(
            f"/api/v1/records/{rid}/legal-hold", headers=h, json={"action": "place"}
        )
        assert bad.status_code == 422

        place = await app_client.post(
            f"/api/v1/records/{rid}/legal-hold",
            headers=h,
            json={"action": "place", "reason": "litigation 2026-06"},
        )
        assert place.status_code == 200, place.text
        state, hold = await _state(rid)
        assert state == "ON_HOLD" and hold is True

        # A held record is invisible to the sweep even with an elapsed clock.
        await _backdate(rid, days=30)
        await _run_sweep()
        state, _ = await _state(rid)
        assert state == "ON_HOLD"

        # PATCH dispose on a held record is refused.
        patch = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=h, json={"to_state": "DISPOSED"}
        )
        assert patch.status_code == 409
        assert patch.json()["code"] == "on_legal_hold"

        # Release → ACTIVE.
        release = await app_client.post(
            f"/api/v1/records/{rid}/legal-hold",
            headers=h,
            json={"action": "release", "reason": "hold lifted"},
        )
        assert release.status_code == 200
        state, hold = await _state(rid)
        assert state == "ACTIVE" and hold is False
        assert await _count_events(rid, EventType.RECORD_LEGAL_HOLD_PLACED) == 1
        assert await _count_events(rid, EventType.RECORD_LEGAL_HOLD_RELEASED) == 1
    finally:
        await _cleanup(policy_id)


# --- manual disposition refusals (GDPR refused-with-reason, R27) --------------------------


async def test_manual_destroy_worm_unexpired_refused_and_audited(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    capturer = _subject("disp")
    user_id = await _grant(capturer, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    disposer = _subject(
        "disp-b"
    )  # distinct disposer so SoD-6 passes → the WORM guard is the refusal
    await _grant(disposer, _DISPOSITION_PERMS)
    h_disposer = _auth(token_factory, disposer)
    policy_id = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        sha = await _upload_evidence(app_client, h, f"e-{uuid.uuid4().hex}".encode())
        rid = (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="cal",
                retention_policy_id=str(policy_id),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        # ACTIVE → DUE (manual early review), then a DESTROY attempt while the WORM lock is live.
        due = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=h, json={"to_state": "DUE_FOR_REVIEW"}
        )
        assert due.status_code == 200, due.text
        refused = await app_client.patch(
            f"/api/v1/records/{rid}/disposition",
            headers=h_disposer,
            json={"to_state": "DISPOSED"},
        )
        assert refused.status_code == 409
        assert refused.json()["code"] == "worm_lock_unexpired"
        # The refusal is LOGGED (GDPR refused-with-reason), and the record is NOT disposed.
        assert await _count_events(rid, EventType.RECORD_ERASURE_REFUSED) == 1
        state, _ = await _state(rid)
        assert state == "DUE_FOR_REVIEW"
    finally:
        await _cleanup(policy_id)


# --- R27 dual-control WORM-destroy-under-legal-order -------------------------------------


async def test_dual_control_destroy_happy_path_and_same_actor_block(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Two distinct authorizers destroy WORM evidence before lock expiry; the bytes are physically
    gone; a same-actor approval is refused; the tombstone records both actors + the legal basis."""
    a_subject = _subject("dca")
    b_subject = _subject("dcb")
    user_a = await _grant(a_subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_a)
    await _grant(b_subject, _DISPOSITION_PERMS)
    ha = _auth(token_factory, a_subject)
    hb = _auth(token_factory, b_subject)
    policy_id = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        sha = await _upload_evidence(app_client, ha, f"e-{uuid.uuid4().hex}".encode())
        rid = (
            await _capture(
                app_client,
                ha,
                record_type="CALIBRATION",
                title="cal",
                retention_policy_id=str(policy_id),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        # Sanity: the WORM object exists in the records bucket before destruction.
        head_before = await storage.head(sha, bucket=storage._records_bucket())
        assert head_before.exists
        # R27 legal-erasure headline case: the record also carries Mode-B structured content, which
        # the WORM-destroy must erase alongside the bytes (content_hash stays the anchor).
        async with get_sessionmaker()() as s:
            await s.execute(
                update(Record)
                .where(Record.id == uuid.UUID(rid))
                .values(form_field_values={"subject": "Jane Doe"}, content_hash="sha256:anchor")
            )
            await s.commit()

        req = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests",
            headers=ha,
            json={"legal_basis": "court order EX-2026-42"},
        )
        assert req.status_code == 201, req.text
        req_id = req.json()["id"]

        # Same actor (the requester) cannot approve — dual control.
        same = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{req_id}/approve", headers=ha, json={}
        )
        assert same.status_code == 409
        assert same.json()["code"] == "dual_control_same_actor"

        # A second, distinct actor approves → execute.
        ok = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{req_id}/approve", headers=hb, json={}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["disposition_state"] == "DISPOSED"

        # The WORM bytes are physically gone (governance bypass actually deleted the version)...
        head_after = await storage.head(sha, bucket=storage._records_bucket())
        assert not head_after.exists
        # ...and the now-false blob row is dropped (the invariant: a blob row exists iff its object
        # does — so backup/restore never tries to copy a destroyed blob).
        async with get_sessionmaker()() as s:
            assert await s.get(Blob, sha) is None
            # ...and the structured content is erased in the same txn as the tombstone, while
            # content_hash survives as the verification anchor (the finding-A fix on the R27 path).
            destroyed = await s.get(Record, uuid.UUID(rid))
            assert destroyed is not None
            assert destroyed.form_field_values is None
            assert destroyed.content_hash == "sha256:anchor"

        events = await _disposition_events(rid)
        assert len(events) == 1
        ev = events[0]
        assert ev.is_worm_destroy is True
        assert ev.action is DispositionAction.DESTROY
        assert ev.requested_by == user_a  # first authorizer
        assert ev.approved_by is not None and ev.approved_by != user_a  # distinct second authorizer
        assert ev.legal_basis == "court order EX-2026-42"
        assert await _count_events(rid, EventType.RECORD_WORM_DESTROYED) == 1
    finally:
        await _cleanup(policy_id)


async def test_dual_control_compliance_mode_refused(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Under COMPLIANCE object-lock mode the bypass is impossible → approve is refused (audited),
    the request stays open, and the record is NOT disposed."""
    a_subject = _subject("dca")
    b_subject = _subject("dcb")
    user_a = await _grant(a_subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_a)
    await _grant(b_subject, _DISPOSITION_PERMS)
    ha = _auth(token_factory, a_subject)
    hb = _auth(token_factory, b_subject)
    policy_id = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        await _set_object_lock_mode(org_id, "COMPLIANCE")
        sha = await _upload_evidence(app_client, ha, f"e-{uuid.uuid4().hex}".encode())
        rid = (
            await _capture(
                app_client,
                ha,
                record_type="CALIBRATION",
                title="cal",
                retention_policy_id=str(policy_id),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        req_id = (
            await app_client.post(
                f"/api/v1/records/{rid}/worm-destroy-requests",
                headers=ha,
                json={"legal_basis": "erasure order"},
            )
        ).json()["id"]
        refused = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{req_id}/approve", headers=hb, json={}
        )
        assert refused.status_code == 409
        assert refused.json()["code"] == "compliance_mode_denies_destroy"
        assert await _count_events(rid, EventType.RECORD_ERASURE_REFUSED) == 1
        state, _ = await _state(rid)
        assert state != "DISPOSED"  # not destroyed
        # The bytes survive (the bypass never ran).
        head = await storage.head(sha, bucket=storage._records_bucket())
        assert head.exists
    finally:
        await _set_object_lock_mode(org_id, "GOVERNANCE")  # restore for other tests (shared org)
        await _cleanup(policy_id)


async def test_dual_control_one_open_request_then_cancel(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("dca")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    policy_id = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="cal",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        first = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests",
            headers=h,
            json={"legal_basis": "order-1"},
        )
        assert first.status_code == 201
        req_id = first.json()["id"]
        # A second open request for the same record is refused (partial-unique / in-service guard).
        dup = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests",
            headers=h,
            json={"legal_basis": "order-2"},
        )
        assert dup.status_code == 409
        assert dup.json()["code"] == "worm_destroy_request_open"
        # Cancel the open one → a fresh request may then be opened.
        cancel = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{req_id}/cancel", headers=h, json={}
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"
        assert await _count_events(rid, EventType.RECORD_WORM_DESTROY_CANCELLED) == 1
        reopened = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests",
            headers=h,
            json={"legal_basis": "order-3"},
        )
        assert reopened.status_code == 201
    finally:
        await _cleanup(policy_id)


async def test_purge_failure_defers_to_reaper(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch 5 purge-AFTER-commit contract: if the immediate byte purge fails, the record is STILL
    disposed (the tombstone + blob-row delete + a ``pending_blob_purge`` marker committed FIRST) and
    the marker is left for the reaper — the bytes are erased on the next reaper pass, never a
    rolled-back disposition over deleted bytes. (Pre-Batch-5 the purge ran BEFORE the commit, so a
    failure rolled the disposition back; the ordering is inverted to keep backups restorable.)"""
    a_subject = _subject("dca")
    b_subject = _subject("dcb")
    user_a = await _grant(a_subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_a)
    await _grant(b_subject, _DISPOSITION_PERMS)
    ha = _auth(token_factory, a_subject)
    hb = _auth(token_factory, b_subject)
    policy_id = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        sha = await _upload_evidence(app_client, ha, f"e-{uuid.uuid4().hex}".encode())
        rid = (
            await _capture(
                app_client,
                ha,
                record_type="CALIBRATION",
                title="cal",
                retention_policy_id=str(policy_id),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        req_id = (
            await app_client.post(
                f"/api/v1/records/{rid}/worm-destroy-requests",
                headers=ha,
                json={"legal_basis": "order"},
            )
        ).json()["id"]

        async def _boom(*_a: object, **_k: object) -> int:
            raise ClientError(
                {"Error": {"Code": "ServiceUnavailable", "Message": "simulated storage outage"}},
                "DeleteObject",
            )

        monkeypatch.setattr(storage, "purge_object", _boom)
        # The immediate purge fails but is CAUGHT + deferred — the approve SUCCEEDS (disposed).
        ok = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{req_id}/approve", headers=hb, json={}
        )
        assert ok.status_code == 200, ok.text
        # The record IS disposed (tombstone committed), the blob row gone, and a marker awaits the
        # reaper; the bytes are still present (the purge failed).
        state, _ = await _state(rid)
        assert state == "DISPOSED"
        assert len(await _disposition_events(rid)) == 1
        assert (await storage.head(sha, bucket=storage._records_bucket())).exists
        async with get_sessionmaker()() as s:
            assert await s.get(Blob, sha) is None
            pending = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert pending == 1
        # Restore the real purge; the reaper completes the erasure the crash deferred.
        monkeypatch.undo()
        async with get_sessionmaker()() as s:
            await disposition.reap_pending_blob_purges(s)
        assert not (await storage.head(sha, bucket=storage._records_bucket())).exists
        async with get_sessionmaker()() as s:
            pending = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert pending == 0
    finally:
        await _cleanup(policy_id)


# --- SoD-6 creator-not-disposer (S-rec-4, doc 07 §7) -------------------------------------


async def _to_due(app_client: AsyncClient, h: dict[str, str], rid: str) -> None:
    """Advance ACTIVE → DUE_FOR_REVIEW (a manual early review; not SoD-6-gated)."""
    r = await app_client.patch(
        f"/api/v1/records/{rid}/disposition", headers=h, json={"to_state": "DUE_FOR_REVIEW"}
    )
    assert r.status_code == 200, r.text


async def test_sod6_self_disposition_blocked_and_audited(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The capturer may NOT dispose their own record (default-enforced). Proves the gate is NOT
    bypassed by the SYSTEM ``record.dispose`` override the capturer holds — only the config flag
    relaxes it. The refusal is audited DISPOSITION_REFUSED_SOD, the record stays DUE_FOR_REVIEW."""
    capturer = _subject("disp")
    user_id = await _grant(capturer, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="c",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        await _to_due(app_client, h, rid)
        refused = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=h, json={"to_state": "DISPOSED"}
        )
        assert refused.status_code == 409
        assert refused.json()["code"] == "sod_self_disposition"
        assert await _count_events(rid, EventType.DISPOSITION_REFUSED_SOD) == 1
        assert await _count_events(rid, EventType.RECORD_DISPOSED) == 0
        state, _ = await _state(rid)
        assert state == "DUE_FOR_REVIEW"
    finally:
        await _cleanup(policy_id)


async def test_sod6_distinct_disposer_allowed(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A distinct disposer (not the capturer) disposes successfully."""
    capturer = _subject("disp")
    user_id = await _grant(capturer, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    disposer = _subject("disp-b")
    await _grant(disposer, _DISPOSITION_PERMS)
    hb = _auth(token_factory, disposer)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="c",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        await _to_due(app_client, h, rid)
        ok = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=hb, json={"to_state": "DISPOSED"}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["disposition_state"] == "DISPOSED"
        assert await _count_events(rid, EventType.RECORD_DISPOSED) == 1
    finally:
        await _cleanup(policy_id)


async def test_sod6_relaxed_by_config_flag(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """With allow_self_disposition=true the capturer may self-dispose (small/solo org)."""
    capturer = _subject("disp")
    user_id = await _grant(capturer, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        await _set_self_disposition(org_id, True)
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="c",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        await _to_due(app_client, h, rid)
        ok = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=h, json={"to_state": "DISPOSED"}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["disposition_state"] == "DISPOSED"
    finally:
        await _set_self_disposition(org_id, False)  # restore strict for the shared org
        await _cleanup(policy_id)


async def test_sod6_does_not_gate_due_or_active_transitions(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """SoD-6 gates only DISPOSED: the capturer may still flip ACTIVE<->DUE_FOR_REVIEW themselves."""
    capturer = _subject("disp")
    user_id = await _grant(capturer, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="c",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        to_due = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=h, json={"to_state": "DUE_FOR_REVIEW"}
        )
        assert to_due.status_code == 200, to_due.text
        to_active = await app_client.patch(
            f"/api/v1/records/{rid}/disposition", headers=h, json={"to_state": "ACTIVE"}
        )
        assert to_active.status_code == 200, to_active.text
        assert to_active.json()["disposition_state"] == "ACTIVE"
    finally:
        await _cleanup(policy_id)


async def test_sod6_sweep_is_exempt(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The Beat sweep auto-disposes a self-captured record (system actor) — SoD-6 (a human-only
    gate) never blocks it, even though the only human is the capturer."""
    capturer = _subject("disp")
    user_id = await _grant(capturer, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, capturer)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="c",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        await _backdate(rid, days=30)
        await _run_sweep()
        state, _ = await _state(rid)
        assert state == "DISPOSED"
        assert await _count_events(rid, EventType.DISPOSITION_REFUSED_SOD) == 0
    finally:
        await _cleanup(policy_id)


async def test_sod6_keys_off_record_captured_by_for_correction(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """SoD-6 keys off the RECORD's own captured_by: for a correction that is the corrector, so the
    corrector cannot dispose the correction, but the ORIGINAL capturer (who did not capture it)
    can."""
    a = _subject("disp-a")
    user_a = await _grant(a, _DISPOSITION_PERMS)
    org_id = await _org_id(user_a)
    ha = _auth(token_factory, a)
    b = _subject("disp-b")
    await _grant(b, _DISPOSITION_PERMS)
    hb = _auth(token_factory, b)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    try:
        r1_id = (
            await _capture(
                app_client,
                ha,
                record_type="CALIBRATION",
                title="orig",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        # B captures the correction → R2.captured_by == B.
        r2_id = (
            await app_client.post(
                f"/api/v1/records/{r1_id}/correction",
                headers=hb,
                json={
                    "record_type": "CALIBRATION",
                    "title": "corrected",
                    "retention_policy_id": str(policy_id),
                },
            )
        ).json()["id"]
        await _to_due(app_client, hb, r2_id)
        # B (the corrector == R2's capturer) is blocked.
        refused = await app_client.patch(
            f"/api/v1/records/{r2_id}/disposition", headers=hb, json={"to_state": "DISPOSED"}
        )
        assert refused.status_code == 409
        assert refused.json()["code"] == "sod_self_disposition"
        # A (the original capturer, who did NOT capture R2) may dispose it.
        ok = await app_client.patch(
            f"/api/v1/records/{r2_id}/disposition", headers=ha, json={"to_state": "DISPOSED"}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["disposition_state"] == "DISPOSED"
    finally:
        await _cleanup(policy_id)


# --- GET /disposition --------------------------------------------------------------------


async def test_get_disposition_reports_retention_until(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("disp")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    policy_id = await _seed_policy(
        org_id, action=DispositionAction.DESTROY, review_required=False, duration="P3Y"
    )
    try:
        rid = (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="cal",
                retention_policy_id=str(policy_id),
            )
        ).json()["id"]
        got = await app_client.get(f"/api/v1/records/{rid}/disposition", headers=h)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["disposition_state"] == "ACTIVE"
        assert body["legal_hold"] is False
        # basis = captured_at = today → retention_until = today + 3Y.
        assert body["retention_until"] is not None
        assert body["retention_until"].startswith(str(datetime.date.today().year + 3))
        assert body["open_worm_destroy_request"] is None
    finally:
        await _cleanup(policy_id)


# --- Batch 4: WORM-erasure completeness --------------------------------------------------


async def test_destroy_nulls_form_field_values_while_archive_preserves(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[Batch 4] A DESTROY erases the record's structured ``form_field_values`` in the same txn as
    the tombstone — a Mode-B record's personal data must not survive a legal-erasure order — while
    ``content_hash`` stays the verification anchor. ARCHIVE_COLD preserves the content (a custody
    change, not an erasure). Pre-fix the DESTROY left every field value in the DB + served by the
    API."""
    subject = _subject("ffv-destroy")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    destroy_pol = await _seed_policy(
        org_id, action=DispositionAction.DESTROY, review_required=False
    )
    archive_pol = await _seed_policy(
        org_id, action=DispositionAction.ARCHIVE_COLD, review_required=False
    )
    content = {"name": "Jane Doe", "assessment": "sensitive comment"}
    try:
        rd = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="d",
                retention_policy_id=str(destroy_pol),
            )
        ).json()["id"]
        ra = (
            await _capture(
                app_client,
                h,
                record_type="COMPETENCE",
                title="a",
                retention_policy_id=str(archive_pol),
            )
        ).json()["id"]
        # Simulate a Mode-B structured record: stamp the JSONB content + a content_hash anchor.
        async with get_sessionmaker()() as s:
            for rid in (rd, ra):
                await s.execute(
                    update(Record)
                    .where(Record.id == uuid.UUID(rid))
                    .values(form_field_values=content, content_hash="sha256:anchor")
                )
            await s.commit()
        await _backdate(rd, days=400)
        await _backdate(ra, days=400)
        summary = await _run_sweep()
        assert summary["disposed"] >= 2, summary

        async with get_sessionmaker()() as s:
            recd = await s.get(Record, uuid.UUID(rd))
            reca = await s.get(Record, uuid.UUID(ra))
            assert recd is not None and reca is not None
            # DESTROY: structured content erased, hash anchor preserved.
            assert recd.disposition_state.value == "DISPOSED"
            assert recd.form_field_values is None
            assert recd.content_hash == "sha256:anchor"
            # ARCHIVE_COLD: content preserved (custody change, not erasure).
            assert reca.disposition_state.value == "DISPOSED"
            assert reca.form_field_values == content
        # The API no longer serves the destroyed structured content.
        got = await app_client.get(f"/api/v1/records/{rd}", headers=h)
        assert got.status_code == 200
        assert got.json()["form_field_values"] is None
    finally:
        await _cleanup(destroy_pol)
        await _cleanup(archive_pol)


async def test_disposition_event_append_only_for_app_role(
    app_under_test: object, dsns: dict[str, str]
) -> None:
    """[Batch 4 / AC#6a] The running app (the non-owner ``easysynq_app`` role) is structurally
    denied UPDATE and DELETE on the append-only ``disposition_event`` tombstone (SQLSTATE 42501,
    migration 0072) — so the R27 legal-erasure proof (``legal_basis`` + the dual-control approvers)
    cannot be altered or erased by an app-role compromise. Mirrors the audit_event/signature_event
    AC#6a proof. PostgreSQL checks the table privilege before row matching, so an empty table still
    42501s."""
    engine = create_async_engine(dsns["app"])
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for stmt in (
                "UPDATE disposition_event SET legal_basis = 'forged'",
                "DELETE FROM disposition_event",
            ):
                with pytest.raises(DBAPIError) as exc:
                    await session.execute(text(stmt))
                    await session.commit()
                assert getattr(exc.value.orig, "sqlstate", None) == "42501", stmt
                await session.rollback()
    finally:
        await engine.dispose()


# --- Batch 5: disposition txn / locking integrity ----------------------------------------


async def test_shared_blob_concurrent_disposition_purges_once(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Batch 5 finding 1: two records share ONE evidence blob; concurrent dispositions serialise on
    the blob FOR UPDATE lock so the LAST referencer purges — pre-fix both observed the peer live and
    skipped, orphaning the bytes. The immediate post-commit purge also leaves NO pending marker."""
    subject = _subject("shared-blob")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    pol = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        sha = await _upload_evidence(app_client, h, f"shared-{uuid.uuid4().hex}".encode())
        rids = [
            (
                await _capture(
                    app_client,
                    h,
                    record_type="CALIBRATION",
                    title=title,
                    retention_policy_id=str(pol),
                    evidence=[{"sha256": sha, "content_type": "application/pdf"}],
                )
            ).json()["id"]
            for title in ("r1", "r2")
        ]
        assert (await storage.head(sha, bucket=storage._records_bucket())).exists

        async def dispose(rid: str) -> None:
            async with get_sessionmaker()() as s:
                record = await s.get(Record, uuid.UUID(rid))
                assert record is not None
                specs = await disposition._mark_record_evidence_for_purge(s, record, bypass=True)
                disposition._write_tombstone(
                    s, record, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
                )
                await s.commit()
                await disposition._purge_marked(specs, sessionmaker=get_sessionmaker())

        await asyncio.gather(dispose(rids[0]), dispose(rids[1]))

        # Exactly one disposer purged the shared blob → bytes gone + blob row gone (not both-skip).
        assert not (await storage.head(sha, bucket=storage._records_bucket())).exists
        async with get_sessionmaker()() as s:
            assert await s.get(Blob, sha) is None
            markers = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert markers == 0  # the immediate post-commit purge cleared the marker
    finally:
        await _cleanup(pol)


async def test_sweep_forwards_loop_scoped_sessionmaker_to_purge(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch 5 cross-loop guard: the sweep must hand ``_purge_marked`` the sessionmaker it was
    passed (the Celery task's loop-scoped one), never fall back to the process-global pool — reusing
    the global engine across the task's per-invocation ``asyncio.run`` loop raises a cross-loop
    ``RuntimeError`` (NOT a ``SQLAlchemyError``, so it would escape the reaper deferral and fail the
    retention task). Pins the injection wiring the single-loop suite can't otherwise exercise."""

    async def _no_due(*_a: object, **_k: object) -> list[object]:
        return []  # no records to process → the sweep still calls _purge_marked([], sessionmaker=…)

    captured: dict[str, object] = {}

    async def _capture(_specs: object, *, sessionmaker: object) -> None:
        captured["sessionmaker"] = sessionmaker

    monkeypatch.setattr(disposition.repo, "due_active_records", _no_due)
    monkeypatch.setattr(disposition, "_purge_marked", _capture)
    sentinel: object = object()  # distinct from the global sessionmaker; never used (purge stubbed)
    async with get_sessionmaker()() as s:
        await disposition.sweep_due_records(s, purge_sessionmaker=sentinel)  # type: ignore[arg-type]
    assert captured["sessionmaker"] is sentinel  # forwarded through, NOT the global fallback


async def test_reaper_completes_stranded_purge(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Batch 5 finding 2 crash-recovery: if the immediate purge doesn't run (a crash after the
    disposition commit), a pending_blob_purge marker + the S3 bytes remain (the blob row is already
    gone → backups stay safe); the reaper purges the bytes idempotently and drops the marker."""
    subject = _subject("reaper")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    pol = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        sha = await _upload_evidence(app_client, h, f"reap-{uuid.uuid4().hex}".encode())
        rid = (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="reap",
                retention_policy_id=str(pol),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        # Dispose but SIMULATE A CRASH: mark + commit, skip the immediate _purge_marked.
        async with get_sessionmaker()() as s:
            record = await s.get(Record, uuid.UUID(rid))
            assert record is not None
            await disposition._mark_record_evidence_for_purge(s, record, bypass=True)
            disposition._write_tombstone(
                s, record, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
            )
            await s.commit()  # tombstone + blob-row delete + marker committed; bytes NOT yet purged

        # The blob row is gone (backups stay safe) but the bytes + the marker remain.
        assert (await storage.head(sha, bucket=storage._records_bucket())).exists
        async with get_sessionmaker()() as s:
            assert await s.get(Blob, sha) is None
            pending = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert pending == 1

        # The reaper completes the erasure (idempotent purge + marker drop).
        async with get_sessionmaker()() as s:
            summary = await disposition.reap_pending_blob_purges(s)
        assert summary["reaped"] >= 1
        assert not (await storage.head(sha, bucket=storage._records_bucket())).exists
        async with get_sessionmaker()() as s:
            pending = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert pending == 0
    finally:
        await _cleanup(pol)


async def test_recapture_before_purge_cancels_stale_marker(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Batch 5 P1: if the SAME content is re-captured after a DESTROY committed its mark (blob row
    deleted + marker written, bytes not yet purged), the re-capture re-owns the object (object_key
    is the content hash), so the reaper must SKIP erasing it and just drop the stale marker — never
    destroy the re-captured record's live evidence. Mutation-verify: without the blob-exists
    re-check the reaper would purge the shared object and the re-captured bytes would vanish."""
    subject = _subject("recapture")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    pol = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        content = f"recap-{uuid.uuid4().hex}".encode()
        sha = await _upload_evidence(app_client, h, content)
        r1 = (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="r1",
                retention_policy_id=str(pol),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        # Dispose r1 but skip the immediate purge (crash sim): blob row deleted, marker written.
        async with get_sessionmaker()() as s:
            record = await s.get(Record, uuid.UUID(r1))
            assert record is not None
            await disposition._mark_record_evidence_for_purge(s, record, bypass=True)
            disposition._write_tombstone(
                s, record, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
            )
            await s.commit()
        async with get_sessionmaker()() as s:
            assert await s.get(Blob, sha) is None  # blob row gone; object still present

        # RE-CAPTURE the identical content under a new record → re-creates the blob row.
        await _upload_evidence(app_client, h, content)  # re-stage the identical bytes
        (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="r2",
                retention_policy_id=str(pol),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()
        async with get_sessionmaker()() as s:
            assert await s.get(Blob, sha) is not None  # the re-capture re-created the blob row

        # The reaper must NOT erase the re-captured bytes — it drops the stale marker instead.
        async with get_sessionmaker()() as s:
            await disposition.reap_pending_blob_purges(s)
        assert (await storage.head(sha, bucket=storage._records_bucket())).exists  # r2 bytes intact
        async with get_sessionmaker()() as s:
            pending = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert pending == 0  # the stale marker was dropped, not replayed
    finally:
        await _cleanup(pol)


async def test_recapture_into_other_bucket_still_purges_records_object(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Batch 5 finding-1 follow-up (cross-bucket): ``blob.sha256`` is a GLOBAL content-addressed PK,
    so identical bytes can be re-owned by a blob row in a DIFFERENT bucket (a doc check-in lands
    the sha in the ``documents`` bucket) while a records-evidence marker still targets the
    ``records`` bucket — two physically distinct objects. The purge re-check keys on
    (sha, bucket, object_key), so it must STILL erase the orphaned records object and drop the
    marker, leaving the documents blob untouched. Mutation-verify: a sha-only re-check would treat
    the documents blob as a re-owner, cancel the marker, and leak the disposed record's evidence."""
    subject = _subject("xbucket")
    user_id = await _grant(subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_id)
    h = _auth(token_factory, subject)
    pol = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    doc_blob_sha: str | None = None
    try:
        content = f"xbucket-{uuid.uuid4().hex}".encode()
        sha = await _upload_evidence(app_client, h, content)
        rid = (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="xb",
                retention_policy_id=str(pol),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        # Dispose (crash sim): blob row deleted, records-bucket marker written; bytes remain.
        async with get_sessionmaker()() as s:
            record = await s.get(Record, uuid.UUID(rid))
            assert record is not None
            await disposition._mark_record_evidence_for_purge(s, record, bypass=True)
            disposition._write_tombstone(
                s, record, action=DispositionAction.DESTROY, policy_id=None, approved_by=None
            )
            await s.commit()
        # Simulate a DOCUMENT check-in re-owning the identical content in a DIFFERENT bucket: the
        # global blob PK re-appears, but pointing at documents/<sha>, NOT the records object.
        async with get_sessionmaker()() as s:
            s.add(
                Blob(
                    sha256=sha,
                    org_id=org_id,
                    size_bytes=len(content),
                    mime_type="application/pdf",
                    bucket=storage._doc_bucket(),
                    object_key=sha,
                    worm_locked=True,
                )
            )
            await s.commit()
            doc_blob_sha = sha
        # The reaper must NOT be fooled by the cross-bucket blob row — it purges records/<sha>.
        async with get_sessionmaker()() as s:
            await disposition.reap_pending_blob_purges(s)
        assert not (await storage.head(sha, bucket=storage._records_bucket())).exists  # purged
        async with get_sessionmaker()() as s:
            pending = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert pending == 0  # marker dropped only AFTER the real purge
            assert await s.get(Blob, sha) is not None  # the documents blob row is untouched
    finally:
        if doc_blob_sha is not None:
            async with get_sessionmaker()() as s:
                await s.execute(delete(Blob).where(Blob.sha256 == doc_blob_sha))
                await s.commit()
        await _cleanup(pol)


async def test_purge_post_commit_db_error_defers_to_reaper(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch 5 finding-2 follow-up: once the disposition commits, the record is durably DISPOSED
    and its purge marker is durable, so a transient DB blip in the post-commit purge phase
    (``blob_owns_object`` / marker-delete / commit) must NOT surface as a 500 for an operation that
    already succeeded — it is rolled back and deferred to the reaper. Mutation-verify: without the
    deferral the injected DB error would propagate out of the approve handler as a 500."""
    a_subject = _subject("dbdefa")
    b_subject = _subject("dbdefb")
    user_a = await _grant(a_subject, _DISPOSITION_PERMS)
    org_id = await _org_id(user_a)
    await _grant(b_subject, _DISPOSITION_PERMS)
    ha = _auth(token_factory, a_subject)
    hb = _auth(token_factory, b_subject)
    pol = await _seed_policy(org_id, action=DispositionAction.DESTROY, review_required=True)
    try:
        sha = await _upload_evidence(app_client, ha, f"dbdef-{uuid.uuid4().hex}".encode())
        rid = (
            await _capture(
                app_client,
                ha,
                record_type="CALIBRATION",
                title="cal",
                retention_policy_id=str(pol),
                evidence=[{"sha256": sha, "content_type": "application/pdf"}],
            )
        ).json()["id"]
        req_id = (
            await app_client.post(
                f"/api/v1/records/{rid}/worm-destroy-requests",
                headers=ha,
                json={"legal_basis": "order"},
            )
        ).json()["id"]

        async def _db_boom(*_a: object, **_k: object) -> None:
            raise SQLAlchemyError("simulated post-commit db blip")

        # The marker-delete (a DB op) fails AFTER the disposition commit + the byte purge; the
        # approve must STILL succeed (deferred to the reaper), never a 500.
        monkeypatch.setattr(disposition.repo, "delete_pending_purge", _db_boom)
        ok = await app_client.post(
            f"/api/v1/records/{rid}/worm-destroy-requests/{req_id}/approve", headers=hb, json={}
        )
        assert ok.status_code == 200, ok.text
        state, _ = await _state(rid)
        assert state == "DISPOSED"
        assert len(await _disposition_events(rid)) == 1
        # The marker survived the rolled-back delete and awaits the reaper.
        async with get_sessionmaker()() as s:
            pending = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert pending == 1
        # Restore the real delete; the reaper drops the marker (bytes were already purged — the
        # re-purge is an idempotent no-op).
        monkeypatch.undo()
        async with get_sessionmaker()() as s:
            await disposition.reap_pending_blob_purges(s)
        async with get_sessionmaker()() as s:
            pending = await s.scalar(
                select(func.count())
                .select_from(PendingBlobPurge)
                .where(PendingBlobPurge.sha256 == sha)
            )
            assert pending == 0
    finally:
        await _cleanup(pol)
