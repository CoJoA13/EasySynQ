"""GET /notifications + mark-read + /me/notification-preferences — self-scope proofs.

Three tests:
1. GET /notifications returns only the caller's rows (user B's rows are excluded).
2. User B cannot mark user A's notification read (404 + A's row stays unread).
3. PUT /me/notification-preferences {email_enabled:false} upserts; GET round-trips the value.

Notifications are seeded directly (no dispatch) so the tests are self-contained.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.notification import Notification
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _seed_user(org_id: uuid.UUID, salt: str) -> AppUser:
    """Create a unique AppUser and return the ORM row (id populated)."""
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-notif-api-{salt}",
            display_name=f"Notif API Test {salt}",
            email=f"notif-api-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user


async def _seed_notification(org_id: uuid.UUID, recipient_user_id: uuid.UUID) -> uuid.UUID:
    """Insert a minimal Notification row and return its id."""
    async with get_sessionmaker()() as s:
        notif = Notification(
            org_id=org_id,
            recipient_user_id=recipient_user_id,
            event_key="task.assigned",
            subject_type="document",
            subject_id=None,
            title="You have a new task",
            body="A document requires your attention.",
            deep_link="/tasks",
        )
        s.add(notif)
        await s.commit()
        await s.refresh(notif)
        return notif.id


def _auth(token_factory: Callable[..., str], subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_factory(subject)}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_list_notifications_returns_only_callers_rows(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """GET /notifications returns the caller's rows and excludes other users' rows."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()

    user_a = await _seed_user(org_id, f"a-{salt}")
    user_b = await _seed_user(org_id, f"b-{salt}")

    notif_a_id = await _seed_notification(org_id, user_a.id)
    await _seed_notification(org_id, user_b.id)  # must not appear in A's listing

    ha = _auth(token_factory, user_a.keycloak_subject)
    r = await app_client.get("/api/v1/notifications", headers=ha)
    assert r.status_code == 200, r.text

    ids = {row["id"] for row in r.json()}
    assert str(notif_a_id) in ids, "A's notification missing from A's listing"
    # B's notification must not appear — verify by checking none of the returned ids
    # belong to user_b (we check directly rather than relying on counting).
    async with get_sessionmaker()() as s:
        b_ids = {
            str(row.id)
            for row in (
                await s.execute(
                    select(Notification).where(Notification.recipient_user_id == user_b.id)
                )
            )
            .scalars()
            .all()
        }
    assert not (ids & b_ids), "B's notification leaked into A's listing"


async def test_mark_read_cross_user_returns_404_and_row_stays_unread(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """User B cannot mark user A's notification read: 404 + the row stays unread."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()

    user_a = await _seed_user(org_id, f"a2-{salt}")
    user_b = await _seed_user(org_id, f"b2-{salt}")

    notif_a_id = await _seed_notification(org_id, user_a.id)

    hb = _auth(token_factory, user_b.keycloak_subject)
    r = await app_client.post(f"/api/v1/notifications/{notif_a_id}/read", headers=hb)
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

    # A's row must still be unread
    async with get_sessionmaker()() as s:
        notif = await s.get(Notification, notif_a_id)
        assert notif is not None
        assert notif.read_at is None, "A's notification was incorrectly marked read by B"


async def test_notification_preferences_upsert_and_roundtrip(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT /me/notification-preferences persists; GET returns the stored value."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"pref-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    # Default (no row) should be enabled=True
    r_default = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r_default.status_code == 200, r_default.text
    assert r_default.json()["email_enabled"] is True

    # Disable email
    r_put = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"email_enabled": False},
    )
    assert r_put.status_code == 200, r_put.text
    assert r_put.json()["email_enabled"] is False

    # GET must return the stored value
    r_get = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r_get.status_code == 200, r_get.text
    assert r_get.json()["email_enabled"] is False

    # Re-enable (idempotent upsert)
    r_put2 = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"email_enabled": True},
    )
    assert r_put2.status_code == 200, r_put2.text
    r_get2 = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r_get2.json()["email_enabled"] is True
