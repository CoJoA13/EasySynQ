"""S4/S5 integration proofs — the document lifecycle FSM + the atomic single-Effective cutover,
now driven through the S5 task/approval flow under separation of duties.

The headline proofs are AC#1a (``test_release_supersedes``) and AC#1b
(``test_two_effective_impossible``). Under SoD (S5) these are multi-actor: the author (a) checks in
+ submits; the approver (b) decides approve via ``POST /tasks/{id}/decision``; release is by a
non-author (b with ``allow_approver_release`` on, or a third party). SoD itself is proven in
``test_sod.py``; here the variable under test is the FSM + cutover + signature emission.
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

from easysynq_api.db.models._signature_enums import SignatureMeaning
from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.signature_event import SignatureEvent as SignatureEventRow
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _map_clause, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}", c=f"kc-releaser-{salt}")


async def _signature_count(version_id: uuid.UUID, meaning: SignatureMeaning) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(SignatureEventRow)
                .where(
                    SignatureEventRow.signed_object_id == version_id,
                    SignatureEventRow.meaning == meaning,
                )
            )
        ).scalar_one()


# --- AC#1a -------------------------------------------------------------------------------


async def test_release_supersedes(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[AC#1a] Draft→…→Effective (author a, approver/releaser b), then a revision's release
    atomically supersedes the prior Effective version. Approval + both releases sign (S5)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)  # b approves AND releases
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    doc = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, f"ac1a-v1-{subj.a}".encode())
    did = doc["id"]
    assert doc["current_state"] == "Effective"
    v1_id = doc["current_effective_version_id"]
    assert v1_id is not None

    # Open a revision (author a), check in v2, run it through approve (b) → release (b).
    sr = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    assert sr.status_code == 200, sr.text
    assert sr.json()["current_state"] == "UnderRevision"
    sha2 = await _upload(app_client, ha, did, f"ac1a-v2-{subj.a}".encode())
    ci2 = await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MINOR")
    v2_id = ci2.json()["id"]
    assert (
        await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    ).status_code == 200
    task_id = await s5.task_for_doc(did)
    assert (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
        )
    ).status_code == 200
    rel2 = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel2.status_code == 200, rel2.text
    after = rel2.json()
    assert after["current_state"] == "Effective"
    assert after["current_effective_version_id"] == v2_id

    v1, v2 = await s5.get_version(v1_id), await s5.get_version(v2_id)
    assert v1.version_state is VersionState.Superseded
    assert v1.effective_to is not None
    assert v1.superseded_by_version_id == uuid.UUID(v2_id)
    assert v2.version_state is VersionState.Effective
    assert v2.effective_to is None
    assert await s5.effective_count(did) == 1
    # Each release emitted a signature_event(meaning=release).
    assert await _signature_count(uuid.UUID(v1_id), SignatureMeaning.release) == 1
    assert await _signature_count(uuid.UUID(v2_id), SignatureMeaning.release) == 1


# --- AC#1b -------------------------------------------------------------------------------


async def test_two_effective_impossible(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[AC#1b] Two parallel releases targeting two distinct Approved versions of one document →
    exactly one Effective. Released by the non-author ``b`` (SoD-2: the author may never release;
    the direct-seeded versions carry no approval signature, so b is not blocked as approver)."""
    await s5.grant_lifecycle(subj.a)  # author of both versions
    await s5.grant_lifecycle(subj.b)  # the releaser (≠ author)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]

    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha1 = await _upload(app_client, ha, did, f"ac1b-v1-{subj.a}".encode())
    v1 = (
        await _checkin(app_client, ha, did, sha1, change_reason="v1", change_significance="MAJOR")
    ).json()
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha2 = await _upload(app_client, ha, did, f"ac1b-v2-{subj.a}".encode())
    v2 = (
        await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MAJOR")
    ).json()

    # Seed both versions Approved + due (bypassing the FSM) so they race; no approval signature.
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
            f"/api/v1/documents/{did}/release", headers=hb, json={"version_id": v1["id"]}
        ),
        app_client.post(
            f"/api/v1/documents/{did}/release", headers=hb, json={"version_id": v2["id"]}
        ),
        return_exceptions=True,
    )
    statuses = sorted(r.status_code for r in (r1, r2) if isinstance(r, httpx.Response))
    assert statuses == [200, 409], f"expected one 200 + one 409, got {(r1, r2)}"
    assert await s5.effective_count(did) == 1


# --- illegal transition + future-dated + revision + obsolete + signatures ----------------


async def test_illegal_transition_returns_409_with_allowed(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]
    # Release a freshly-created Draft (nothing Approved) → 409 invalid_state_transition. No Approved
    # version exists, so the release SoD scope degrades and the FSM 409 is reached.
    r = await app_client.post(f"/api/v1/documents/{did}/release", headers=ha, json={})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "invalid_state_transition"
    assert body["allowed_transitions"] == ["submit_review"]


async def test_future_dated_stays_approved_then_beat_sweep_releases(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, f"future-{subj.a}".encode())
    v = (
        await _checkin(app_client, ha, did, sha, change_reason="v1", change_significance="MAJOR")
    ).json()
    await _map_clause(app_client, ha, did)  # S9: submit-review needs ≥1 clause_mapping
    await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)

    # Approve (b) with a future go-live — carried on the decision body (replaces /approve's body).
    future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=7)).isoformat()
    task_id = await s5.task_for_doc(did)
    ap = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=hb,
        json={"outcome": "approve", "effective_from": future},
    )
    assert ap.status_code == 200, ap.text

    # Manual release of a future-dated version is refused — it stays Approved (Beat releases it).
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 422, rel.text
    assert rel.json()["code"] == "validation_error"

    # Make it due, then run the Beat sweep → it becomes Effective (with a system release signature).
    async with get_sessionmaker()() as s:
        ver = (
            await s.execute(select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(v["id"])))
        ).scalar_one()
        ver.effective_from = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        await s.commit()

    from easysynq_api.services.vault import release_due

    released = await release_due()
    assert uuid.UUID(v["id"]) in released
    after = await s5.get_version(v["id"])
    assert after.version_state is VersionState.Effective
    assert await _signature_count(uuid.UUID(v["id"]), SignatureMeaning.release) == 1
    # The Beat release runs as the system principal — no human signer (nullable), system context.
    async with get_sessionmaker()() as s:
        sig = (
            await s.execute(
                select(SignatureEventRow).where(
                    SignatureEventRow.signed_object_id == uuid.UUID(v["id"]),
                    SignatureEventRow.meaning == SignatureMeaning.release,
                )
            )
        ).scalar_one()
        assert sig.signer_user_id is None
        assert (sig.auth_context or {}).get("system") is True


async def test_immediate_approval_is_not_auto_released_by_beat(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """SoD-2 guard: an immediately-approved version (no effective_from) is NOT Beat-eligible —
    release stays a separate, SoD-gated act so allow_approver_release=False requires a separate
    releaser (the Beat must not auto-release what the sole approver was just 403'd from)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    await s5.set_approver_release(await s5.default_org_id(), False)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"beat-guard")

    from easysynq_api.services.vault import release_due

    released = await release_due()

    async with get_sessionmaker()() as s:
        version_id = (
            await s.execute(
                select(DocumentVersion.id)
                .where(DocumentVersion.document_id == uuid.UUID(did))
                .order_by(DocumentVersion.version_seq.desc())
                .limit(1)
            )
        ).scalar_one()
        doc = await s.get(DocumentedInformation, uuid.UUID(did))
    assert version_id not in released
    assert doc.current_state is DocumentCurrentState.Approved  # NOT auto-released
    assert await s5.effective_count(did) == 0


async def test_start_revision_opens_under_revision(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"rev")
    did = doc["id"]
    eff_v = doc["current_effective_version_id"]

    sr = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    assert sr.status_code == 200, sr.text
    assert sr.json()["current_state"] == "UnderRevision"
    assert sr.json()["current_effective_version_id"] == eff_v  # the Effective version still governs

    sr2 = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    assert sr2.status_code == 409
    assert sr2.json()["allowed_transitions"] == ["submit_review"]


async def test_obsolete_clears_effective_pointer_and_signs(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"obs")
    did = doc["id"]
    eff_v = doc["current_effective_version_id"]

    blank = await app_client.post(
        f"/api/v1/documents/{did}/obsolete", headers=ha, json={"reason": "  "}
    )
    assert blank.status_code == 422  # reason required

    ob = await app_client.post(
        f"/api/v1/documents/{did}/obsolete", headers=ha, json={"reason": "withdrawn"}
    )
    assert ob.status_code == 200, ob.text
    assert ob.json()["current_state"] == "Obsolete"
    assert ob.json()["current_effective_version_id"] is None
    assert (await s5.get_version(eff_v)).version_state is VersionState.Obsolete
    assert await _signature_count(uuid.UUID(eff_v), SignatureMeaning.obsolete) == 1


async def test_singleton_one_effective_per_type(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[R25] Only one Effective singleton (Quality Policy) per (org, type). A second's release hits
    the R25 partial unique index → 409 conflict."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    pol = await s5.type_id("POL")

    first = await s5.drive_to_effective(app_client, ha, hb, hb, pol, f"pol-A-{subj.a}".encode())
    assert first["current_state"] == "Effective"
    assert first["is_singleton"] is True

    # A second Quality Policy: drive to Approved, then release → R25 conflict.
    bid = await s5.drive_to_approved(app_client, ha, hb, pol, f"pol-B-{subj.a}".encode())
    rel = await app_client.post(f"/api/v1/documents/{bid}/release", headers=hb, json={})
    assert rel.status_code == 409, rel.text
    assert rel.json()["code"] == "conflict"


async def test_approval_emits_signature(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """S5 emits signature_events (replacing the S4 'injectable but not emitted' seam test):
    the approval decision signs the version (meaning=approval)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"sig")

    async with get_sessionmaker()() as s:
        version_id = (
            await s.execute(
                select(DocumentVersion.id)
                .where(DocumentVersion.document_id == uuid.UUID(did))
                .order_by(DocumentVersion.version_seq.desc())
                .limit(1)
            )
        ).scalar_one()
    assert await _signature_count(version_id, SignatureMeaning.approval) == 1
