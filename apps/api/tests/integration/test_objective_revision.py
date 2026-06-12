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
