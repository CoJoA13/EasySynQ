"""Admin /admin/config notifications_email_enabled toggle — integration proof.

PATCH /admin/config {notifications_email_enabled: true} as an admin (config.update) →
  - 200 response with notifications_email_enabled: true
  - subsequent GET shows it true
  - a CONFIG_UPDATED AuditEvent was written (before=false, after=true)

Restores the flag to false after the test so the shared-DB config doesn't pollute others.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant the given permission keys at SYSTEM scope via override (the S2/test_audits pattern)."""
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


async def _config_updated_count_for_key(org_id: uuid.UUID, field: str) -> int:
    """Count CONFIG_UPDATED audit events for this org that mention the given field in `after`."""
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.org_id == org_id,
                    AuditEvent.object_type == AuditObjectType.config,
                    AuditEvent.event_type == EventType.CONFIG_UPDATED,
                    AuditEvent.after[field].astext.isnot(None),
                )
            )
        ).scalar_one()


async def test_notifications_email_enabled_toggle_and_audit(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """Enable notifications_email_enabled via PATCH, confirm via GET, assert CONFIG_UPDATED."""
    subject = _subject("notif-cfg")
    user_id = await _grant(subject, ("config.update",))
    h = _auth(token_factory, subject)

    # ---- GET: confirm default is false (fail-closed) ----
    r_get = await app_client.get("/api/v1/admin/config", headers=h)
    assert r_get.status_code == 200, r_get.text
    assert r_get.json()["notifications_email_enabled"] is False

    # ---- Capture audit count before the flip ----
    # Derive org_id from the user we just created
    async with get_sessionmaker()() as s:
        from easysynq_api.db.models.app_user import AppUser

        user = await s.get(AppUser, user_id)
        assert user is not None
        org_id = user.org_id

    before_count = await _config_updated_count_for_key(org_id, "notifications_email_enabled")

    # ---- PATCH: enable email notifications ----
    r_patch = await app_client.patch(
        "/api/v1/admin/config",
        headers=h,
        json={"notifications_email_enabled": True},
    )
    assert r_patch.status_code == 200, r_patch.text
    assert r_patch.json()["notifications_email_enabled"] is True

    # ---- GET: round-trip confirms the stored value ----
    r_get2 = await app_client.get("/api/v1/admin/config", headers=h)
    assert r_get2.status_code == 200, r_get2.text
    assert r_get2.json()["notifications_email_enabled"] is True

    # ---- Audit: exactly one new CONFIG_UPDATED row with this field ----
    after_count = await _config_updated_count_for_key(org_id, "notifications_email_enabled")
    assert after_count == before_count + 1, (
        f"Expected one new CONFIG_UPDATED audit row for notifications_email_enabled; "
        f"before={before_count}, after={after_count}"
    )

    # ---- Restore default (fail-closed) so the shared DB doesn't pollute other tests ----
    r_restore = await app_client.patch(
        "/api/v1/admin/config",
        headers=h,
        json={"notifications_email_enabled": False},
    )
    assert (
        r_restore.status_code == 200 and r_restore.json()["notifications_email_enabled"] is False
    ), r_restore.text
