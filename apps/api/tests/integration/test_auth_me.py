"""S1: /me resolves a validated Keycloak token to an app_user (JIT-provisioned)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.auth.dependencies import _jit_provision_user
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker

pytestmark = pytest.mark.integration


async def test_me_jit_provisions_and_reuses_app_user(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    sub = f"kc-{uuid.uuid4()}"
    headers = {"Authorization": f"Bearer {token_factory(sub)}"}

    first = await app_client.get("/api/v1/me", headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["keycloak_subject"] == sub
    assert body["status"] == "ACTIVE"
    assert body["display_name"] == "Test User"
    assert body["is_guest"] is False

    # a second call with a fresh token for the same subject reuses the same row
    second = await app_client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {token_factory(sub)}"}
    )
    assert second.status_code == 200
    assert second.json()["id"] == body["id"]


async def test_concurrent_jit_provisioning_converges_on_one_user(
    app_under_test: object,
) -> None:
    subject = f"kc-race-{uuid.uuid4()}"
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        org_id = (
            await session.execute(
                select(Organization.id).order_by(Organization.created_at).limit(1)
            )
        ).scalar_one()

    async def _provision() -> AppUser:
        async with sessionmaker() as session:
            return await _jit_provision_user(
                session,
                org_id=org_id,
                subject=subject,
                display_name="Concurrent user",
                email=None,
            )

    first, second = await asyncio.gather(_provision(), _provision())

    assert first.id == second.id
    async with sessionmaker() as session:
        rows = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == subject))
        ).scalars()
        assert len(rows.all()) == 1


async def test_me_without_token_is_401(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/v1/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthenticated"


async def test_auth_config_is_public(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    assert resp.json()["client_id"] == "easysynq-web"


async def test_color_scheme_defaults_to_auto_on_a_freshly_provisioned_user(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A new account follows the operating system, exactly as the SPA did before R69."""
    sub = f"kc-{uuid.uuid4()}"
    body = (
        await app_client.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {token_factory(sub)}"}
        )
    ).json()
    assert body["color_scheme"] == "AUTO"


async def test_preference_update_persists_to_the_account_across_a_new_token(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The whole point of R69's account-level choice, so it is proven end-to-end.

    The re-read uses a FRESH token rather than the same one, because that is the case a
    browser-only preference cannot serve: the SPA holds tokens in memory, so every reload
    re-authenticates. Reading it back on the same token would pass against a purely in-request
    change that never reached the database.
    """
    sub = f"kc-{uuid.uuid4()}"
    patched = await app_client.patch(
        "/api/v1/me/preferences",
        headers={"Authorization": f"Bearer {token_factory(sub)}"},
        json={"color_scheme": "DARK"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["color_scheme"] == "DARK"

    reread = await app_client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {token_factory(sub)}"}
    )
    assert reread.status_code == 200
    assert reread.json()["color_scheme"] == "DARK"


async def test_auto_is_reachable_again_after_choosing_a_fixed_scheme(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """R69 keeps AUTO a real selectable value, not merely the initial one.

    Load-bearing: a two-state design would leave a user who once picked DARK unable to return to
    OS-following, and nothing else in the suite would notice.
    """
    sub = f"kc-{uuid.uuid4()}"
    headers = {"Authorization": f"Bearer {token_factory(sub)}"}
    await app_client.patch("/api/v1/me/preferences", headers=headers, json={"color_scheme": "DARK"})
    back = await app_client.patch(
        "/api/v1/me/preferences", headers=headers, json={"color_scheme": "AUTO"}
    )
    assert back.status_code == 200, back.text
    assert back.json()["color_scheme"] == "AUTO"


async def test_empty_preference_body_is_a_no_op_not_a_reset(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """`None` means "not supplied". A partial update that reset omitted fields would silently
    revert a user's scheme whenever a future preference is sent on its own."""
    sub = f"kc-{uuid.uuid4()}"
    headers = {"Authorization": f"Bearer {token_factory(sub)}"}
    await app_client.patch(
        "/api/v1/me/preferences", headers=headers, json={"color_scheme": "LIGHT"}
    )
    noop = await app_client.patch("/api/v1/me/preferences", headers=headers, json={})
    assert noop.status_code == 200, noop.text
    assert noop.json()["color_scheme"] == "LIGHT"


async def test_preference_update_rejects_a_bad_value_and_an_unknown_key(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A misspelled key must be a 422, never a silent no-op — `additionalProperties: false` in the
    contract is what makes `{"colour_scheme": "DARK"}` fail loudly instead of appearing to work."""
    headers = {"Authorization": f"Bearer {token_factory(f'kc-{uuid.uuid4()}')}"}
    assert (
        await app_client.patch(
            "/api/v1/me/preferences", headers=headers, json={"color_scheme": "SEPIA"}
        )
    ).status_code == 422
    assert (
        await app_client.patch(
            "/api/v1/me/preferences", headers=headers, json={"colour_scheme": "DARK"}
        )
    ).status_code == 422


async def test_preference_update_requires_authentication(app_client: AsyncClient) -> None:
    unauth = await app_client.patch("/api/v1/me/preferences", json={"color_scheme": "DARK"})
    assert unauth.status_code == 401


async def test_preference_update_cannot_reach_another_account(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The route takes no user id — the target is always `get_current_user`, which is why it needs
    no permission key. Proven rather than asserted: a second account is untouched by the first's
    write, so the authentication-only design cannot quietly become an admin surface."""
    other = f"kc-{uuid.uuid4()}"
    other_headers = {"Authorization": f"Bearer {token_factory(other)}"}
    assert (await app_client.get("/api/v1/me", headers=other_headers)).json()[
        "color_scheme"
    ] == "AUTO"

    mine = f"kc-{uuid.uuid4()}"
    await app_client.patch(
        "/api/v1/me/preferences",
        headers={"Authorization": f"Bearer {token_factory(mine)}"},
        json={"color_scheme": "DARK"},
    )

    assert (await app_client.get("/api/v1/me", headers=other_headers)).json()[
        "color_scheme"
    ] == "AUTO"
