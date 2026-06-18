"""S-obj-4 integration: the byte-path guard (O-5), the PATCH edit surface (O-1), start-revision +
the revision-aware submit, the read-back switch (O-3), mid-revision measurement capture (O-2), and
the unit-change reset (micro-call B). Run-scoped/delta assertions — the session DB is shared."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.kpi_measurement import KpiMeasurement
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


async def _record(
    app_client: AsyncClient, h: dict[str, str], oid: str, *, value: str, unit: str, period: str
) -> int:
    r = await app_client.post(
        f"/api/v1/objectives/{oid}/measurements",
        headers=h,
        json={"period": period, "value": value, "unit": unit},
    )
    return r.status_code


async def test_unit_change_revision_resets_current_value(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    oid, ho, hap, hrl = await _drive_to_effective(
        app_client, token_factory, "Unit-change objective"
    )
    assert await _record(app_client, ho, oid, value="92", unit="%", period="2026-05-31") == 201
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=ho)).json()
    assert detail["current_value"] == "92"
    # revise % → count
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    assert (
        await app_client.patch(
            f"/api/v1/objectives/{oid}",
            headers=ho,
            json={
                "unit": "count",
                "target_value": "10",
                "at_risk_threshold": None,
                "baseline_value": None,
            },
        )
    ).status_code == 200
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    task_id = await s5.task_for_doc(oid)
    assert (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
        )
    ).status_code == 200
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    # micro-call B: the old %-readings can't grade a count-target — honest unmeasured
    assert rel.json()["current_value"] is None
    assert rel.json()["rag"] == "unmeasured"
    # the next reading validates against the NEW governing unit and re-rolls
    assert await _record(app_client, ho, oid, value="8", unit="count", period="2026-06-30") == 201
    assert await _record(app_client, ho, oid, value="8", unit="%", period="2026-07-31") == 422


async def test_reads_serve_governing_during_revision(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """O-3/F-2 closed: during an UnderRevision edit, register/scorecard/detail keep grading
    against the GOVERNING frozen commitment; the edit shows only as pending_commitment."""
    oid, ho, _hap, _hrl = await _drive_to_effective(app_client, token_factory, "Governing reads")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    assert (
        await app_client.patch(f"/api/v1/objectives/{oid}", headers=ho, json={"target_value": "50"})
    ).status_code == 200
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=ho)).json()
    assert detail["target_value"] == "98"  # the governing v1 target, NOT the in-edit 50
    assert detail["pending_commitment"]["target_value"] == "50"  # the edit, detail-only
    assert detail["capabilities"]["edit"] is True
    assert detail["capabilities"]["start_revision"] is True
    row = next(
        o
        for o in (await app_client.get("/api/v1/objectives", headers=ho)).json()["data"]
        if o["id"] == oid
    )
    assert row["target_value"] == "98"
    assert "pending_commitment" not in row  # detail-only
    sc = next(
        o
        for o in (await app_client.get("/api/v1/objectives/scorecard", headers=ho)).json()[
            "objectives"
        ]
        if o["id"] == oid
    )
    assert sc["target_value"] == "98"


async def test_pending_commitment_null_without_divergence(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    oid, ho, _hap, _hrl = await _drive_to_effective(app_client, token_factory, "No divergence")
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=ho)).json()
    assert detail["pending_commitment"] is None  # working == governing after release


async def test_measurement_mid_revision_captures_governing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """O-2 + S-obj-freeze: the unit gate + the FULL frozen grading basis (target, direction,
    threshold) read the governing commitment — an unapproved mid-revision edit can never leak into
    evidence-grade KPI_READING records."""
    oid, ho, _hap, _hrl = await _drive_to_effective(app_client, token_factory, "Mid-rev capture")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    # edit unit/target AND the grading basis (direction + threshold) in the uncommitted working row
    assert (
        await app_client.patch(
            f"/api/v1/objectives/{oid}",
            headers=ho,
            json={
                "unit": "count",
                "target_value": "10",
                "direction": "LOWER_IS_BETTER",
                "at_risk_threshold": None,
            },
        )
    ).status_code == 200
    # governing unit is still "%" — a "count" reading is rejected, a "%" one accepted
    assert await _record(app_client, ho, oid, value="9", unit="count", period="2026-05-31") == 422
    assert await _record(app_client, ho, oid, value="97", unit="%", period="2026-05-31") == 201
    ms = (await app_client.get(f"/api/v1/objectives/{oid}/measurements", headers=ho)).json()["data"]
    assert ms[0]["target_at_capture"] == "98"  # the governing v1 target, never the in-edit 10
    # the frozen basis is internal (not exposed) — assert it captured GOVERNING, not the in-edit row
    async with get_sessionmaker()() as s:
        km = (
            await s.execute(
                select(KpiMeasurement).where(KpiMeasurement.objective_id == uuid.UUID(oid))
            )
        ).scalar_one()
        assert (
            km.direction_at_capture is ObjectiveDirection.HIGHER_IS_BETTER
        )  # governing, not LOWER
        assert str(km.at_risk_threshold_at_capture) == "95"  # governing band, not the cleared None


async def test_same_unit_revision_preserves_current_value(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Micro-call B's conditional is load-bearing: a target-only (same-unit) revision must NOT
    wipe the operational rollup (the reset fires ONLY on a unit change)."""
    oid, ho, hap, hrl = await _drive_to_effective(app_client, token_factory, "Same-unit revision")
    assert await _record(app_client, ho, oid, value="92", unit="%", period="2026-05-31") == 201
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    assert (
        await app_client.patch(f"/api/v1/objectives/{oid}", headers=ho, json={"target_value": "99"})
    ).status_code == 200
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    task_id = await s5.task_for_doc(oid)
    assert (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
        )
    ).status_code == 200
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["current_value"] == "92"  # preserved — and now graded vs the NEW target
    assert rel.json()["target_value"] == "99"


async def test_draft_unit_patch_resets_stale_rollup(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """diff-critic MAJOR: a Draft objective can carry old-unit measurements; a pre-first-release
    unit PATCH must reset current_value (the release reset can't — there is no prior governing
    unit), or the stale cross-unit rollup grades the new target through first release."""
    subject = f"obj4-dunit-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Draft unit-change objective")
    assert await _record(app_client, h, oid, value="92", unit="%", period="2026-05-31") == 201
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=h)).json()
    assert detail["current_value"] == "92"
    r = await app_client.patch(
        f"/api/v1/objectives/{oid}", headers=h, json={"unit": "count", "target_value": "10"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["current_value"] is None  # the stale %-rollup cannot grade a count target
    assert r.json()["rag"] == "unmeasured"
    # a same-unit PATCH never wipes the rollup
    assert await _record(app_client, h, oid, value="8", unit="count", period="2026-06-30") == 201
    r2 = await app_client.patch(f"/api/v1/objectives/{oid}", headers=h, json={"target_value": "9"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["current_value"] == "8"
