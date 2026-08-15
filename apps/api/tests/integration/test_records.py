"""S-rec-1 integration proofs — records capture + evidence-linking + correction, over HTTP against
testcontainer Postgres + MinIO + Redis.

Records authoring rides on a SYSTEM ``record.create`` override (the seeded role grants don't reach a
folderless/processless record — the ``document.export``/``process.create`` precedent), so each test
grants the keys it needs directly; authz itself is proven in S2. Subjects + evidence bytes are
unique per test so the session containers stay isolated; assertions are existence/delta-scoped to a
test's own record ids (the shared DB accumulates rows across tests).
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models._record_enums import RecordDispositionState, RecordType
from easysynq_api.db.models._retention_enums import DispositionAction, RetentionBasis
from easysynq_api.db.models._vault_enums import DocumentKind
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.clause import Clause
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.evidence_blob import EvidenceBlob
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.retention_policy import RetentionPolicy
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.domain.records.content_hash import record_content_hash
from easysynq_api.services.records import repository as records_repo
from easysynq_api.services.records import service as records_service
from easysynq_api.services.records.listing import RecordListCriteria, RecordListCursor
from easysynq_api.services.vault import storage, upload_rejection
from easysynq_api.services.vault.staged_identity import StagedVersionLocator, StagingDomain

from .test_vault import (
    _auth,
    _ensure_user,
    _grant_doc_perms,
    _object_exists,
    _object_version_exists,
    _sop_type_id,
    _upload,
)

pytestmark = pytest.mark.integration

_RECORD_PERMS = ("record.read", "record.create")


@dataclass(frozen=True, slots=True)
class _EvidenceUpload:
    sha256: str
    version_id: str | None


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant the given permission keys at SYSTEM scope via override (the S2/S9c pattern)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in keys:
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
        return user.id


async def _upload_evidence(
    client: AsyncClient, h: dict[str, str], content: bytes, ct: str = "application/pdf"
) -> _EvidenceUpload:
    sha = hashlib.sha256(content).hexdigest()
    init = await client.post(
        "/api/v1/records:init-upload", headers=h, json={"sha256": sha, "content_type": ct}
    )
    assert init.status_code == 200, init.text
    body = init.json()
    version_id: str | None = None
    if not body["dedup"]:
        async with httpx.AsyncClient(timeout=30) as raw:
            put = await raw.put(body["upload_url"], content=content, headers={"Content-Type": ct})
            assert put.status_code in (200, 204), f"{put.status_code} {put.text}"
            version_id = put.headers["x-amz-version-id"]
    return _EvidenceUpload(sha256=sha, version_id=version_id)


def _evidence_json(
    upload: _EvidenceUpload, content_type: str = "application/pdf"
) -> dict[str, str | None]:
    return {
        "sha256": upload.sha256,
        "content_type": content_type,
        "staging_version_id": upload.version_id,
    }


async def _capture(client: AsyncClient, h: dict[str, str], **body: object) -> httpx.Response:
    return await client.post("/api/v1/records", headers=h, json=body)


async def _assert_correction_refused(
    *, original_id: str, corrected_title: str, actor_id: uuid.UUID
) -> None:
    """The original survives and no successor/audit success escapes a refused correction."""
    async with get_sessionmaker()() as s:
        original_record = await s.get(Record, uuid.UUID(original_id))
        original_base = await s.get(DocumentedInformation, uuid.UUID(original_id))
        assert original_record is not None
        assert original_base is not None
        assert original_record.superseded_by_correction is None
        assert (
            await s.execute(
                select(DocumentedInformation).where(DocumentedInformation.title == corrected_title)
            )
        ).scalar_one_or_none() is None
        correction_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == uuid.UUID(original_id),
                        AuditEvent.event_type == EventType.RECORD_CORRECTED,
                    )
                )
            )
            .scalars()
            .all()
        )
        captured_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_id == actor_id,
                        AuditEvent.event_type == EventType.RECORD_CAPTURED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert correction_events == []
    assert len(captured_events) == 1


async def _first_iso_clause_id() -> str:
    async with get_sessionmaker()() as s:
        return str(
            (
                await s.execute(
                    select(Clause.id)
                    .join(Framework, Clause.framework_id == Framework.id)
                    .where(Framework.code == "iso9001:2015")
                    .order_by(Clause.number)
                    .limit(1)
                )
            ).scalar_one()
        )


# --- capture -----------------------------------------------------------------------------


async def test_capture_read_download_round_trip(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    content = f"evidence-{uuid.uuid4().hex}".encode()
    upload = await _upload_evidence(app_client, h, content)
    sha = upload.sha256

    r = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title="Calibration certificate",
        evidence=[_evidence_json(upload)],
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    rid = rec["id"]
    assert rec["kind"] == "RECORD"
    assert rec["disposition_state"] == "ACTIVE"
    assert rec["retention_policy_id"] is not None
    assert rec["retention_basis_date"] is not None  # captured_at basis → a date
    assert rec["content_hash_version"] == 2
    assert rec["content_hash"] == record_content_hash(
        record_type="EVIDENCE",
        source_version_id=None,
        form_field_values=None,
        evidence_sha256s=[sha],
    )
    assert [b["sha256"] for b in rec["evidence_blobs"]] == [sha]

    # The evidence blob is WORM-locked in the records bucket.
    async with get_sessionmaker()() as s:
        blob = await s.get(Blob, sha)
        assert blob is not None
        assert blob.bucket == "records"
        assert blob.worm_locked is True
        assert blob.worm_retain_until is not None

    # GET round-trips; download presigns the evidence and the bytes match.
    got = await app_client.get(f"/api/v1/records/{rid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["identifier"].startswith("REC-")
    dl = await app_client.get(f"/api/v1/records/{rid}/evidence/{sha}/download", headers=h)
    assert dl.status_code == 200, dl.text
    async with httpx.AsyncClient(timeout=30) as raw:
        fetched = await raw.get(dl.json()["download_url"])
        assert fetched.status_code == 200
        assert fetched.content == content


async def test_record_upload_identity_requires_staging_version_for_new_evidence(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec-upload-version")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    title = f"missing version {uuid.uuid4().hex}"
    upload = await _upload_evidence(app_client, h, title.encode())
    assert upload.version_id is not None

    response = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title=title,
        evidence=[
            {
                "sha256": upload.sha256,
                "content_type": "application/pdf",
                "staging_version_id": None,
            }
        ],
    )

    assert response.status_code == 422
    assert response.json()["code"] == "staging_version_required"
    assert _object_version_exists("staging", upload.sha256, upload.version_id)
    assert not _object_exists("records", upload.sha256)
    async with get_sessionmaker()() as s:
        assert await s.get(Blob, upload.sha256) is None
        assert (
            await s.execute(
                select(DocumentedInformation).where(DocumentedInformation.title == title)
            )
        ).scalar_one_or_none() is None


async def test_record_upload_identity_rejects_same_sha_with_different_versions_before_storage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec-ambiguous-version")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    title = f"ambiguous version {uuid.uuid4().hex}"
    content = title.encode()
    first = await _upload_evidence(app_client, h, content)
    second = await _upload_evidence(app_client, h, content)
    assert first.version_id is not None and second.version_id is not None
    assert first.version_id != second.version_id

    response = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title=title,
        evidence=[_evidence_json(first), _evidence_json(second)],
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.json()["errors"] == [
        {
            "field": "evidence",
            "code": "ambiguous_staging_version",
            "message": "the same evidence sha256 names different staging versions",
        }
    ]
    assert _object_version_exists("staging", first.sha256, first.version_id)
    assert _object_version_exists("staging", second.sha256, second.version_id)
    assert not _object_exists("records", first.sha256)
    async with get_sessionmaker()() as s:
        assert await s.get(Blob, first.sha256) is None
        assert (
            await s.execute(
                select(DocumentedInformation).where(DocumentedInformation.title == title)
            )
        ).scalar_one_or_none() is None


async def test_record_upload_identity_mismatch_rolls_back_audits_exact_cleanup_and_retries(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject("rec-upload-mismatch")
    actor_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    title = f"identity mismatch {uuid.uuid4().hex}"
    honest = f"honest-record-{uuid.uuid4().hex}".encode()
    false = b"x" * len(honest)
    sha = hashlib.sha256(honest).hexdigest()
    init = await app_client.post(
        "/api/v1/records:init-upload",
        headers=h,
        json={"sha256": sha, "content_type": "application/pdf"},
    )
    assert init.status_code == 200, init.text
    async with httpx.AsyncClient(timeout=30) as raw:
        put = await raw.put(
            init.json()["upload_url"],
            content=false,
            headers={"Content-Type": "application/pdf"},
        )
    assert put.status_code in (200, 204), put.text
    bad_version = put.headers["x-amz-version-id"]

    replacement_ref = None
    original_record = upload_rejection.DbUploadRejectionSink.record

    async def _record_then_write_replacement(
        self: upload_rejection.DbUploadRejectionSink,
        context: upload_rejection.RejectionContext,
        failure: upload_rejection.IdentityRefusal | upload_rejection.TargetIdentityConflict,
    ) -> upload_rejection.AuditEventRef:
        nonlocal replacement_ref
        ref = await original_record(self, context, failure)
        replacement_ref = await storage.put_staging_bytes(
            honest, sha, content_type="application/pdf"
        )
        return ref

    monkeypatch.setattr(
        upload_rejection.DbUploadRejectionSink, "record", _record_then_write_replacement
    )
    rejected = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title=title,
        evidence=[
            {
                "sha256": sha,
                "content_type": "application/pdf",
                "staging_version_id": bad_version,
            }
        ],
    )

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "upload_identity_mismatch"
    assert all(
        secret not in rejected.text
        for secret in (hashlib.sha256(false).hexdigest(), bad_version, "staging")
    )
    assert replacement_ref is not None
    assert not _object_version_exists("staging", sha, bad_version)
    assert _object_version_exists("staging", sha, replacement_ref.locator.version_id)
    assert not _object_exists("records", sha)

    async with get_sessionmaker()() as s:
        assert await s.get(Blob, sha) is None
        assert (
            await s.execute(
                select(DocumentedInformation).where(DocumentedInformation.title == title)
            )
        ).scalar_one_or_none() is None
        assert (
            await s.execute(select(EvidenceBlob).where(EvidenceBlob.blob_sha256 == sha))
        ).scalars().all() == []
        identity_events = [
            event
            for event in (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED
                    )
                )
            )
            .scalars()
            .all()
            if event.after is not None and event.after.get("expected", {}).get("sha256") == sha
        ]
        captured_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_id == actor_id,
                        AuditEvent.event_type == EventType.RECORD_CAPTURED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert captured_events == []
    assert len(identity_events) == 1
    rejection = identity_events[0]
    assert (rejection.actor_type, rejection.actor_id, rejection.object_type) == (
        ActorType.user,
        actor_id,
        AuditObjectType.config,
    )
    assert rejection.after == {
        "operation": "record_capture",
        "classification": "digest_mismatch",
        "source": {
            "bucket": "staging",
            "object_key": sha,
            "version_id": bad_version,
            "etag": put.headers.get("etag"),
        },
        "expected": {"sha256": sha, "size_bytes": None},
        "observed": {"sha256": hashlib.sha256(false).hexdigest(), "size_bytes": len(false)},
        "cleanup": {"policy": "delete_exact_version_after_audit"},
    }

    honest_upload = await _upload_evidence(app_client, h, honest)
    accepted = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title=title,
        evidence=[_evidence_json(honest_upload)],
    )
    assert accepted.status_code == 201, accepted.text
    async with get_sessionmaker()() as s:
        records = (
            (await s.execute(select(Record).where(Record.captured_by == actor_id))).scalars().all()
        )
        captured_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_id == actor_id,
                        AuditEvent.event_type == EventType.RECORD_CAPTURED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(records) == 1
    assert len(captured_events) == 1


async def test_record_upload_identity_missing_exact_version_is_conflict_and_retains_newer(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec-missing-exact")
    actor_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    title = f"missing exact {uuid.uuid4().hex}"
    content = title.encode()
    upload = await _upload_evidence(app_client, h, content)
    assert upload.version_id is not None
    replacement = await storage.put_staging_bytes(
        content, upload.sha256, content_type="application/pdf"
    )
    await storage.delete_staged_version(
        StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=upload.sha256,
            version_id=upload.version_id,
        )
    )

    rejected = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title=title,
        evidence=[_evidence_json(upload)],
    )

    assert rejected.status_code == 409
    assert rejected.json()["code"] == "staged_source_unavailable"
    assert _object_version_exists("staging", upload.sha256, replacement.locator.version_id)
    assert not _object_exists("records", upload.sha256)
    async with get_sessionmaker()() as s:
        assert await s.get(Blob, upload.sha256) is None
        assert (
            await s.execute(
                select(DocumentedInformation).where(DocumentedInformation.title == title)
            )
        ).scalar_one_or_none() is None
        events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_id == actor_id,
                        AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1


async def test_record_upload_identity_allows_records_worm_dedup_without_staging_version(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec-worm-dedup")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    content = f"record dedup {uuid.uuid4().hex}".encode()
    first_upload = await _upload_evidence(app_client, h, content)
    first = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title="first dedup source",
        evidence=[_evidence_json(first_upload)],
    )
    assert first.status_code == 201, first.text

    dedup = await _upload_evidence(app_client, h, content)
    assert dedup.version_id is None
    second = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title="second dedup consumer",
        evidence=[_evidence_json(dedup)],
    )

    assert second.status_code == 201, second.text
    assert [item["sha256"] for item in second.json()["evidence_blobs"]] == [dedup.sha256]


async def test_capture_pins_source_version(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant_doc_perms(subject)
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    type_id = await _sop_type_id()
    # A real controlled document + a checked-in version (Draft is fine — R21 pins the version).
    doc = (
        await app_client.post(
            "/api/v1/documents",
            headers=h,
            json={"title": "SOP", "document_type_id": type_id, "area_code": "QA"},
        )
    ).json()
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    upload = await _upload(app_client, h, did, f"doc-{uuid.uuid4().hex}".encode())
    ci = await app_client.post(
        f"/api/v1/documents/{did}/checkin",
        headers=h,
        json={
            "sha256": upload.sha256,
            "staging_version_id": upload.version_id,
            "change_reason": "v1",
            "change_significance": "MAJOR",
        },
    )
    assert ci.status_code == 201, ci.text
    version_id = ci.json()["id"]

    r = await _capture(
        app_client,
        h,
        record_type="RELEASE",
        title="Release record",
        source_document_id=did,
        source_version_id=version_id,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["source_document_id"] == did
    assert rec["source_version_id"] == version_id


async def test_r21_ad_hoc_null_but_under_doc_requires_version(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant_doc_perms(subject)
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    type_id = await _sop_type_id()
    doc = (
        await app_client.post(
            "/api/v1/documents",
            headers=h,
            json={"title": "SOP", "document_type_id": type_id},
        )
    ).json()

    # (a) ad-hoc EVIDENCE — no source, null pin: 201.
    ok = await _capture(app_client, h, record_type="EVIDENCE", title="ad hoc")
    assert ok.status_code == 201, ok.text
    assert ok.json()["source_version_id"] is None

    # (b) declares a source document but omits the version → 422 source_version_required (R21).
    bad = await _capture(
        app_client, h, record_type="RELEASE", title="under doc", source_document_id=doc["id"]
    )
    assert bad.status_code == 422, bad.text
    assert any(e["code"] == "source_version_required" for e in bad.json().get("errors", []))


async def test_all_16_record_types_accepted(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    for rtype in (m.value for m in RecordType):
        r = await _capture(app_client, h, record_type=rtype, title=f"{rtype} record")
        assert r.status_code == 201, f"{rtype}: {r.text}"
        assert r.json()["record_type"] == rtype


# --- deterministic pre-authorization candidates -----------------------------------------


async def test_candidate_order_uses_descending_id_tiebreak_and_strict_boundary(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("candidate-order")
    actor_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    record_ids: list[uuid.UUID] = []
    for sequence in range(3):
        response = await _capture(
            app_client,
            h,
            record_type="EVIDENCE",
            title=f"candidate order {sequence} {uuid.uuid4().hex}",
        )
        assert response.status_code == 201, response.text
        record_ids.append(uuid.UUID(response.json()["id"]))

    captured_at = datetime.datetime(2026, 8, 14, 12, tzinfo=datetime.UTC)
    criteria = RecordListCriteria(captured_by=actor_id)
    expected = sorted(record_ids, reverse=True)
    async with get_sessionmaker()() as session:
        records = list(
            (await session.scalars(select(Record).where(Record.id.in_(record_ids)))).all()
        )
        for record in records:
            record.captured_at = captured_at
        await session.commit()

        rows = await records_repo.list_record_candidates(
            session,
            records[0].org_id,
            criteria=criteria,
            after=None,
            limit=10,
        )
        assert [record.id for record, _base in rows] == expected

        after = RecordListCursor(captured_at=captured_at, record_id=expected[1])
        rows_after = await records_repo.list_record_candidates(
            session,
            records[0].org_id,
            criteria=criteria,
            after=after,
            limit=10,
        )
        assert [record.id for record, _base in rows_after] == expected[2:]


async def test_literal_search_filters_identifier_title_and_all_record_fields(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("candidate-filters")
    actor_id = await _grant(subject, _RECORD_PERMS)
    other_actor_id = await _grant(_subject("candidate-other-actor"), _RECORD_PERMS)
    h = _auth(token_factory, subject)
    marker = uuid.uuid4().hex
    literal = f"50%_done\\-{marker}"
    cases = {
        "target": f"Literal {literal}",
        "identifier_target": f"Identifier-only match {marker}",
        "wildcard_decoy": f"Literal 50AXdone-{marker}",
        "wrong_search": f"Unrelated {marker}",
        "wrong_type": f"Literal {literal}",
        "wrong_source": f"Literal {literal}",
        "wrong_actor": f"Literal {literal}",
        "wrong_disposition": f"Literal {literal}",
        "wrong_hold": f"Literal {literal}",
    }
    record_ids: dict[str, uuid.UUID] = {}
    for name, title in cases.items():
        response = await _capture(
            app_client,
            h,
            record_type="EVIDENCE",
            title=title,
        )
        assert response.status_code == 201, response.text
        record_ids[name] = uuid.UUID(response.json()["id"])

    async with get_sessionmaker()() as session:
        actor = await session.get(AppUser, actor_id)
        assert actor is not None
        framework_id = await session.scalar(
            select(Framework.id).where(Framework.org_id == actor.org_id).limit(1)
        )
        assert framework_id is not None
        source_document = DocumentedInformation(
            org_id=actor.org_id,
            framework_id=framework_id,
            kind=DocumentKind.DOCUMENT,
            identifier=f"CANDIDATE-SOURCE-{marker}",
            title=f"Candidate source {marker}",
            owner_user_id=actor_id,
            created_by=actor_id,
        )
        session.add(source_document)
        await session.flush()
        records = {
            record.id: record
            for record in (
                await session.scalars(select(Record).where(Record.id.in_(record_ids.values())))
            ).all()
        }
        source_document_id = source_document.id
        for record in records.values():
            record.source_document_id = source_document_id
        identifier_base = await session.get(DocumentedInformation, record_ids["identifier_target"])
        assert identifier_base is not None
        identifier_base.identifier = f"LITERAL {literal}"
        records[record_ids["wrong_type"]].record_type = RecordType.CALIBRATION
        records[record_ids["wrong_source"]].source_document_id = None
        records[record_ids["wrong_actor"]].captured_by = other_actor_id
        records[record_ids["wrong_disposition"]].disposition_state = RecordDispositionState.ON_HOLD
        records[record_ids["wrong_hold"]].legal_hold = True
        await session.commit()

        criteria = RecordListCriteria(
            q=f"  LITERAL 50%_DONE\\-{marker.upper()}  ",
            record_type=RecordType.EVIDENCE,
            source_document_id=source_document_id,
            captured_by=actor_id,
            disposition_state=RecordDispositionState.ACTIVE,
            legal_hold=False,
        )
        rows = await records_repo.list_record_candidates(
            session,
            actor.org_id,
            criteria=criteria,
            after=None,
            limit=20,
        )

    assert {record.id for record, _base in rows} == {
        record_ids["target"],
        record_ids["identifier_target"],
    }


async def test_evidence_reuse_of_non_worm_blob_rejected(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # Review fix: a record's evidence must be WORM-sealed in the records bucket. A sha that already
    # resolves to a NON-records-WORM Blob (e.g. a derived renditions blob, worm_locked=False) must
    # NOT be silently attached as sealed evidence via the global-sha dedup branch → 423, no attach.
    subject = _subject("rec")
    user_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    sha = hashlib.sha256(f"rendition-{uuid.uuid4().hex}".encode()).hexdigest()
    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, user_id)
        assert user is not None
        s.add(
            Blob(
                sha256=sha,
                org_id=user.org_id,
                size_bytes=10,
                mime_type="application/pdf",
                bucket="renditions",  # the non-WORM derived bucket
                object_key=sha,
                worm_locked=False,
            )
        )
        await s.commit()
    try:
        r = await _capture(
            app_client,
            h,
            record_type="EVIDENCE",
            title="reuse non-worm",
            evidence=[{"sha256": sha, "content_type": "application/pdf"}],
        )
        assert r.status_code == 423, r.text
        async with get_sessionmaker()() as s:
            blob = await s.get(Blob, sha)
            assert blob is not None and blob.bucket == "renditions" and blob.worm_locked is False
            n = (
                await s.execute(
                    select(func.count())
                    .select_from(EvidenceBlob)
                    .where(EvidenceBlob.blob_sha256 == sha)
                )
            ).scalar_one()
            assert n == 0  # nothing attached — capture rolled back
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Blob).where(Blob.sha256 == sha))
            await s.commit()


async def test_evidence_revalidates_blob_after_insert_conflict(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A records-domain first writer must validate the global-sha conflict winner.

    The first lookup is forced stale to model two transactions that both observed no Blob before a
    documents-domain writer committed. Without the post-insert re-read, capture returns 201 and
    attaches sealed record evidence to the foreign-retention row.
    """
    subject = _subject("rec-blob-race")
    user_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    content = f"record-blob-race-{uuid.uuid4().hex}".encode()
    upload = await _upload_evidence(app_client, h, content)
    sha = upload.sha256

    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, user_id)
        assert user is not None
        s.add(
            Blob(
                sha256=sha,
                org_id=user.org_id,
                size_bytes=len(content),
                mime_type="application/pdf",
                bucket=get_settings().s3_bucket_documents,
                object_key=sha,
                worm_locked=True,
            )
        )
        await s.commit()

    original_get_blob = records_service.vault_repo.get_blob
    target_reads = 0

    async def stale_then_authoritative(
        session: AsyncSession,
        requested_sha: str,
    ) -> Blob | None:
        nonlocal target_reads
        if requested_sha == sha:
            target_reads += 1
            if target_reads == 1:
                return None
        return await original_get_blob(session, requested_sha)

    monkeypatch.setattr(records_service.vault_repo, "get_blob", stale_then_authoritative)
    try:
        response = await _capture(
            app_client,
            h,
            record_type="EVIDENCE",
            title="conflicting evidence placement",
            evidence=[_evidence_json(upload)],
        )
        assert response.status_code == 423, response.text
        assert response.json()["code"] == "worm_required"
        assert target_reads == 2
        async with get_sessionmaker()() as s:
            blob = await s.get(Blob, sha)
            assert blob is not None and blob.bucket == get_settings().s3_bucket_documents
            attached = (
                await s.execute(
                    select(func.count())
                    .select_from(EvidenceBlob)
                    .where(EvidenceBlob.blob_sha256 == sha)
                )
            ).scalar_one()
            assert attached == 0
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(EvidenceBlob).where(EvidenceBlob.blob_sha256 == sha))
            await s.execute(delete(Blob).where(Blob.sha256 == sha))
            await s.commit()


async def test_capture_authz_403_without_record_create(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant(subject, ("record.read",))  # read but NOT create
    h = _auth(token_factory, subject)
    r = await _capture(app_client, h, record_type="EVIDENCE", title="denied")
    assert r.status_code == 403, r.text


async def test_record_captured_audit_in_txn(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    r = await _capture(app_client, h, record_type="COMPETENCE", title="training")
    assert r.status_code == 201, r.text
    rid = uuid.UUID(r.json()["id"])
    async with get_sessionmaker()() as s:
        n = (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == rid,
                    AuditEvent.object_type == AuditObjectType.record,
                    AuditEvent.event_type == EventType.RECORD_CAPTURED,
                )
            )
        ).scalar_one()
        assert n == 1


# --- evidence links ----------------------------------------------------------------------


async def test_evidence_link_map_get_unmap(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    rid = (await _capture(app_client, h, record_type="EVIDENCE", title="link me")).json()["id"]
    clause_id = await _first_iso_clause_id()

    mapped = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=h,
        json={"target_type": "clause", "target_id": clause_id},
    )
    assert mapped.status_code == 201, mapped.text
    link_id = mapped.json()["id"]
    assert mapped.json()["target_type"] == "clause"

    listed = await app_client.get(f"/api/v1/records/{rid}/evidence-links", headers=h)
    assert listed.status_code == 200
    assert [link["id"] for link in listed.json()] == [link_id]

    # Dup → 409.
    dup = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=h,
        json={"target_type": "clause", "target_id": clause_id},
    )
    assert dup.status_code == 409, dup.text

    unmapped = await app_client.delete(f"/api/v1/records/{rid}/evidence-links/{link_id}", headers=h)
    assert unmapped.status_code == 204
    assert (await app_client.get(f"/api/v1/records/{rid}/evidence-links", headers=h)).json() == []


async def test_evidence_link_cross_framework_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    user_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    rid = (await _capture(app_client, h, record_type="EVIDENCE", title="x-fw")).json()["id"]

    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, user_id)
        assert user is not None
        fw = Framework(org_id=user.org_id, code=f"test:foreign-{salt}", name="Foreign")
        s.add(fw)
        await s.flush()
        clause = Clause(
            framework_id=fw.id,
            number="X.1",
            title="Foreign clause",
            intent_text="x",
            pdca_phase=PdcaPhase.PLAN,
        )
        s.add(clause)
        await s.commit()
        foreign_clause_id, foreign_fw_id = str(clause.id), fw.id
    try:
        r = await app_client.post(
            f"/api/v1/records/{rid}/evidence-links",
            headers=h,
            json={"target_type": "clause", "target_id": foreign_clause_id},
        )
        assert r.status_code == 422, r.text
        assert any(e["code"] == "framework_mismatch" for e in r.json().get("errors", []))
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Clause).where(Clause.id == uuid.UUID(foreign_clause_id)))
            await s.execute(delete(Framework).where(Framework.id == foreign_fw_id))
            await s.commit()


async def test_evidence_link_authz_403(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # The capturer can link; a record.read-only subject cannot.
    owner = _subject("rec")
    await _grant(owner, _RECORD_PERMS)
    h_owner = _auth(token_factory, owner)
    rid = (await _capture(app_client, h_owner, record_type="EVIDENCE", title="guarded")).json()[
        "id"
    ]
    clause_id = await _first_iso_clause_id()

    reader = _subject("reader")
    await _grant(reader, ("record.read",))
    h_reader = _auth(token_factory, reader)
    r = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=h_reader,
        json={"target_type": "clause", "target_id": clause_id},
    )
    assert r.status_code == 403, r.text


# --- correction --------------------------------------------------------------------------


async def test_correction_creates_new_flags_old(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    r1 = (await _capture(app_client, h, record_type="CALIBRATION", title="orig")).json()
    r1_id = r1["id"]

    corr = await app_client.post(
        f"/api/v1/records/{r1_id}/correction",
        headers=h,
        json={"record_type": "CALIBRATION", "title": "corrected"},
    )
    assert corr.status_code == 201, corr.text
    r2 = corr.json()
    assert r2["correction_of"] == r1_id
    assert r2["id"] != r1_id

    # The original now points to its successor and is still retrievable.
    again = await app_client.get(f"/api/v1/records/{r1_id}", headers=h)
    assert again.status_code == 200
    assert again.json()["superseded_by_correction"] == r2["id"]

    # The RECORD_CORRECTED audit row exists on the original.
    async with get_sessionmaker()() as s:
        n = (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == uuid.UUID(r1_id),
                    AuditEvent.event_type == EventType.RECORD_CORRECTED,
                )
            )
        ).scalar_one()
        assert n == 1


async def test_record_correction_upload_identity_mismatch_preserves_pointer_and_retries_once(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    subject = _subject("rec-correction-mismatch")
    actor_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    original = await _capture(
        app_client, h, record_type="CALIBRATION", title=f"original {uuid.uuid4().hex}"
    )
    assert original.status_code == 201, original.text
    original_id = original.json()["id"]
    corrected_title = f"corrected {uuid.uuid4().hex}"
    honest = f"honest-correction-{uuid.uuid4().hex}".encode()
    false = b"x" * len(honest)
    sha = hashlib.sha256(honest).hexdigest()
    init = await app_client.post(
        "/api/v1/records:init-upload",
        headers=h,
        json={"sha256": sha, "content_type": "application/pdf"},
    )
    assert init.status_code == 200, init.text
    async with httpx.AsyncClient(timeout=30) as raw:
        put = await raw.put(
            init.json()["upload_url"],
            content=false,
            headers={"Content-Type": "application/pdf"},
        )
    assert put.status_code in (200, 204), put.text
    bad_version = put.headers["x-amz-version-id"]

    rejected = await app_client.post(
        f"/api/v1/records/{original_id}/correction",
        headers=h,
        json={
            "record_type": "CALIBRATION",
            "title": corrected_title,
            "evidence": [
                {
                    "sha256": sha,
                    "content_type": "application/pdf",
                    "staging_version_id": bad_version,
                }
            ],
        },
    )

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "upload_identity_mismatch"
    assert not _object_version_exists("staging", sha, bad_version)
    assert not _object_exists("records", sha)
    async with get_sessionmaker()() as s:
        original_row = await s.get(Record, uuid.UUID(original_id))
        assert original_row is not None
        assert original_row.superseded_by_correction is None
        assert (
            await s.execute(
                select(DocumentedInformation).where(DocumentedInformation.title == corrected_title)
            )
        ).scalar_one_or_none() is None
        assert await s.get(Blob, sha) is None
        assert (
            await s.execute(select(EvidenceBlob).where(EvidenceBlob.blob_sha256 == sha))
        ).scalars().all() == []
        rejection_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_id == actor_id,
                        AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
        correction_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == uuid.UUID(original_id),
                        AuditEvent.event_type == EventType.RECORD_CORRECTED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rejection_events) == 1
    assert correction_events == []

    retry_upload = await _upload_evidence(app_client, h, honest)
    accepted = await app_client.post(
        f"/api/v1/records/{original_id}/correction",
        headers=h,
        json={
            "record_type": "CALIBRATION",
            "title": corrected_title,
            "evidence": [_evidence_json(retry_upload)],
        },
    )
    assert accepted.status_code == 201, accepted.text
    async with get_sessionmaker()() as s:
        original_row = await s.get(Record, uuid.UUID(original_id))
        assert original_row is not None
        assert str(original_row.superseded_by_correction) == accepted.json()["id"]
        correction_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == uuid.UUID(original_id),
                        AuditEvent.event_type == EventType.RECORD_CORRECTED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(correction_events) == 1


async def test_record_correction_upload_identity_requires_version_and_preserves_original(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec-correction-version")
    actor_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    original = await _capture(
        app_client, h, record_type="CALIBRATION", title=f"original {uuid.uuid4().hex}"
    )
    assert original.status_code == 201, original.text
    original_id = original.json()["id"]
    corrected_title = f"missing-version correction {uuid.uuid4().hex}"
    upload = await _upload_evidence(app_client, h, corrected_title.encode())
    assert upload.version_id is not None

    rejected = await app_client.post(
        f"/api/v1/records/{original_id}/correction",
        headers=h,
        json={
            "record_type": "CALIBRATION",
            "title": corrected_title,
            "evidence": [
                {
                    "sha256": upload.sha256,
                    "content_type": "application/pdf",
                    "staging_version_id": None,
                }
            ],
        },
    )

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "staging_version_required"
    assert _object_version_exists("staging", upload.sha256, upload.version_id)
    assert not _object_exists("records", upload.sha256)
    await _assert_correction_refused(
        original_id=original_id, corrected_title=corrected_title, actor_id=actor_id
    )
    async with get_sessionmaker()() as s:
        assert await s.get(Blob, upload.sha256) is None
        assert (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.actor_id == actor_id,
                    AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED,
                )
            )
        ).scalars().all() == []


async def test_record_correction_upload_identity_rejects_ambiguous_versions_before_storage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec-correction-ambiguous")
    actor_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    original = await _capture(
        app_client, h, record_type="CALIBRATION", title=f"original {uuid.uuid4().hex}"
    )
    assert original.status_code == 201, original.text
    original_id = original.json()["id"]
    corrected_title = f"ambiguous correction {uuid.uuid4().hex}"
    content = corrected_title.encode()
    first = await _upload_evidence(app_client, h, content)
    second = await _upload_evidence(app_client, h, content)
    assert first.version_id is not None and second.version_id is not None
    assert first.version_id != second.version_id

    rejected = await app_client.post(
        f"/api/v1/records/{original_id}/correction",
        headers=h,
        json={
            "record_type": "CALIBRATION",
            "title": corrected_title,
            "evidence": [_evidence_json(first), _evidence_json(second)],
        },
    )

    assert rejected.status_code == 422
    assert rejected.json()["code"] == "validation_error"
    assert rejected.json()["errors"] == [
        {
            "field": "evidence",
            "code": "ambiguous_staging_version",
            "message": "the same evidence sha256 names different staging versions",
        }
    ]
    assert _object_version_exists("staging", first.sha256, first.version_id)
    assert _object_version_exists("staging", second.sha256, second.version_id)
    assert not _object_exists("records", first.sha256)
    await _assert_correction_refused(
        original_id=original_id, corrected_title=corrected_title, actor_id=actor_id
    )
    async with get_sessionmaker()() as s:
        assert await s.get(Blob, first.sha256) is None
        assert (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.actor_id == actor_id,
                    AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED,
                )
            )
        ).scalars().all() == []


async def test_record_correction_upload_identity_missing_exact_version_retains_newer(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec-correction-missing-exact")
    actor_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    original = await _capture(
        app_client, h, record_type="CALIBRATION", title=f"original {uuid.uuid4().hex}"
    )
    assert original.status_code == 201, original.text
    original_id = original.json()["id"]
    corrected_title = f"missing exact correction {uuid.uuid4().hex}"
    content = corrected_title.encode()
    upload = await _upload_evidence(app_client, h, content)
    assert upload.version_id is not None
    replacement = await storage.put_staging_bytes(
        content, upload.sha256, content_type="application/pdf"
    )
    await storage.delete_staged_version(
        StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=upload.sha256,
            version_id=upload.version_id,
        )
    )

    rejected = await app_client.post(
        f"/api/v1/records/{original_id}/correction",
        headers=h,
        json={
            "record_type": "CALIBRATION",
            "title": corrected_title,
            "evidence": [_evidence_json(upload)],
        },
    )

    assert rejected.status_code == 409
    assert rejected.json()["code"] == "staged_source_unavailable"
    assert not _object_version_exists("staging", upload.sha256, upload.version_id)
    assert _object_version_exists("staging", upload.sha256, replacement.locator.version_id)
    assert not _object_exists("records", upload.sha256)
    await _assert_correction_refused(
        original_id=original_id, corrected_title=corrected_title, actor_id=actor_id
    )
    async with get_sessionmaker()() as s:
        assert await s.get(Blob, upload.sha256) is None
        rejection_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_id == actor_id,
                        AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rejection_events) == 1
    rejection = rejection_events[0]
    assert rejection.after is not None
    assert rejection.after["operation"] == "record_capture"
    assert rejection.after["classification"] == "source_missing"
    assert rejection.after["source"] == {
        "bucket": "staging",
        "object_key": upload.sha256,
        "version_id": upload.version_id,
        "etag": None,
    }


async def test_record_correction_upload_identity_allows_records_worm_dedup_without_version(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec-correction-dedup")
    actor_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    content = f"correction dedup {uuid.uuid4().hex}".encode()
    upload = await _upload_evidence(app_client, h, content)
    original = await _capture(
        app_client,
        h,
        record_type="CALIBRATION",
        title=f"original {uuid.uuid4().hex}",
        evidence=[_evidence_json(upload)],
    )
    assert original.status_code == 201, original.text
    original_id = original.json()["id"]
    corrected_title = f"dedup correction {uuid.uuid4().hex}"
    dedup = await _upload_evidence(app_client, h, content)
    assert dedup.version_id is None

    accepted = await app_client.post(
        f"/api/v1/records/{original_id}/correction",
        headers=h,
        json={
            "record_type": "CALIBRATION",
            "title": corrected_title,
            "evidence": [_evidence_json(dedup)],
        },
    )

    assert accepted.status_code == 201, accepted.text
    successor_id = accepted.json()["id"]
    assert [item["sha256"] for item in accepted.json()["evidence_blobs"]] == [dedup.sha256]
    assert _object_exists("records", dedup.sha256)
    async with get_sessionmaker()() as s:
        original_record = await s.get(Record, uuid.UUID(original_id))
        original_base = await s.get(DocumentedInformation, uuid.UUID(original_id))
        successor = await s.get(Record, uuid.UUID(successor_id))
        assert original_record is not None
        assert original_base is not None
        assert successor is not None
        assert str(original_record.superseded_by_correction) == successor_id
        successors = (
            (
                await s.execute(
                    select(DocumentedInformation).where(
                        DocumentedInformation.title == corrected_title
                    )
                )
            )
            .scalars()
            .all()
        )
        attachments = (
            (
                await s.execute(
                    select(EvidenceBlob).where(
                        EvidenceBlob.record_id == uuid.UUID(successor_id),
                        EvidenceBlob.blob_sha256 == dedup.sha256,
                    )
                )
            )
            .scalars()
            .all()
        )
        correction_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == uuid.UUID(original_id),
                        AuditEvent.event_type == EventType.RECORD_CORRECTED,
                    )
                )
            )
            .scalars()
            .all()
        )
        captured_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_id == actor_id,
                        AuditEvent.event_type == EventType.RECORD_CAPTURED,
                    )
                )
            )
            .scalars()
            .all()
        )
        rejection_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_id == actor_id,
                        AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(successors) == 1
    assert len(attachments) == 1
    assert len(correction_events) == 1
    assert len(captured_events) == 2
    assert rejection_events == []


async def test_correction_of_already_superseded_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    r1_id = (await _capture(app_client, h, record_type="CALIBRATION", title="orig")).json()["id"]
    first = await app_client.post(
        f"/api/v1/records/{r1_id}/correction",
        headers=h,
        json={"record_type": "CALIBRATION", "title": "c1"},
    )
    assert first.status_code == 201, first.text
    second = await app_client.post(
        f"/api/v1/records/{r1_id}/correction",
        headers=h,
        json={"record_type": "CALIBRATION", "title": "c2"},
    )
    assert second.status_code == 409, second.text


# --- retention resolution ----------------------------------------------------------------


async def test_retention_resolution_tiers(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    user_id = await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    salt = uuid.uuid4().hex[:8]

    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, user_id)
        assert user is not None
        rt_policy = RetentionPolicy(
            org_id=user.org_id,
            name=f"RT-CAL-{salt}",
            applies_to={"record_type": "CALIBRATION"},
            basis=RetentionBasis.CAPTURED_AT,
            duration="P3Y",
            disposition_action=DispositionAction.DESTROY,
            review_required=False,
        )
        s.add(rt_policy)
        # The always-present system default (ensure-created on first capture; seed it here too).
        await s.commit()
        rt_policy_id = str(rt_policy.id)
    try:
        # record-type tier: a CALIBRATION capture resolves to the applies_to policy.
        cal = (await _capture(app_client, h, record_type="CALIBRATION", title="cal")).json()
        assert cal["retention_policy_id"] == rt_policy_id

        # fallback tier: a type with no applies_to policy → the system default (not rt_policy).
        ev = (await _capture(app_client, h, record_type="EVIDENCE", title="ev")).json()
        assert ev["retention_policy_id"] != rt_policy_id
        system_default_id = ev["retention_policy_id"]

        # override tier: an explicit retention_policy_id beats the matching record-type default.
        over = (
            await _capture(
                app_client,
                h,
                record_type="CALIBRATION",
                title="cal-override",
                retention_policy_id=system_default_id,
            )
        ).json()
        assert over["retention_policy_id"] == system_default_id
    finally:
        # Captured records PIN the policy (the FK ratchet — that is the point), so the records this
        # test created must be removed before the seeded policy can be dropped. Their capture audit
        # rows stay (append-only; object_id has no FK) — harmless orphans.
        async with get_sessionmaker()() as s:
            pinned = list(
                (
                    await s.execute(
                        select(Record.id).where(
                            Record.retention_policy_id == uuid.UUID(rt_policy_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            if pinned:
                await s.execute(delete(Record).where(Record.id.in_(pinned)))
                await s.execute(
                    delete(DocumentedInformation).where(DocumentedInformation.id.in_(pinned))
                )
            await s.execute(
                delete(RetentionPolicy).where(RetentionPolicy.id == uuid.UUID(rt_policy_id))
            )
            await s.commit()


# --- kind-scoping: records never leak into the documents / search surfaces ----------------


async def test_records_absent_from_documents_and_search(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("rec")
    await _grant_doc_perms(subject)  # document.read so the list/search aren't empty by authz
    await _grant(subject, _RECORD_PERMS)
    h = _auth(token_factory, subject)
    token = f"zzqcp{uuid.uuid4().hex[:10]}"
    rid = (await _capture(app_client, h, record_type="EVIDENCE", title=f"{token} record")).json()[
        "id"
    ]

    # GET /documents excludes Records (kind-scoping), even though they are Effective.
    docs = await app_client.get("/api/v1/documents?limit=100", headers=h)
    assert docs.status_code == 200
    assert rid not in {d["id"] for d in docs.json()["data"]}

    # /search is Effective-DOCUMENTS only — the record's unique title token must not surface it.
    search = await app_client.get(f"/api/v1/search?q={token}", headers=h)
    assert search.status_code == 200
    hit_ids = {hit["id"] for hit in search.json().get("results", [])}
    assert rid not in hit_ids
