"""S-web-3: the per-document ``capabilities`` block on GET /documents/{id} (DP-6).

The authz answer per authoring key against the document's real scope — detail-only (never on the
list), and ``release`` reflects the version-relative SoD-2 (the author of the latest Approved
version is HARD-denied release; a neutral third holder is allowed).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import _auth, _create, _ensure_user

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-a-{salt}", b=f"kc-b-{salt}", c=f"kc-c-{salt}")


async def _grant_keys_system(subject: str, keys: list[str]) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in keys:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            s.add(scope)
            await s.flush()
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


async def test_capabilities_reflect_the_granted_subset(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A user granted create/read/checkout/edit/submit/read_draft but NOT manage_metadata/release/
    obsolete gets exactly that capability map — the authz answer per key."""
    await _grant_keys_system(
        subj.a,
        [
            "document.create",
            "document.read",
            "document.checkout",
            "document.edit",
            "document.submit",
            "document.read_draft",
        ],
    )
    ha = _auth(token_factory, subj.a)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]

    detail = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()
    assert detail["capabilities"] == {
        "checkout": True,
        "edit": True,
        "manage_metadata": False,
        "submit": True,
        "release": False,
        "obsolete": False,
        "read_draft": True,
    }


async def test_capabilities_absent_on_list(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """capabilities is a DETAIL-only block — never on the list rows (O(rows*keys) authz)."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]
    owner = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()["owner_user_id"]
    rows = (
        await app_client.get(
            f"/api/v1/documents?limit=100&filter[owner_user_id][eq]={owner}", headers=ha
        )
    ).json()["data"]
    row = next(d for d in rows if d["id"] == did)
    assert "capabilities" not in row


async def test_capabilities_release_blocked_by_sod(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """``release`` reflects the version-relative SoD-2: the AUTHOR of the latest Approved version is
    HARD-denied release even holding ``document.release``; the prior APPROVER is denied too (with
    ``allow_approver_release`` off); a neutral third holder is allowed."""
    # Pin allow_approver_release OFF for this org (shared DB — another test may have set it True).
    await s5.set_approver_release(await s5.default_org_id(), False)
    await s5.grant_lifecycle(subj.a)  # author
    await s5.grant_lifecycle(subj.b)  # approver
    await s5.grant_lifecycle(subj.c)  # neutral third releaser
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hc = _auth(token_factory, subj.c)
    did = await s5.drive_to_approved(
        app_client, ha, hb, await s5.type_id("SOP"), f"caps-{subj.a}".encode()
    )

    # author A — SoD-2 author-side block (unconditional) → release False
    caps_a = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()["capabilities"]
    assert caps_a["release"] is False

    # neutral C — holds document.release, neither author nor approver → release True
    caps_c = (await app_client.get(f"/api/v1/documents/{did}", headers=hc)).json()["capabilities"]
    assert caps_c["release"] is True

    # approver B — SoD-2 approver-side block (allow_approver_release off) → release False
    caps_b = (await app_client.get(f"/api/v1/documents/{did}", headers=hb)).json()["capabilities"]
    assert caps_b["release"] is False
