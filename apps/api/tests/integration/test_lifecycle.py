"""S4 integration proofs — the document lifecycle FSM + the atomic single-Effective cutover,
exercised over HTTP against testcontainer Postgres + MinIO + Redis.

The two headline proofs are AC#1a (``test_release_supersedes`` — a release atomically supersedes the
prior Effective version) and AC#1b (``test_two_effective_impossible`` — two parallel releases under
real concurrent connections yield exactly one Effective; the loser rolls back to 409). Vault
mechanics (check-out/upload/check-in) are reused from S3; here the variable under test is lifecycle.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.document_type import DocumentType
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from .test_vault import _auth, _checkin, _create, _ensure_user, _sop_type_id, _upload

pytestmark = pytest.mark.integration

# The full set a lifecycle actor needs (S3 vault perms + the S4 lifecycle keys, doc 07 §3.1).
_PERMS = (
    "document.read",
    "document.read_draft",
    "document.create",
    "document.checkout",
    "document.edit",
    "document.manage_metadata",
    "document.submit",
    "document.review",
    "document.approve",
    "document.release",
    "document.obsolete",
)


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-other-{salt}")


async def _grant(subject: str) -> uuid.UUID:
    """Grant the actor every lifecycle permission at SYSTEM scope (authz is proven in S2)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in _PERMS:
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


async def _effective_count(doc_id: str) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(DocumentVersion)
                .where(
                    DocumentVersion.document_id == uuid.UUID(doc_id),
                    DocumentVersion.version_state == VersionState.Effective,
                )
            )
        ).scalar_one()


async def _pol_type_id() -> str:
    """The seeded singleton Quality Policy (POL) type — ``is_singleton=true`` (R25)."""
    async with get_sessionmaker()() as s:
        return str(
            (await s.execute(select(DocumentType).where(DocumentType.code == "POL")))
            .scalar_one()
            .id
        )


async def _version(version_id: str) -> DocumentVersion:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(version_id))
            )
        ).scalar_one()


async def _make_effective(
    client: AsyncClient, h: dict[str, str], type_id: str, content: bytes
) -> dict:
    """Drive a fresh document Draft → InReview → Approved → Effective over HTTP."""
    doc = await _create(client, h, type_id)
    did = doc["id"]
    await client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha = await _upload(client, h, did, content)
    ci = await _checkin(client, h, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    assert (
        await client.post(f"/api/v1/documents/{did}/submit-review", headers=h)
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/documents/{did}/approve", headers=h, json={})
    ).status_code == 200
    rel = await client.post(f"/api/v1/documents/{did}/release", headers=h, json={})
    assert rel.status_code == 200, rel.text
    return rel.json()


# --- AC#1a -------------------------------------------------------------------------------


async def test_release_supersedes(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[AC#1a] Draft→InReview→Approved→Effective, then a revision's release atomically supersedes
    the prior Effective version. (signature_event accuracy is an S5 proof; S4 proves the FSM +
    atomic supersession.)"""
    await _grant(subj.a)
    h = _auth(token_factory, subj.a)
    type_id = await _sop_type_id()

    doc = await _make_effective(app_client, h, type_id, f"ac1a-v1-{subj.a}".encode())
    did = doc["id"]
    assert doc["current_state"] == "Effective"
    v1_id = doc["current_effective_version_id"]
    assert v1_id is not None

    # Open a revision, check in v2, and run it through to release.
    sr = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=h)
    assert sr.status_code == 200, sr.text
    assert sr.json()["current_state"] == "UnderRevision"
    sha2 = await _upload(app_client, h, did, f"ac1a-v2-{subj.a}".encode())
    ci2 = await _checkin(app_client, h, did, sha2, change_reason="v2", change_significance="MINOR")
    v2_id = ci2.json()["id"]
    await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=h)
    await app_client.post(f"/api/v1/documents/{did}/approve", headers=h, json={})
    rel2 = await app_client.post(f"/api/v1/documents/{did}/release", headers=h, json={})
    assert rel2.status_code == 200, rel2.text
    after = rel2.json()
    assert after["current_state"] == "Effective"
    assert after["current_effective_version_id"] == v2_id

    v1, v2 = await _version(v1_id), await _version(v2_id)
    assert v1.version_state is VersionState.Superseded
    assert v1.effective_to is not None
    assert v1.superseded_by_version_id == uuid.UUID(v2_id)
    assert v2.version_state is VersionState.Effective
    assert v2.effective_to is None
    assert await _effective_count(did) == 1


# --- AC#1b -------------------------------------------------------------------------------


async def test_two_effective_impossible(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[AC#1b] Two parallel releases targeting two distinct Approved versions of one document →
    exactly one Effective. The loser rolls back (serialization failure 40001 or INV-1 unique
    violation 23505) and surfaces as 409; the FOR UPDATE row lock keeps it deadlock-free."""
    await _grant(subj.a)
    h = _auth(token_factory, subj.a)
    did = (await _create(app_client, h, await _sop_type_id()))["id"]

    # Two checked-in Draft versions on the one document.
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha1 = await _upload(app_client, h, did, f"ac1b-v1-{subj.a}".encode())
    v1 = (
        await _checkin(app_client, h, did, sha1, change_reason="v1", change_significance="MAJOR")
    ).json()
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha2 = await _upload(app_client, h, did, f"ac1b-v2-{subj.a}".encode())
    v2 = (
        await _checkin(app_client, h, did, sha2, change_reason="v2", change_significance="MAJOR")
    ).json()

    # Seed both versions Approved + due (bypassing the single-active-version FSM) so they race.
    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as s:
        for vid in (v1["id"], v2["id"]):
            ver = (
                await s.execute(select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(vid)))
            ).scalar_one()
            ver.version_state = VersionState.Approved
            ver.effective_from = now
        d = (
            await s.execute(
                select(DocumentedInformation).where(DocumentedInformation.id == uuid.UUID(did))
            )
        ).scalar_one()
        d.current_state = DocumentCurrentState.Approved
        await s.commit()

    r1, r2 = await asyncio.gather(
        app_client.post(
            f"/api/v1/documents/{did}/release", headers=h, json={"version_id": v1["id"]}
        ),
        app_client.post(
            f"/api/v1/documents/{did}/release", headers=h, json={"version_id": v2["id"]}
        ),
        return_exceptions=True,
    )
    statuses = sorted(r.status_code for r in (r1, r2) if isinstance(r, httpx.Response))
    assert statuses == [200, 409], f"expected one 200 + one 409, got {(r1, r2)}"
    assert await _effective_count(did) == 1  # the invariant holds regardless of who won


# --- illegal transition + future-dated + revision + obsolete + signature seam -----------


async def test_illegal_transition_returns_409_with_allowed(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.a)
    h = _auth(token_factory, subj.a)
    did = (await _create(app_client, h, await _sop_type_id()))["id"]
    # Release a freshly-created Draft (nothing Approved) → 409 invalid_state_transition.
    r = await app_client.post(f"/api/v1/documents/{did}/release", headers=h, json={})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "invalid_state_transition"
    assert body["allowed_transitions"] == ["submit_review"]


async def test_future_dated_stays_approved_then_beat_sweep_releases(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.a)
    h = _auth(token_factory, subj.a)
    did = (await _create(app_client, h, await _sop_type_id()))["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha = await _upload(app_client, h, did, f"future-{subj.a}".encode())
    v = (
        await _checkin(app_client, h, did, sha, change_reason="v1", change_significance="MAJOR")
    ).json()
    await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=h)

    future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=7)).isoformat()
    ap = await app_client.post(
        f"/api/v1/documents/{did}/approve", headers=h, json={"effective_from": future}
    )
    assert ap.status_code == 200
    assert ap.json()["current_state"] == "Approved"

    # Manual release of a future-dated version is refused — it stays Approved (Beat releases it).
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=h, json={})
    assert rel.status_code == 422, rel.text
    assert rel.json()["code"] == "validation_error"

    # Make it due, then run the Beat sweep → it becomes Effective.
    async with get_sessionmaker()() as s:
        ver = (
            await s.execute(select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(v["id"])))
        ).scalar_one()
        ver.effective_from = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        await s.commit()

    from easysynq_api.services.vault import release_due

    released = await release_due()
    assert uuid.UUID(v["id"]) in released
    after = await _version(v["id"])
    assert after.version_state is VersionState.Effective


async def test_start_revision_opens_under_revision(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _make_effective(app_client, h, await _sop_type_id(), f"rev-{subj.a}".encode())
    did = doc["id"]
    eff_v = doc["current_effective_version_id"]

    sr = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=h)
    assert sr.status_code == 200, sr.text
    assert sr.json()["current_state"] == "UnderRevision"
    assert sr.json()["current_effective_version_id"] == eff_v  # the Effective version still governs

    # A second start-revision (already UnderRevision) is illegal.
    sr2 = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=h)
    assert sr2.status_code == 409
    assert sr2.json()["allowed_transitions"] == ["submit_review"]


async def test_obsolete_clears_effective_pointer(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _make_effective(app_client, h, await _sop_type_id(), f"obs-{subj.a}".encode())
    did = doc["id"]
    eff_v = doc["current_effective_version_id"]

    blank = await app_client.post(
        f"/api/v1/documents/{did}/obsolete", headers=h, json={"reason": "  "}
    )
    assert blank.status_code == 422  # reason required

    ob = await app_client.post(
        f"/api/v1/documents/{did}/obsolete", headers=h, json={"reason": "withdrawn"}
    )
    assert ob.status_code == 200, ob.text
    assert ob.json()["current_state"] == "Obsolete"
    assert ob.json()["current_effective_version_id"] is None
    assert (await _version(eff_v)).version_state is VersionState.Obsolete


async def test_singleton_one_effective_per_type(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[R25] Only one Effective singleton (Quality Policy) per (org, type) at a time. A second
    Quality Policy's release hits the R25 partial unique index → surfaced as 409 conflict."""
    await _grant(subj.a)
    h = _auth(token_factory, subj.a)
    pol = await _pol_type_id()

    first = await _make_effective(app_client, h, pol, f"pol-A-{subj.a}".encode())
    assert first["current_state"] == "Effective"
    assert first["is_singleton"] is True

    # A second Quality Policy: drive to Approved, then release → R25 conflict.
    bid = (await _create(app_client, h, pol))["id"]
    await app_client.post(f"/api/v1/documents/{bid}/checkout", headers=h)
    sha = await _upload(app_client, h, bid, f"pol-B-{subj.a}".encode())
    await _checkin(app_client, h, bid, sha, change_reason="v1", change_significance="MAJOR")
    await app_client.post(f"/api/v1/documents/{bid}/submit-review", headers=h)
    await app_client.post(f"/api/v1/documents/{bid}/approve", headers=h, json={})
    rel = await app_client.post(f"/api/v1/documents/{bid}/release", headers=h, json={})
    assert rel.status_code == 409, rel.text
    assert rel.json()["code"] == "conflict"


async def test_signature_sink_injectable_but_not_emitted(
    app_client: AsyncClient,
    app_under_test: object,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """The S5 seam is wired (injectable) but S4 emits no signature_event."""
    from easysynq_api.services.vault import CapturingSignatureEventSink, get_vault_signature_sink

    sink = CapturingSignatureEventSink()
    app_under_test.dependency_overrides[get_vault_signature_sink] = lambda: sink  # type: ignore[attr-defined]

    await _grant(subj.a)
    h = _auth(token_factory, subj.a)
    doc = await _make_effective(app_client, h, await _sop_type_id(), f"sig-{subj.a}".encode())
    assert doc["current_state"] == "Effective"
    assert sink.events == []  # no signature_event in S4 (S5 wires emission)
