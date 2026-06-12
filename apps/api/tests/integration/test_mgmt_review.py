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
