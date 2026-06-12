"""S-obj-4 integration: the byte-path guard (O-5), the PATCH edit surface (O-1), start-revision +
the revision-aware submit, the read-back switch (O-3), mid-revision measurement capture (O-2), and
the unit-change reset (micro-call B). Run-scoped/delta assertions — the session DB is shared."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

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
