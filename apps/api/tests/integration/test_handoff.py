"""S11 exit proof — the Avery→Mara handoff (doc 18 §12 'Avery→Mara handoff demoable').

Avery (System Administrator) invites Mara, grants her QMS Owner, and Mara then logs in and can
OPERATE (a QMS-Owner-gated action ALLOW) while being DENIED an admin-only action — proving the
persona hand-off + that ADMIN caps do not bleed into the content role. Fully CI-feasible: JWTs are
minted (no live Keycloak); the real browser OIDC login stays a documented manual proof
(install-online.md). Reuses the S8d invite/role-assign API + the S2 authz bundles (0004): QMS Owner
holds report.compliance_checklist.read but NOT user.read/user.create.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_vault import _auth

pytestmark = pytest.mark.integration


def _sub(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def test_avery_invites_mara_then_mara_operates(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    avery_sub, mara_sub = _sub("avery"), _sub("mara")
    await s5.grant_role(avery_sub, "System Administrator")
    h_avery = _auth(token_factory, avery_sub)

    # 1. Avery invites Mara (→ INVITED + USER_CREATED audit)
    invite = await app_client.post(
        "/api/v1/users",
        headers=h_avery,
        json={"keycloak_subject": mara_sub, "display_name": "Mara", "email": "mara@example.io"},
    )
    assert invite.status_code == 201, invite.text
    assert invite.json()["status"] == "INVITED"
    async with get_sessionmaker()() as s:
        created = await s.scalar(
            select(AuditEvent.id).where(AuditEvent.event_type == EventType.USER_CREATED)
        )
    assert created is not None

    roster = (await app_client.get("/api/v1/users", headers=h_avery)).json()
    mara_id = next(u["id"] for u in roster if u["keycloak_subject"] == mara_sub)

    # 2. Avery grants Mara QMS Owner (the reused S2 role-assign; two-tier guard does not fire)
    assigned = await app_client.post(
        f"/api/v1/users/{mara_id}/roles", headers=h_avery, json={"role_name": "QMS Owner"}
    )
    assert assigned.status_code == 201, assigned.text

    # 3. Mara logs in → INVITED reconciles to ACTIVE on first authenticated call
    h_mara = _auth(token_factory, mara_sub)
    me = await app_client.get("/api/v1/me", headers=h_mara)
    assert me.status_code == 200, me.text
    assert me.json()["status"] == "ACTIVE"

    # 4. Mara CAN operate — a QMS-Owner-gated read (report.compliance_checklist.read, 0004)
    checklist = await app_client.get("/api/v1/reports/compliance-checklist", headers=h_mara)
    assert checklist.status_code == 200, checklist.text

    # 5. Mara is DENIED admin-only actions — Avery's admin caps did not bleed into QMS Owner
    assert (await app_client.get("/api/v1/users", headers=h_mara)).status_code == 403
    forbidden_invite = await app_client.post(
        "/api/v1/users", headers=h_mara, json={"keycloak_subject": _sub("nope")}
    )
    assert forbidden_invite.status_code == 403
