"""S-obj-4 integration: the byte-path guard (O-5), the PATCH edit surface (O-1), start-revision +
the revision-aware submit, the read-back switch (O-3), mid-revision measurement capture (O-2), and
the unit-change reset (micro-call B). Run-scoped/delta assertions — the session DB is shared."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.working_draft import WorkingDraft
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_objective_lifecycle import _OBJ_KEYS, _create_objective
from .test_quality_objectives import _grant
from .test_vault import _auth, _checkin

pytestmark = pytest.mark.integration


async def test_generic_byte_path_rejected_on_objective(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """O-5: checkout/checkin/start-revision/submit-review 422 on an OBJ id (the commitment is the
    ONLY content an objective can carry; generic submit would bypass the content-aware freeze).
    Reads stay open — the approver card depends on /versions. Replaces the S-obj-3
    test_submit_freezes_even_after_a_generic_byte_checkin (the seam is now welded shut; the
    snapshot-keyed freeze stays pinned at unit level as belt-and-braces)."""
    subject = f"obj4-guard-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    await _grant(
        subject,
        ("document.checkout", "document.edit", "document.submit", "document.read_draft"),
    )
    oid = await _create_objective(app_client, h, "Byte-guard objective")

    for path in ("checkout", "start-revision", "submit-review"):
        r = await app_client.post(f"/api/v1/documents/{oid}/{path}", headers=h)
        assert r.status_code == 422, f"{path}: {r.text}"
        body = r.json()
        assert body["errors"][0]["code"] == "objective_managed_via_objectives", path
    # the guard fired BEFORE locks.acquire: a SECOND checkout still 422s the same way (a leaked
    # Redis lock from the first attempt would 409 lock_conflict here instead)
    r2 = await app_client.post(f"/api/v1/documents/{oid}/checkout", headers=h)
    assert r2.status_code == 422, r2.text
    assert r2.json()["errors"][0]["code"] == "objective_managed_via_objectives"
    # checkin: the guard fires BEFORE the working-draft 409 (deterministic 422, no checkout exists)
    ci = await _checkin(
        app_client, h, oid, "0" * 64, change_reason="x", change_significance="MAJOR"
    )
    assert ci.status_code == 422, ci.text
    assert ci.json()["errors"][0]["code"] == "objective_managed_via_objectives"
    # reads stay open
    vs = await app_client.get(f"/api/v1/documents/{oid}/versions", headers=h)
    assert vs.status_code == 200, vs.text


async def test_patch_edits_working_commitment_in_draft(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-patch-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Patchable objective")
    r = await app_client.patch(
        f"/api/v1/objectives/{oid}",
        headers=h,
        json={"target_value": "97", "at_risk_threshold": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_value"] == "97"
    assert body["at_risk_threshold"] is None  # explicit null CLEARS
    # omitted fields inherit: baseline untouched by the partial PATCH
    assert body["baseline_value"] == "90"


async def test_patch_409_outside_draft_or_under_revision(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-p409-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "InReview is read-only")
    submit_r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert submit_r.status_code == 200
    r = await app_client.patch(f"/api/v1/objectives/{oid}", headers=h, json={"target_value": "1"})
    assert r.status_code == 409, r.text


async def test_patch_explicit_null_on_required_field_422_and_bad_policy_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-p422-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Validation objective")
    r1 = await app_client.patch(f"/api/v1/objectives/{oid}", headers=h, json={"target_value": None})
    assert r1.status_code == 422, r1.text
    r2 = await app_client.patch(
        f"/api/v1/objectives/{oid}", headers=h, json={"policy_id": str(uuid.uuid4())}
    )
    assert r2.status_code == 422, r2.text  # not the current Effective POL (mirrors create)


async def test_patch_requires_objective_manage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    owner = f"obj4-pown-{uuid.uuid4()}"
    ho = _auth(token_factory, owner)
    await _grant(owner, _OBJ_KEYS)
    oid = await _create_objective(app_client, ho, "Manage-gated patch")
    reader = f"obj4-prdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("objective.read",))
    r = await app_client.patch(f"/api/v1/objectives/{oid}", headers=hr, json={"target_value": "1"})
    assert r.status_code == 403, r.text


async def _drive_to_effective(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    title: str,
) -> tuple[str, dict[str, str], dict[str, str], dict[str, str]]:
    """Create → submit (owner) → approve (Approver role) → release (third party).

    Returns (objective_id, h_owner, h_approver, h_releaser). Self-provided personas per call
    (run-scoped)."""
    salt = uuid.uuid4().hex[:8]
    owner, approver, releaser = f"obj4-ow-{salt}", f"obj4-ap-{salt}", f"obj4-rl-{salt}"
    ho, hap, hrl = (
        _auth(token_factory, owner),
        _auth(token_factory, approver),
        _auth(token_factory, releaser),
    )
    await _grant(owner, _OBJ_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(approver, ("document.review",))  # changes_requested needs document.review
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))
    oid = await _create_objective(app_client, ho, title)
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    return oid, ho, hap, hrl


async def test_start_revision_flips_under_revision_and_keeps_governing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    oid, ho, _hap, _hrl = await _drive_to_effective(
        app_client, token_factory, "Revisable objective"
    )
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(oid))
        assert doc is not None and doc.current_effective_version_id is not None
        ptr_before = doc.current_effective_version_id
    r = await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    assert r.status_code == 200, r.text
    assert r.json()["current_state"] == "UnderRevision"
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(oid))
        assert doc is not None
        # R43: POINTER IDENTITY — the governing pointer never moved (not merely "still set")
        assert doc.current_effective_version_id == ptr_before
        v = await s.get(DocumentVersion, ptr_before)
        assert v is not None and v.version_state is VersionState.Effective  # v1 keeps governing
        wd = (
            await s.execute(select(WorkingDraft).where(WorkingDraft.document_id == uuid.UUID(oid)))
        ).scalar_one_or_none()
        assert wd is not None  # start-revision opened the WD …
        assert wd.source_version_id == ptr_before  # … seeded from the governing Effective version


async def test_start_revision_409_on_draft_and_403_for_reader(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-sr409-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Draft cannot revise")
    r = await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=h)
    assert r.status_code == 409, r.text
    reader = f"obj4-srrdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("objective.read",))
    r2 = await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=hr)
    assert r2.status_code == 403, r2.text


async def test_full_revision_round_trip(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """THE slice test: Effective v1 → start-revision → PATCH → re-submit (new frozen version,
    WD gone) → re-approve → re-release → v1 Superseded + v2 Effective (INV-1) + the edit lock was
    released (a second start-revision succeeds)."""
    oid, ho, hap, hrl = await _drive_to_effective(app_client, token_factory, "Round-trip objective")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    p = await app_client.patch(f"/api/v1/objectives/{oid}", headers=ho, json={"target_value": "99"})
    assert p.status_code == 200, p.text
    sub = await app_client.post(
        f"/api/v1/objectives/{oid}/submit-review",
        headers=ho,
        json={"change_reason": "Raise the bar after Q2 results"},
    )
    assert sub.status_code == 200, sub.text
    assert sub.json()["current_state"] == "InReview"
    async with get_sessionmaker()() as s:
        versions = (
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == uuid.UUID(oid))
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )
        assert len(versions) == 2  # the v1 commitment + the re-frozen v2
        v2 = versions[-1]
        assert (v2.metadata_snapshot or {})["objective_commitment"]["target_value"] == "99"
        assert v2.change_reason == "Raise the bar after Q2 results"
        assert v2.version_state is VersionState.InReview
        assert versions[0].version_state is VersionState.Effective  # v1 STILL governs
        wd = (
            await s.execute(select(WorkingDraft).where(WorkingDraft.document_id == uuid.UUID(oid)))
        ).scalar_one_or_none()
        assert wd is None  # the start-revision WorkingDraft was consumed by the submit

    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["current_state"] == "Effective"
    async with get_sessionmaker()() as s:
        versions = (
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == uuid.UUID(oid))
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )
        v1, v2 = versions[0], versions[-1]
        assert v1.version_state is VersionState.Superseded
        assert v1.effective_to is not None
        assert v1.superseded_by_version_id == v2.id
        assert v2.version_state is VersionState.Effective and v2.effective_to is None
    # the edit lock was released at submit: a NEW revision can start (would 409 lock_conflict else)
    again = await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    assert again.status_code == 200, again.text


async def test_resubmit_after_changes_requested(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Unchanged re-submit re-advances the SAME Draft version (no duplicate freeze); a PATCH in
    the changes_requested window re-freezes a NEW version carrying the edit."""
    salt = uuid.uuid4().hex[:8]
    owner, approver = f"obj4-rs-{salt}", f"obj4-ra-{salt}"
    ho, hap = _auth(token_factory, owner), _auth(token_factory, approver)
    await _grant(owner, _OBJ_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(approver, ("document.review",))
    oid = await _create_objective(app_client, ho, "Changes-requested objective")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    task_id = await s5.task_for_doc(oid)
    rc = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=hap,
        json={"outcome": "changes_requested", "comment": "tighten the threshold"},
    )
    assert rc.status_code == 200, rc.text
    # leg 1: no edit → the same Draft re-advances, still ONE version
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    async with get_sessionmaker()() as s:
        n1 = len(
            (
                await s.execute(
                    select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(oid))
                )
            )
            .scalars()
            .all()
        )
    assert n1 == 1
    # leg 2: changes_requested again, PATCH, re-submit → a NEW frozen version with the edit
    task_id = await s5.task_for_doc(oid)
    rc2 = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=hap,
        json={"outcome": "changes_requested", "comment": "again"},
    )
    assert rc2.status_code == 200, rc2.text
    assert (
        await app_client.patch(
            f"/api/v1/objectives/{oid}", headers=ho, json={"at_risk_threshold": "96"}
        )
    ).status_code == 200
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    async with get_sessionmaker()() as s:
        versions = (
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == uuid.UUID(oid))
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )
    assert len(versions) == 2
    frozen = versions[-1]
    assert (frozen.metadata_snapshot or {})["objective_commitment"]["at_risk_threshold"] == "96"


async def test_submit_409_on_in_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-s409-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Double submit")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    ).status_code == 200
    r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert r.status_code == 409, r.text
