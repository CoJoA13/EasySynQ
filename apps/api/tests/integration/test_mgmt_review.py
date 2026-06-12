"""S-mr-1 integration (Phase 3): create → list → detail of a Management Review, plus the submit
freeze (Draft → InReview + a ``mgmt_review_minutes`` snapshot). Grants are SYSTEM-scope
PermissionOverrides on JIT users (the test_quality_objectives / test_objective_lifecycle harness).

The full submit → approve → release → 9.3-★-COVERED lifecycle is Phase 8 (not built here)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.session import get_sessionmaker

from .test_quality_objectives import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration

# mgmtReview.create to create, .read to list/detail, .record_outputs to author/submit.
_MR_KEYS = ("mgmtReview.create", "mgmtReview.read", "mgmtReview.record_outputs")

# The six sourced-read keys the compiler PDP-checks against the review OWNER (verbatim from the live
# endpoints; complaints ride record.read — there is no complaint.read key). With the union held, all
# six sourced rows compile available=True.
_SOURCE_KEYS = (
    "objective.read",
    "audit.read",
    "capa.read",
    "ncr.read",
    "record.read",
    "kpi.read",
    "report.compliance_checklist.read",
    "drift.read",
)

# The 12 canonical 9.3.2 input types (enum-declaration order). 6 sourced + 6 sourceless gap.
_SOURCED_TYPES = {
    "OBJECTIVES_STATUS",
    "PROCESS_PERFORMANCE",
    "NONCONFORMITIES_CAPA",
    "MONITORING_RESULTS",
    "AUDIT_RESULTS",
    "PRIOR_ACTIONS",  # sourced-but-gap until a 2nd review exists (v1)
}
_SOURCELESS_TYPES = {
    "CONTEXT_CHANGES",
    "CUSTOMER_SATISFACTION",
    "SUPPLIER_PERFORMANCE",
    "RESOURCE_ADEQUACY",
    "RISK_OPPORTUNITY_ACTIONS",
    "IMPROVEMENT_OPPORTUNITIES",
}


async def _create_review(client: AsyncClient, h: dict[str, str], title: str) -> str:
    r = await client.post(
        "/api/v1/management-reviews",
        headers=h,
        json={"title": title, "period_label": "2026 Annual"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_create_list_detail_management_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)

    rid = await _create_review(app_client, h, "Q2 2026 Review")

    # list (the register) contains the new review
    lst = (await app_client.get("/api/v1/management-reviews", headers=h)).json()
    assert any(row["id"] == rid for row in lst["data"]), lst

    # detail: Draft, mapped identity, the inputs/outputs collections present (empty pre-compile)
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    assert det["id"] == rid
    assert det["current_state"] == "Draft"
    assert det["period_label"] == "2026 Annual"
    assert det["close_state"] is None
    assert det["inputs"] == []
    assert det["outputs"] == []


async def test_create_requires_mgmt_review_create(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    reader = f"mr-rdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("mgmtReview.read",))
    r = await app_client.post(
        "/api/v1/management-reviews", headers=hr, json={"title": "No create key"}
    )
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "permission_denied"


async def test_add_output_action_requires_owner(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-out-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, h, "Outputs review")

    # an ACTION output with no owner is a 422 (it would spawn an ownerless MR_ACTION at release)
    bad = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=h,
        json={"output_type": "ACTION", "description": "Tighten supplier controls"},
    )
    assert bad.status_code == 422, bad.text

    # a DECISION output records cleanly
    ok = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=h,
        json={"output_type": "DECISION", "description": "QMS remains suitable and effective"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["output_type"] == "DECISION"

    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    assert len(det["outputs"]) == 1


async def test_submit_freezes_minutes_and_enters_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-sub-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, h, "Submittable review")

    # set the review meta the freeze reads into the minutes
    meta = await app_client.patch(
        f"/api/v1/management-reviews/{rid}",
        headers=h,
        json={"review_date": "2026-06-12", "attendees": [{"name": "Mara", "role": "QM"}]},
    )
    assert meta.status_code == 200, meta.text

    # author a decision so the frozen minutes carry an output
    await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=h,
        json={"output_type": "DECISION", "description": "Approve the objectives for 2026"},
    )

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["current_state"] == "InReview"

    # a Draft version exists with the frozen minutes in its metadata_snapshot
    async with get_sessionmaker()() as s:
        v = (
            await s.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(rid))
            )
        ).scalar_one()
        minutes = (v.metadata_snapshot or {}).get("mgmt_review_minutes")
        assert minutes is not None
        assert minutes["period_label"] == "2026 Annual"
        assert minutes["review_date"] == "2026-06-12"
        assert minutes["attendees"] == [{"name": "Mara", "role": "QM"}]
        descriptions = [o["description"] for o in minutes["outputs"]]
        assert "Approve the objectives for 2026" in descriptions


async def test_update_output_to_action_requires_owner(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-upd-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    owner_id = await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, h, "Update-output review")

    # author a DECISION output (no owner required)
    created = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=h,
        json={"output_type": "DECISION", "description": "QMS remains effective"},
    )
    assert created.status_code == 201, created.text
    output_id = created.json()["id"]

    # PATCH it to ACTION without an owner → 422 (an ACTION must keep an owner)
    bad = await app_client.patch(
        f"/api/v1/management-reviews/{rid}/outputs/{output_id}",
        headers=h,
        json={"output_type": "ACTION"},
    )
    assert bad.status_code == 422, bad.text

    # PATCH it to ACTION WITH an owner → 200
    ok = await app_client.patch(
        f"/api/v1/management-reviews/{rid}/outputs/{output_id}",
        headers=h,
        json={"output_type": "ACTION", "owner_user_id": str(owner_id)},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["output_type"] == "ACTION"
    assert ok.json()["owner_user_id"] == str(owner_id)


async def test_submit_twice_is_a_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-dbl-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)
    rid = await _create_review(app_client, h, "Submit once")
    first = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert first.status_code == 200, first.text
    again = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert again.status_code == 409, again.text


async def test_compile_inputs_writes_all_twelve_rows(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A fully-granted owner compiles all 12 rows: the 6 sourced rows available=True with a summary,
    the 6 sourceless gap rows available=False with a reason (F4). PRIOR_ACTIONS is a sourced-but-gap
    row (no 2nd review yet)."""
    subject = f"mr-comp-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    # The creator IS the review owner (create_document sets owner_user_id=actor.id); grant the union
    # so every sourced read PDP-passes for the owner.
    await _grant(subject, _MR_KEYS + _SOURCE_KEYS)
    rid = await _create_review(app_client, h, "Fully-granted compile")

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert r.status_code == 200, r.text
    inputs = r.json()["inputs"]

    by_type = {ri["input_type"]: ri for ri in inputs}
    assert set(by_type) == _SOURCED_TYPES | _SOURCELESS_TYPES
    assert len(inputs) == 12

    # Every row's source_ref carries the envelope (available + generated_at).
    for ri in inputs:
        ref = ri["source_ref"]
        assert "available" in ref and "generated_at" in ref
        assert ref["available"] is ri["available"]

    # The five live sourced reads are available with a summary.
    for t in (
        "OBJECTIVES_STATUS",
        "PROCESS_PERFORMANCE",
        "NONCONFORMITIES_CAPA",
        "MONITORING_RESULTS",
        "AUDIT_RESULTS",
    ):
        assert by_type[t]["available"] is True, by_type[t]
        assert "summary" in by_type[t]["source_ref"], by_type[t]

    # The objectives scorecard summary has the EXACT by_rag key set.
    obj = by_type["OBJECTIVES_STATUS"]["source_ref"]["summary"]
    assert set(obj["by_rag"]) == {"green", "amber", "red", "unmeasured"}
    assert obj["on_target"] == obj["by_rag"]["green"]

    # PRIOR_ACTIONS + the six sourceless inputs are gap rows (available=False with a reason).
    for t in {"PRIOR_ACTIONS"} | _SOURCELESS_TYPES:
        assert by_type[t]["available"] is False, by_type[t]
        assert by_type[t]["source_ref"].get("reason"), by_type[t]


async def test_compile_inputs_owner_without_audit_read_yields_gap_row_not_403(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """F3: an owner LACKING audit.read yields an available=False AUDIT_RESULTS gap row — NOT a 403
    of the whole compile. The gate on the TRIGGER is the caller's record_outputs; per-source access
    is the owner's, fail-closed to a gap row."""
    subject = f"mr-noaud-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    # The union MINUS audit.read — every other sourced read stays available.
    keys = _MR_KEYS + tuple(k for k in _SOURCE_KEYS if k != "audit.read")
    await _grant(subject, keys)
    rid = await _create_review(app_client, h, "Owner missing audit.read")

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert r.status_code == 200, r.text  # NOT a 403 — the whole compile succeeds
    by_type = {ri["input_type"]: ri for ri in r.json()["inputs"]}

    aud = by_type["AUDIT_RESULTS"]
    assert aud["available"] is False, aud
    assert aud["source_ref"].get("reason") == "not available (insufficient access)", aud
    assert "summary" not in aud["source_ref"], aud

    # The other live sourced reads stay available (only the audit source gap-rowed).
    assert by_type["OBJECTIVES_STATUS"]["available"] is True
    assert by_type["AUDIT_RESULTS"]["available"] is False


async def test_recompile_replaces_the_working_input_set(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Re-compile (Draft) REPLACES the working review_input set (delete-then-insert) — the row count
    stays 12, never doubles."""
    subject = f"mr-recomp-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS + _SOURCE_KEYS)
    rid = await _create_review(app_client, h, "Re-compile review")

    first = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert first.status_code == 200, first.text
    assert len(first.json()["inputs"]) == 12

    second = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert second.status_code == 200, second.text
    assert len(second.json()["inputs"]) == 12  # replaced, not appended

    # The detail read also shows exactly 12 (no orphaned prior-compile rows).
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    assert len(det["inputs"]) == 12


async def test_compile_inputs_blocked_after_submit(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """compile-inputs is Draft-only — a 409 once the review leaves Draft (the snapshot is then the
    WORM authority)."""
    subject = f"mr-comp-late-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS + _SOURCE_KEYS)
    rid = await _create_review(app_client, h, "Compile-after-submit review")
    submitted = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert submitted.status_code == 200, submitted.text

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=h)
    assert r.status_code == 409, r.text
