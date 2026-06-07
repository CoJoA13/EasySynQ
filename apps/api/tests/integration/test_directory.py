"""S-web-2 integration proofs — GET /directory/users (the friendly Owner column / facet source).

Minimal disclosure: returns ONLY {id, display_name} (never the PII the admin GET /users exposes:
email/keycloak_subject/status/roles), for ACTIVE non-guest members only. Authentication-only. The
assertions are run-scoped to users this test creates.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from easysynq_api.db.models.app_user import UserStatus
from easysynq_api.db.session import get_sessionmaker

from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration


async def _make_user(subject: str, *, status: UserStatus, is_guest: bool, display_name: str) -> str:
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        user.status = status
        user.is_guest = is_guest
        user.display_name = display_name
        await s.commit()
        return str(user.id)


async def test_directory_minimal_shape_and_includes_self(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    h = _auth(token_factory, f"kc-dir-{uuid.uuid4().hex[:10]}")
    me = (await app_client.get("/api/v1/me", headers=h)).json()
    r = await app_client.get("/api/v1/directory/users", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list) and rows
    # Minimal disclosure: ONLY id + display_name — never PII fields.
    for row in rows:
        assert set(row) == {"id", "display_name"}
    # The caller (ACTIVE, non-guest after JIT) appears.
    assert me["id"] in {row["id"] for row in rows}


async def test_directory_excludes_guest_and_disabled(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:10]
    active_id = await _make_user(
        f"kc-dir-active-{salt}",
        status=UserStatus.ACTIVE,
        is_guest=False,
        display_name=f"Active {salt}",
    )
    guest_id = await _make_user(
        f"kc-dir-guest-{salt}",
        status=UserStatus.ACTIVE,
        is_guest=True,
        display_name=f"Guest {salt}",
    )
    disabled_id = await _make_user(
        f"kc-dir-disabled-{salt}",
        status=UserStatus.DISABLED,
        is_guest=False,
        display_name=f"Disabled {salt}",
    )

    h = _auth(token_factory, f"kc-dir-caller-{salt}")
    r = await app_client.get("/api/v1/directory/users", headers=h)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert active_id in ids  # ACTIVE non-guest → included
    assert guest_id not in ids  # guest → excluded
    assert disabled_id not in ids  # disabled → excluded


async def test_directory_requires_auth(app_client: AsyncClient) -> None:
    r = await app_client.get("/api/v1/directory/users")
    assert r.status_code == 401, r.text
