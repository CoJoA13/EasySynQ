"""S-notify-5b: the admin notification-health endpoint gate (config.update)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from .test_capa import _grant  # SYSTEM-scope PermissionOverride grant helper
from .test_vault import _auth  # bearer-header builder

pytestmark = pytest.mark.integration


async def test_health_endpoint_returns_snapshot_for_admin(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"nh-admin-{uuid.uuid4().hex[:8]}"
    await _grant(subject, ("config.update",))
    r = await app_client.get(
        "/api/v1/admin/notifications/health", headers=_auth(token_factory, subject)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"org_email_enabled", "email", "recent_failures", "awareness"}
    assert set(body["email"]) == {
        "failed",
        "pending_now",
        "pending_scheduled",
        "suppressed",
        "oldest_pending_at",
    }
    assert isinstance(body["recent_failures"], list)
    assert set(body["awareness"]) == {"pending", "oldest_pending_at"}


async def test_health_endpoint_forbidden_without_config_update(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"nh-noperm-{uuid.uuid4().hex[:8]}"
    await _grant(subject, ("document.read",))  # exists, but lacks config.update
    r = await app_client.get(
        "/api/v1/admin/notifications/health", headers=_auth(token_factory, subject)
    )
    assert r.status_code == 403, r.text
