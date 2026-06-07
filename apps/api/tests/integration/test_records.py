"""S-rec-1 integration proofs — records capture + evidence-linking + correction, over HTTP against
testcontainer Postgres + MinIO + Redis.

Records authoring rides on a SYSTEM ``record.create`` override (the seeded role grants don't reach a
folderless/processless record — the ``document.export``/``process.create`` precedent), so each test
grants the keys it needs directly; authz itself is proven in S2. Subjects + evidence bytes are
unique per test so the session containers stay isolated; assertions are existence/delta-scoped to a
test's own record ids (the shared DB accumulates rows across tests).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models._record_enums import RecordType
from easysynq_api.db.models._retention_enums import DispositionAction, RetentionBasis
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

from .test_vault import _auth, _ensure_user, _grant_doc_perms, _sop_type_id, _upload

pytestmark = pytest.mark.integration

_RECORD_PERMS = ("record.read", "record.create")


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
) -> str:
    sha = hashlib.sha256(content).hexdigest()
    init = await client.post(
        "/api/v1/records:init-upload", headers=h, json={"sha256": sha, "content_type": ct}
    )
    assert init.status_code == 200, init.text
    body = init.json()
    if not body["dedup"]:
        async with httpx.AsyncClient(timeout=30) as raw:
            put = await raw.put(body["upload_url"], content=content, headers={"Content-Type": ct})
            assert put.status_code in (200, 204), f"{put.status_code} {put.text}"
    return sha


async def _capture(client: AsyncClient, h: dict[str, str], **body: object) -> httpx.Response:
    return await client.post("/api/v1/records", headers=h, json=body)


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
    sha = await _upload_evidence(app_client, h, content)

    r = await _capture(
        app_client,
        h,
        record_type="EVIDENCE",
        title="Calibration certificate",
        evidence=[{"sha256": sha, "content_type": "application/pdf"}],
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    rid = rec["id"]
    assert rec["kind"] == "RECORD"
    assert rec["disposition_state"] == "ACTIVE"
    assert rec["retention_policy_id"] is not None
    assert rec["retention_basis_date"] is not None  # captured_at basis → a date
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
    sha = await _upload(app_client, h, did, f"doc-{uuid.uuid4().hex}".encode())
    ci = await app_client.post(
        f"/api/v1/documents/{did}/checkin",
        headers=h,
        json={"sha256": sha, "change_reason": "v1", "change_significance": "MAJOR"},
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
