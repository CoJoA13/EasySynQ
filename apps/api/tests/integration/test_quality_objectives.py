"""S-obj-1 integration: objectives ride the seeded objective.*/kpi.* keys (PROCESS-scoped); the test
actor has no role assignment, so each test grants the keys it needs at SYSTEM scope (the test_capa /
test_audits precedent — a SYSTEM grant matches any resource)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.quality_objective import QualityObjective
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

# the established integration helpers (test_capa precedent)
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_OBJ_KEYS = ("objective.read", "objective.manage", "kpi.read", "kpi.record")


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant keys at SYSTEM scope via PermissionOverride (test_capa.py:55-78, verbatim).

    A SYSTEM override is a real Scope ROW (level=SYSTEM) referenced by scope_id —
    NOT an inline JSON scope.
    """
    async with get_sessionmaker()() as s:
        # create-or-get the JIT app_user row by keycloak_subject
        user = await _ensure_user(s, subject)
        for key in keys:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            s.add(scope)
            await s.flush()  # populate scope.id
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


async def test_create_objective_is_a_document_subtype_mapped_to_6_2(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    r = await app_client.post(
        "/api/v1/objectives",
        headers=h,
        json={
            "title": "Raise on-time delivery to 98%",
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
            "baseline_value": "90",
            "at_risk_threshold": "95",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["unit"] == "%"
    assert body["rag"] == "unmeasured"  # no reading yet
    assert body["identifier"].startswith("OBJ-")
    # the satellite row exists + the base is kind=DOCUMENT type OBJ
    async with get_sessionmaker()() as s:
        qo = (
            await s.execute(
                select(QualityObjective).where(QualityObjective.id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
        assert qo.target_value == 98


async def test_create_rejects_unknown_policy_id(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    r = await app_client.post(
        "/api/v1/objectives",
        headers=h,
        json={
            "title": "Bad policy link",
            "target_value": "5",
            "unit": "count",
            "direction": "LOWER_IS_BETTER",
            "due_date": "2026-12-31",
            "policy_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 422, r.text


async def test_record_measurements_roll_up_latest_period_wins(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    obj = (
        await app_client.post(
            "/api/v1/objectives",
            headers=h,
            json={
                "title": "Cut complaints to 5/mo",
                "target_value": "5",
                "unit": "count",
                "direction": "LOWER_IS_BETTER",
                "due_date": "2026-12-31",
                "at_risk_threshold": "10",
            },
        )
    ).json()
    oid = obj["id"]
    # two readings, out of order — current_value must reflect the LATEST period
    r1 = await app_client.post(
        f"/api/v1/objectives/{oid}/measurements",
        headers=h,
        json={"period": "2026-03-31", "value": "12", "unit": "count"},
    )
    assert r1.status_code == 201, r1.text
    r2 = await app_client.post(
        f"/api/v1/objectives/{oid}/measurements",
        headers=h,
        json={"period": "2026-06-30", "value": "8", "unit": "count"},
    )
    assert r2.status_code == 201, r2.text
    # insert an older period AFTER — must NOT clobber current_value
    await app_client.post(
        f"/api/v1/objectives/{oid}/measurements",
        headers=h,
        json={"period": "2026-01-31", "value": "20", "unit": "count"},
    )
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=h)).json()
    assert detail["current_value"] == "8"  # the 2026-06-30 reading
    assert detail["rag"] == "amber"  # 8 is between target 5 and threshold 10
    hist = (await app_client.get(f"/api/v1/objectives/{oid}/measurements", headers=h)).json()
    assert len(hist["data"]) == 3
    assert all(m["target_at_capture"] == "5" for m in hist["data"])  # frozen at capture
