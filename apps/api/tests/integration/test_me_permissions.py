"""S-web-3: GET /me/permissions — the self-scoped affordance endpoint (DP-6).

Authentication-only (NOT ``user.read``-gated, unlike the admin
``/users/{id}/effective-permissions``), so an ordinary author can discover their own affordances;
the optional ``scope_level``/``scope_id`` refine the answer (a FOLDER grant is absent from the
default SYSTEM resolution but present when resolved at that FOLDER).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration


async def _grant_keys(
    subject: str,
    keys: list[str],
    *,
    level: ScopeLevel = ScopeLevel.SYSTEM,
    selector: dict[str, str] | None = None,
) -> uuid.UUID:
    """Grant ``keys`` to ``subject`` via per-key ALLOW overrides at ``level`` (the s5 pattern)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in keys:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=level, selector=selector)
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


async def test_me_permissions_requires_auth(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/v1/me/permissions")
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "unauthenticated"


async def test_me_permissions_self_scoped_not_admin_gated(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An ordinary content user — who does NOT hold ``user.read`` — can read their OWN affordances
    (the admin ``/users/{id}/effective-permissions`` would 403 them). Proves self-scoped, not
    ``user.read``-gated."""
    subject = f"kc-author-{uuid.uuid4().hex[:10]}"
    await s5.grant_lifecycle(subject)  # the document.* lifecycle set — notably NOT user.read
    h = _auth(token_factory, subject)

    resp = await app_client.get("/api/v1/me/permissions", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"]["level"] == "SYSTEM"
    by_key = {p["key"]: p for p in body["permissions"]}
    # the granted lifecycle keys resolve ALLOW at SYSTEM (a SYSTEM override matches every resource)
    assert by_key["document.create"]["effect"] == "ALLOW"
    assert by_key["document.submit"]["effect"] == "ALLOW"
    assert set(by_key["document.create"]) == {"key", "effect", "source"}
    # the caller genuinely lacks user.read yet still got a 200 → not the admin gate
    assert "user.read" not in by_key


async def test_me_permissions_scope_param_refines_answer(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A FOLDER-scoped grant is absent from the default SYSTEM answer but present when the query
    resolves at that FOLDER — proving ``scope_level``/``scope_id`` refine the result."""
    subject = f"kc-folder-{uuid.uuid4().hex[:10]}"
    folder = f"sweb3.{uuid.uuid4().hex[:8]}"
    await _grant_keys(
        subject, ["document.read"], level=ScopeLevel.FOLDER, selector={"folder_path": folder}
    )
    h = _auth(token_factory, subject)

    sysq = (await app_client.get("/api/v1/me/permissions", headers=h)).json()
    # a FOLDER grant does not resolve ALLOW at SYSTEM
    assert "document.read" not in {p["key"] for p in sysq["permissions"]}

    folderq = (
        await app_client.get(
            f"/api/v1/me/permissions?scope_level=FOLDER&scope_id={folder}", headers=h
        )
    ).json()
    assert folderq["scope"]["level"] == "FOLDER"
    by_key = {p["key"]: p for p in folderq["permissions"]}
    assert by_key["document.read"]["effect"] == "ALLOW"
