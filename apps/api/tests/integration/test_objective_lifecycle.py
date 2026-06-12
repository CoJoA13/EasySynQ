"""S-obj-3 integration: the objective lifecycle (submit → approve → release → Effective), the
6.2-★ flip to COVERED, and the new reads. Grants are SYSTEM-scope PermissionOverrides on JIT users
keyed by keycloak_subject (the test_quality_objectives / s5_helpers precedent)."""

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

_OBJ_KEYS = ("objective.read", "objective.manage", "kpi.read", "kpi.record")


async def _create_objective(client: AsyncClient, h: dict[str, str], title: str) -> str:
    r = await client.post(
        "/api/v1/objectives",
        headers=h,
        json={
            "title": title,
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
            "at_risk_threshold": "95",
            "baseline_value": "90",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_submit_freezes_the_commitment_and_enters_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-sub-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "On-time delivery")

    r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["current_state"] == "InReview"

    # a Draft version exists with the frozen commitment in its metadata_snapshot
    async with get_sessionmaker()() as s:
        v = (
            await s.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(oid))
            )
        ).scalar_one()
        commitment = (v.metadata_snapshot or {}).get("objective_commitment")
        assert commitment is not None
        assert commitment["target_value"] == "98"
        assert commitment["unit"] == "%"
        assert commitment["direction"] == "HIGHER_IS_BETTER"
        assert commitment["at_risk_threshold"] == "95"


async def test_submit_requires_objective_manage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    owner = f"obj-own-{uuid.uuid4()}"
    ho = _auth(token_factory, owner)
    await _grant(owner, _OBJ_KEYS)
    oid = await _create_objective(app_client, ho, "Needs manage")

    # a reader without objective.manage cannot submit
    reader = f"obj-rdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("objective.read",))
    r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hr)
    assert r.status_code == 403, r.text


async def test_submit_twice_is_a_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-dbl-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Submit once")
    first = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert first.status_code == 200, first.text
    again = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert again.status_code == 409, again.text
