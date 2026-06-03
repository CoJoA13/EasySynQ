"""S3 integration proofs — the vault check-out → CAS upload → immutable check-in cycle,
exercised over HTTP against testcontainer Postgres + MinIO + Redis.

The test actor is granted the ``document.*`` permissions at SYSTEM scope directly (authz is
already proven in S2); here the variable under test is the vault mechanics. Subjects + content
are unique per test so the session-scoped containers stay isolated.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.clause import Clause
from easysynq_api.db.models.document_type import DocumentType
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.working_draft import WorkingDraft
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.vault import locks

pytestmark = pytest.mark.integration

_DOC_PERMS = (
    "document.read",
    "document.read_draft",
    "document.create",
    "document.checkout",
    "document.edit",
    "document.manage_metadata",
)


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-other-{salt}")


def _auth(token_factory: Callable[..., str], subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_factory(subject)}"}


async def _ensure_user(session: object, subject: str) -> AppUser:
    s = session  # AsyncSession
    user = (
        await s.execute(select(AppUser).where(AppUser.keycloak_subject == subject))
    ).scalar_one_or_none()
    if user is None:
        org_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()
        user = AppUser(
            org_id=org_id, keycloak_subject=subject, display_name=subject, status=UserStatus.ACTIVE
        )
        s.add(user)
        await s.flush()
    return user


async def _grant_doc_perms(subject: str) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in _DOC_PERMS:
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


async def _sop_type_id() -> str:
    async with get_sessionmaker()() as s:
        return str(
            (await s.execute(select(DocumentType).where(DocumentType.code == "SOP")))
            .scalar_one()
            .id
        )


async def _create(client: AsyncClient, h: dict[str, str], type_id: str, area: str = "PUR") -> dict:
    r = await client.post(
        "/api/v1/documents",
        headers=h,
        json={"title": "Test Doc", "document_type_id": type_id, "area_code": area},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _first_clause_id() -> str:
    """An ``iso9001:2015`` clause id — scoped to that framework so a foreign-framework test clause
    (test_clauses' cross-framework case) can't be picked and trip the framework-match guard."""
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


async def _map_clause(client: AsyncClient, h: dict[str, str], doc_id: str) -> str:
    """Map a document to one ISO clause (satisfies the S9 submit-review >=1-clause_mapping gate).
    Requires the caller to hold ``document.manage_metadata`` (in both _DOC_PERMS + LIFECYCLE)."""
    clause_id = await _first_clause_id()
    r = await client.post(
        f"/api/v1/documents/{doc_id}/clause-mappings", headers=h, json={"clause_id": clause_id}
    )
    assert r.status_code == 201, r.text
    return clause_id


async def _upload(
    client: AsyncClient, h: dict[str, str], doc_id: str, content: bytes, ct: str = "application/pdf"
) -> str:
    sha = hashlib.sha256(content).hexdigest()
    init = await client.post(
        f"/api/v1/documents/{doc_id}/versions:init-upload",
        headers=h,
        json={"sha256": sha, "content_type": ct},
    )
    assert init.status_code == 200, init.text
    body = init.json()
    if not body["dedup"]:
        async with httpx.AsyncClient(timeout=30) as raw:
            put = await raw.put(body["upload_url"], content=content, headers={"Content-Type": ct})
            assert put.status_code in (200, 204), f"{put.status_code} {put.text}"
    return sha


async def _checkin(
    client: AsyncClient, h: dict[str, str], doc_id: str, sha: str, **kw
) -> httpx.Response:
    payload = {"sha256": sha, "change_reason": "edit", "change_significance": "MINOR", **kw}
    return await client.post(f"/api/v1/documents/{doc_id}/checkin", headers=h, json=payload)


# --- identifier allocation --------------------------------------------------------------


async def test_create_allocates_identifier_atomically(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_doc_perms(subj.a)
    h = _auth(token_factory, subj.a)
    type_id = await _sop_type_id()
    area = f"A{uuid.uuid4().hex[:5].upper()}"  # fresh area → sequence starts at 1
    d1 = await _create(app_client, h, type_id, area)
    d2 = await _create(app_client, h, type_id, area)
    assert d1["identifier"] == f"SOP-{area}-001"
    assert d2["identifier"] == f"SOP-{area}-002"
    assert "Rev" not in d1["identifier"]  # revision is never part of the identifier


# --- check-out lock ---------------------------------------------------------------------


async def test_double_checkout_409_lock_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_doc_perms(subj.a)
    await _grant_doc_perms(subj.b)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await _create(app_client, ha, await _sop_type_id())
    did = doc["id"]

    first = await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    assert first.status_code == 200, first.text
    assert first.json()["lock_ttl_seconds"] == 28800

    second = await app_client.post(f"/api/v1/documents/{did}/checkout", headers=hb)
    assert second.status_code == 409
    assert second.json()["code"] == "lock_conflict"


async def test_checkout_lock_ttl_is_8h(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_doc_perms(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _create(app_client, h, await _sop_type_id())
    await app_client.post(f"/api/v1/documents/{doc['id']}/checkout", headers=h)
    remaining = await locks.ttl(uuid.UUID(doc["id"]))
    assert 28000 < remaining <= 28800  # ~8h (R24)


# --- INV-3 ------------------------------------------------------------------------------


async def test_checkin_requires_reason_and_significance(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_doc_perms(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _create(app_client, h, await _sop_type_id())
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha = await _upload(app_client, h, did, f"inv3-{subj.a}".encode())

    blank_reason = await _checkin(app_client, h, did, sha, change_reason="   ")
    assert blank_reason.status_code == 422
    assert blank_reason.json()["code"] == "validation_error"

    bad_sig = await _checkin(app_client, h, did, sha, change_significance="HUGE")
    assert bad_sig.status_code == 422

    ok = await _checkin(
        app_client, h, did, sha, change_reason="real reason", change_significance="MAJOR"
    )
    assert ok.status_code == 201, ok.text


# --- content-addressed dedup ------------------------------------------------------------


async def test_recheckin_identical_bytes_no_new_version(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_doc_perms(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _create(app_client, h, await _sop_type_id())
    did = doc["id"]
    content = f"dedup-{subj.a}".encode()

    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha = await _upload(app_client, h, did, content)
    first = await _checkin(
        app_client, h, did, sha, change_reason="initial", change_significance="MAJOR"
    )
    assert first.status_code == 201, first.text
    assert first.json()["change_detected"] is True
    assert first.json()["version_seq"] == 1

    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha2 = await _upload(app_client, h, did, content)  # identical bytes → init-upload dedups
    again = await _checkin(
        app_client, h, did, sha2, change_reason="no real change", change_significance="MINOR"
    )
    assert again.status_code == 201, again.text
    assert again.json()["change_detected"] is False  # "no change detected"

    versions = (await app_client.get(f"/api/v1/documents/{did}/versions", headers=h)).json()
    assert len(versions) == 1  # no new version was created


# --- break-lock preserves scratch (R9) --------------------------------------------------


async def test_break_lock_preserves_scratch_and_audits(
    app_client: AsyncClient,
    app_under_test: object,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    from easysynq_api.services.vault import CapturingVaultAuditSink, get_vault_audit_sink

    sink = CapturingVaultAuditSink()
    app_under_test.dependency_overrides[get_vault_audit_sink] = lambda: sink  # type: ignore[attr-defined]

    await _grant_doc_perms(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _create(app_client, h, await _sop_type_id())
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    await _upload(app_client, h, did, f"scratch-{subj.a}".encode())  # records scratch_blob_ref

    broken = await app_client.post(f"/api/v1/documents/{did}/break-lock", headers=h)
    assert broken.status_code == 200, broken.text

    async with get_sessionmaker()() as s:
        wd = (
            await s.execute(select(WorkingDraft).where(WorkingDraft.document_id == uuid.UUID(did)))
        ).scalar_one_or_none()
    assert wd is not None  # the working draft (and its scratch) is NOT deleted (R9)
    assert wd.scratch_blob_ref is not None
    assert any(e.event_type == "LOCK_BROKEN" for e in sink.events)

    # the lock is released, so a fresh check-out succeeds
    again = await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    assert again.status_code == 200


# --- WORM + presigned I/O ---------------------------------------------------------------


async def test_blob_worm_locked_before_version(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_doc_perms(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _create(app_client, h, await _sop_type_id())
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha = await _upload(app_client, h, did, f"worm-{subj.a}".encode())
    ci = await _checkin(app_client, h, did, sha, change_reason="r", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text

    async with get_sessionmaker()() as s:
        blob = (await s.execute(select(Blob).where(Blob.sha256 == sha))).scalar_one()
    assert blob.worm_locked is True
    assert blob.worm_retain_until is not None  # WORM applied before the version committed
    assert blob.bucket == "documents"


async def test_content_io_is_presigned_never_proxied(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_doc_perms(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _create(app_client, h, await _sop_type_id())
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)

    sha = hashlib.sha256(f"presign-{subj.a}".encode()).hexdigest()
    init = await app_client.post(
        f"/api/v1/documents/{did}/versions:init-upload",
        headers=h,
        json={"sha256": sha, "content_type": "application/pdf"},
    )
    upload_url = init.json()["upload_url"]
    assert upload_url.startswith("http") and "/api/v1/" not in upload_url  # points at MinIO

    await _upload(app_client, h, did, f"presign-{subj.a}".encode())
    ci = await _checkin(app_client, h, did, sha, change_reason="r", change_significance="MAJOR")
    vid = ci.json()["id"]
    dl = await app_client.get(f"/api/v1/documents/{did}/versions/{vid}/download", headers=h)
    assert dl.status_code == 200, dl.text
    assert dl.json()["download_url"].startswith("http")
    assert "/api/v1/" not in dl.json()["download_url"]


async def test_checkout_heartbeat_refreshes_lock(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_doc_perms(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _create(app_client, h, await _sop_type_id())
    await app_client.post(f"/api/v1/documents/{doc['id']}/checkout", headers=h)

    beat = await app_client.post(f"/api/v1/documents/{doc['id']}/heartbeat", headers=h)
    assert beat.status_code == 200, beat.text
    assert 28000 < beat.json()["lock_ttl_seconds"] <= 28800  # TTL refreshed to ~8h (R24)

    # heartbeat on a document the caller has not checked out -> 409
    other = await _create(app_client, h, await _sop_type_id())
    miss = await app_client.post(f"/api/v1/documents/{other['id']}/heartbeat", headers=h)
    assert miss.status_code == 409
    assert miss.json()["code"] == "lock_conflict"
