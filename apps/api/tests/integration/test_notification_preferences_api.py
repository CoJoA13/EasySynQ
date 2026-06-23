"""GET/PUT /me/notification-preferences — full digest-matrix shape (S-notify-3a).

Tests the new preference API: effective defaults when no row exists, partial updates
with validation, and the email_enabled toggle. Uses the same app-client harness as
test_notification_api.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.app_user import AppUser, UserStatus
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
            keycloak_subject=f"kc-pref-api-{salt}",
            display_name=f"Pref API Test {salt}",
            email=f"pref-api-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user


def _auth(token_factory: Callable[..., str], subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_factory(subject)}"}


# Expected defaults when no preference row exists.
_EXPECTED_DEFAULTS = {
    "email_enabled": True,
    "digest_modes": {
        "action_required": "daily",
        "awareness": "daily",
        "critical": "immediate",
        "admin_ops": "immediate",
    },
    "digest_hour": 8,
    "timezone": "UTC",
    "quiet_start": None,
    "quiet_end": None,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_get_preferences_no_row_returns_effective_defaults(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """GET with no preference row returns the effective defaults."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"default-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["email_enabled"] is True
    assert data["digest_modes"] == _EXPECTED_DEFAULTS["digest_modes"]
    assert data["digest_hour"] == 8
    assert data["timezone"] == "UTC"
    assert data["quiet_start"] is None
    assert data["quiet_end"] is None


async def test_put_partial_update_and_get_reflects_changes(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT a partial update; GET reflects the changes; untouched fields stay at defaults."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"partial-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={
            "digest_modes": {"action_required": "immediate"},
            "digest_hour": 6,
            "timezone": "America/New_York",
            "quiet_start": "22:00",
            "quiet_end": "06:00",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["digest_modes"]["action_required"] == "immediate"
    assert data["digest_modes"]["awareness"] == "daily"  # untouched → still daily
    assert data["digest_hour"] == 6
    assert data["timezone"] == "America/New_York"
    assert data["quiet_start"] == "22:00"
    assert data["quiet_end"] == "06:00"

    # Follow-up GET reflects the stored values.
    r2 = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["digest_modes"]["action_required"] == "immediate"
    assert data2["digest_modes"]["awareness"] == "daily"
    assert data2["digest_hour"] == 6
    assert data2["timezone"] == "America/New_York"
    assert data2["quiet_start"] == "22:00"
    assert data2["quiet_end"] == "06:00"


async def test_put_invalid_digest_hour_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT {digest_hour: 99} → 422."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"hr422-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"digest_hour": 99},
    )
    assert r.status_code == 422, r.text


async def test_put_invalid_timezone_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT {timezone: "Not/AZone"} → 422."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"tz422-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"timezone": "Not/AZone"},
    )
    assert r.status_code == 422, r.text


async def test_put_invalid_digest_mode_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT {digest_modes: {action_required: "weekly"}} → 422 (mode not in enum)."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"mode422-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"digest_modes": {"action_required": "weekly"}},
    )
    assert r.status_code == 422, r.text


async def test_put_unknown_class_in_digest_modes_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT {digest_modes: {bogus_class: "daily"}} → 422 (unknown class key)."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"cls422-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"digest_modes": {"bogus_class": "daily"}},
    )
    assert r.status_code == 422, r.text


async def test_put_only_quiet_start_without_end_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT {quiet_start: "22:00"} (only one of the pair) → 422."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"qs422-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "22:00"},
    )
    assert r.status_code == 422, r.text


async def test_put_only_quiet_start_with_existing_hours_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """FIX A: a user with EXISTING quiet hours who PUTs only quiet_start → 422.

    Previously the handler read the stored end from pref.quiet_end and validated
    the pair (start_provided, stored_end) as complete → silently accepted a half-update.
    Now the check is based purely on what was PROVIDED in the request."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"existing-hours-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    # First set both quiet hours legitimately.
    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "22:00", "quiet_end": "06:00"},
    )
    assert r.status_code == 200, r.text

    # Now attempt to update only quiet_start (the previously-missed case) → must be 422.
    r2 = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "23:00"},
    )
    assert r2.status_code == 422, r2.text

    # GET must still reflect the original values (the half-update was rejected).
    r3 = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r3.status_code == 200, r3.text
    data3 = r3.json()
    assert data3["quiet_start"] == "22:00"
    assert data3["quiet_end"] == "06:00"


async def test_put_only_quiet_end_with_existing_hours_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """FIX A (symmetric): PUT only quiet_end on an existing-hours user → 422."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"existing-hours-end-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "22:00", "quiet_end": "06:00"},
    )
    assert r.status_code == 200, r.text

    r2 = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_end": "07:00"},
    )
    assert r2.status_code == 422, r2.text


async def test_put_both_quiet_hours_on_existing_user_updates(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """FIX A: PUT both quiet_start + quiet_end on an existing-hours user → 200 + updated values."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"update-hours-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    # Set initial quiet hours.
    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "22:00", "quiet_end": "06:00"},
    )
    assert r.status_code == 200, r.text

    # Update both → 200 + new values.
    r2 = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "23:00", "quiet_end": "07:00"},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["quiet_start"] == "23:00"
    assert data2["quiet_end"] == "07:00"

    r3 = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r3.json()["quiet_start"] == "23:00"
    assert r3.json()["quiet_end"] == "07:00"


async def test_put_both_quiet_hours_null_clears_existing(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """FIX A: PUT {quiet_start: null, quiet_end: null} clears existing quiet hours → 200."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"clear-hours-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "22:00", "quiet_end": "06:00"},
    )
    assert r.status_code == 200, r.text

    # Clear both by sending null/null → 200.
    r2 = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": None, "quiet_end": None},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["quiet_start"] is None
    assert data2["quiet_end"] is None

    r3 = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r3.json()["quiet_start"] is None
    assert r3.json()["quiet_end"] is None


async def test_put_quiet_start_empty_string_end_set_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """R2-1: {quiet_start: "", quiet_end: "06:00"} is a half-set request → 422.

    The prior fix treated empty-string as falsy (same as None) when assigning pref.quiet_start,
    so the stored value became NULL while quiet_end was set — a one-sided window in the DB.
    The new value-level check rejects this case before any assignment.
    """
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"empty-start-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "", "quiet_end": "06:00"},
    )
    assert r.status_code == 422, r.text


async def test_put_quiet_end_empty_string_start_set_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """R2-1 (symmetric): {quiet_start: "22:00", quiet_end: ""} → 422."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"empty-end-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "22:00", "quiet_end": ""},
    )
    assert r.status_code == 422, r.text


async def test_put_both_empty_strings_clears_quiet_hours(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """R2-1: {quiet_start: "", quiet_end: ""} is treated as clearing both → 200, null/null in DB."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"clear-empty-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    # First set both hours legitimately.
    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "22:00", "quiet_end": "06:00"},
    )
    assert r.status_code == 200, r.text

    # Clear both with empty strings — should be treated same as null/null → 200.
    r2 = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": "", "quiet_end": ""},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["quiet_start"] is None, f"Expected null, got {data2['quiet_start']!r}"
    assert data2["quiet_end"] is None, f"Expected null, got {data2['quiet_end']!r}"

    r3 = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r3.json()["quiet_start"] is None
    assert r3.json()["quiet_end"] is None


async def test_put_quiet_start_null_quiet_end_set_returns_422(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """R2-1: {quiet_start: null, quiet_end: "06:00"} is a half-set request → 422."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"null-start-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"quiet_start": None, "quiet_end": "06:00"},
    )
    assert r.status_code == 422, r.text


async def test_put_email_enabled_false_and_get_reflects(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT {email_enabled: false} → 200; GET reflects email_enabled False."""
    salt = uuid.uuid4().hex[:8]
    org_id = await _default_org_id()
    user = await _seed_user(org_id, f"email-{salt}")
    h = _auth(token_factory, user.keycloak_subject)

    r = await app_client.put(
        "/api/v1/me/notification-preferences",
        headers=h,
        json={"email_enabled": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["email_enabled"] is False

    r2 = await app_client.get("/api/v1/me/notification-preferences", headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.json()["email_enabled"] is False
