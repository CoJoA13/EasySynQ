"""S-register-steward (R52) integration proofs — Register Steward role beyond the Risk register.

Two proofs the risk-lifecycle headline does not cover:
1. The role reaches Context (4.1) + Interested Parties (4.2) stewardship — a role-only user sees
   ``can_manage`` True on both register-status reads (head-state independent: register.manage
   @ SYSTEM does not depend on the head's lifecycle state).
2. NON-REGRESSION: the role's ``document.release`` @ SYSTEM does NOT open a
   leadership-authorization bypass — with the org flag ON, a role-only steward releasing an
   Approved leadership artifact (OBJ) is still blocked by the Top-Management gate
   (409 leadership_authorization_required), NOT a missing grant. The steward is not in the
   Top-Management candidate pool.

Run-scoped (own ids); the org flag is flipped ON then reset OFF in a finally (shared session DB).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_leadership_authorization import _approved_obj, _set_leadership_flag
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_steward_role_reaches_context_and_ip_stewardship(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A user holding ONLY the Register Steward role can manage the Context (4.1) + Interested
    Parties (4.2) register heads: register.manage @ SYSTEM (from the role) → can_manage True on both
    status reads, with NO SYSTEM override. Head-state independent (no lifecycle driven), so it does
    not pollute the shared singleton heads."""
    subject = f"rs-cx-ip-{uuid.uuid4().hex[:8]}"
    await s5.grant_role(subject, "Register Steward")
    h = _auth(token_factory, subject)

    ctx = await app_client.get("/api/v1/context/register", headers=h)
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["can_manage"] is True

    ip = await app_client.get("/api/v1/interested-parties/register", headers=h)
    assert ip.status_code == 200, ip.text
    assert ip.json()["can_manage"] is True


async def test_steward_release_still_blocked_by_leadership_gate(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """NON-REGRESSION: granting document.release @ SYSTEM via the Register Steward role does NOT
    bypass the S-leadership-1 Top-Management gate. Drive an OBJ to Approved, flip the org flag ON,
    then have a role-only steward (≠ the OBJ author/approver, so SoD-2 does not fire) attempt the
    release: it is blocked with leadership_authorization_required (the steward got PAST the
    document.release authz — proving the role's grant works — and was stopped ONLY by the leadership
    preflight at the cutover chokepoint)."""
    org_id = await s5.default_org_id()
    salt = uuid.uuid4().hex[:8]
    oid, _hrq, _hrl = await _approved_obj(app_client, token_factory, salt)  # OBJ at Approved

    steward = f"rs-ld-{salt}"
    await s5.grant_role(steward, "Register Steward")  # ONLY the role — no override
    hrs = _auth(token_factory, steward)

    await _set_leadership_flag(org_id, True)
    try:
        blocked = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrs)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "leadership_authorization_required"
    finally:
        await _set_leadership_flag(org_id, False)
