"""Cross-cutting notification integration proofs (S-notify-1, spec §4/§5/§6).

Four tests that prove the multi-component contracts the per-task tests don't fully cover:

1. e2e: org flag ON + user with email → submit-review → PENDING email row → drain_once →
   SENT + fake recorded the send.
2. SKIP-LOCKED no double-send: two concurrent drain_once calls on two separate sessions →
   fake called exactly once; row ends SENT with attempts == 1.
3. Delivery-failure ownership: exhausted PENDING row → drain_once → FAILED +
   system.email_delivery_failed Notification to admin, body contains NO subject metadata.
4. No-leak render: render system.email_delivery_failed with a bogus subject.title →
   output does NOT contain the value (whitelist drops it).

S-notify-3a update: action_required defaults to DAILY. Test 1 sets the approver's mode to
IMMEDIATE so a NotificationEmail row is created at enqueue (not held for a digest sweep).
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Callable
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.config import Settings, get_settings
from easysynq_api.db.models._notification_enums import (
    NotificationDigestMode,
    NotificationEmailStatus,
)
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.notification import (
    Notification,
    NotificationEmail,
    NotificationPreference,
)
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.constants import EVENT_EMAIL_DELIVERY_FAILED
from easysynq_api.services.notifications.drain import drain_once
from easysynq_api.services.notifications.mail import FakeMailSender
from easysynq_api.services.notifications.render import render

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _map_clause, _upload

pytestmark = pytest.mark.integration

_T0 = datetime.datetime(2030, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _configured_settings() -> Settings:
    """Return settings with a non-empty smtp_host so the drain proceeds past the at-send
    eligibility re-check (the testcontainer env does not set SMTP_HOST, so get_settings()
    yields smtp_host="" → every row would be SUPPRESSED). Mirrors test_notification_drain.py."""
    base = get_settings()
    return Settings(**{**base.model_dump(), "smtp_host": "test-smtp.local"})


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _seed_user_with_email(org_id: uuid.UUID, salt: str) -> uuid.UUID:
    """Seed an AppUser with an email address."""
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"e2e-user-{salt}",
            display_name=f"E2E User {salt}",
            email=f"e2e-user-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _seed_admin(org_id: uuid.UUID, salt: str) -> uuid.UUID:
    """Seed a System-Administrator user (needed for the FAILED emit path)."""
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"e2e-admin-{salt}",
            display_name=f"E2E Admin {salt}",
            email=f"e2e-admin-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.flush()
        role = (
            await s.execute(
                select(Role).where(Role.org_id == org_id, Role.name == "System Administrator")
            )
        ).scalar_one()
        s.add(
            RoleAssignment(
                org_id=org_id,
                user_id=user.id,
                role_id=role.id,
                bound_scope={"level": "SYSTEM"},
            )
        )
        await s.commit()
        return user.id


async def _seed_email_row(
    org_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
    salt: str,
    *,
    attempts: int = 0,
) -> uuid.UUID:
    """Seed one Notification + one NotificationEmail(PENDING) and return the email row id."""
    async with get_sessionmaker()() as s:
        note = Notification(
            org_id=org_id,
            recipient_user_id=recipient_user_id,
            event_key="task.assigned",
            subject_type="DOCUMENT",
            subject_id=None,
            task_id=None,
            title=f"Seed {salt}",
            body=f"Body {salt}",
            deep_link="/test",
        )
        s.add(note)
        await s.flush()
        email = NotificationEmail(
            org_id=org_id,
            notification_id=note.id,
            recipient_email=f"e2e-user-{salt}@example.com",
            subject=f"Subject {salt}",
            body=f"Body {salt}",
            attempts=attempts,
        )
        s.add(email)
        await s.commit()
        return email.id


async def _get_email(email_id: uuid.UUID) -> NotificationEmail:
    async with get_sessionmaker()() as s:
        row = await s.get(NotificationEmail, email_id)
        assert row is not None
        return row


async def _set_org_email_flag(org_id: uuid.UUID, *, enabled: bool) -> None:
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        if cfg is not None:
            cfg.notifications_email_enabled = enabled
            await s.commit()


# ---------------------------------------------------------------------------
# Proof 1: full e2e — org flag ON + user with email → submit-review → PENDING
#          email row → drain_once → SENT, fake recorded.
# ---------------------------------------------------------------------------


async def test_e2e_submit_review_enqueues_email_and_drain_sends(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    app_under_test: Any,
) -> None:
    """org flag ON + approver has email + IMMEDIATE mode → submit-review creates a PENDING email row
    for the approver → drain_once sends it (SENT, fake recorded).

    S-notify-3a: action_required defaults to DAILY; set IMMEDIATE on the approver's pref so an
    email row is written at enqueue (not deferred to the digest sweep).
    """
    salt = uuid.uuid4().hex[:8]
    author_kc = f"e2e-author-{salt}"
    approver_kc = f"e2e-approver-{salt}"

    # Grant lifecycle permissions to both actors.
    await s5.grant_lifecycle(author_kc)
    await s5.grant_lifecycle(approver_kc)
    # Grant the Approver role so they populate the task's candidate_pool.
    approver_user_id = await s5.grant_role(approver_kc, "Approver")

    # Set the approver's email on their app_user row.
    approver_email = f"approver-{salt}@example.com"
    async with get_sessionmaker()() as s:
        approver = await s.get(AppUser, approver_user_id)
        assert approver is not None
        approver.email = approver_email
        await s.commit()

    # S-notify-3a: action_required defaults to DAILY; set IMMEDIATE so an email row is
    # created immediately at enqueue (not held for a digest sweep).
    async with get_sessionmaker()() as s:
        pref = await s.get(NotificationPreference, approver_user_id)
        if pref is None:
            pref = NotificationPreference(user_id=approver_user_id)
            s.add(pref)
        pref.digest_mode_action_required = NotificationDigestMode.IMMEDIATE
        await s.commit()

    org_id = await s5.default_org_id()
    await _set_org_email_flag(org_id, enabled=True)

    h_author = _auth(token_factory, author_kc)
    type_id = await s5.type_id("SOP")

    # Count email rows BEFORE the submit-review (delta-based; shared DB).
    async with get_sessionmaker()() as s:
        before_count: int = (
            await s.execute(
                sa.select(sa.func.count())
                .select_from(NotificationEmail)
                .join(Notification, NotificationEmail.notification_id == Notification.id)
                .where(Notification.org_id == org_id)
            )
        ).scalar_one()

    # Submit the document for review (drives Draft → InReview).
    did = (await _create(app_client, h_author, type_id))["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h_author)
    sha = await _upload(app_client, h_author, did, f"e2e-content-{salt}".encode())
    ci = await _checkin(
        app_client, h_author, did, sha, change_reason="v1", change_significance="MAJOR"
    )
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, h_author, did)
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=h_author)
    assert sr.status_code == 200, sr.text

    # A PENDING notification_email row must now exist for the approver (delta ≥ 1).
    async with get_sessionmaker()() as s:
        after_count: int = (
            await s.execute(
                sa.select(sa.func.count())
                .select_from(NotificationEmail)
                .join(Notification, NotificationEmail.notification_id == Notification.id)
                .where(Notification.org_id == org_id)
            )
        ).scalar_one()

    new_rows = after_count - before_count
    assert new_rows >= 1, f"Expected ≥1 new email row after submit-review, got {new_rows}"

    # Fetch this document's email row(s) for the approver to confirm PENDING.
    async with get_sessionmaker()() as s:
        pending_for_approver = (
            (
                await s.execute(
                    select(NotificationEmail)
                    .join(Notification, NotificationEmail.notification_id == Notification.id)
                    .where(
                        Notification.recipient_user_id == approver_user_id,
                        NotificationEmail.status == NotificationEmailStatus.PENDING,
                        NotificationEmail.recipient_email == approver_email,
                    )
                    .order_by(NotificationEmail.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .all()
        )

    assert len(pending_for_approver) >= 1, "Expected a PENDING email row for the approver"
    email_row_id = pending_for_approver[0].id

    # drain_once → row transitions to SENT; fake records the send.
    sender = FakeMailSender()
    settings = _configured_settings()  # non-empty smtp_host → drain proceeds (CI has no SMTP_HOST)
    async with get_sessionmaker()() as session:
        await drain_once(session, sender, settings, now=_T0)

    row = await _get_email(email_row_id)
    assert row.status == NotificationEmailStatus.SENT, f"Expected SENT, got {row.status}"
    assert row.sent_at is not None
    assert any(m.to == approver_email for m in sender.sent), (
        f"Fake sender did not record an email to {approver_email}; sent={sender.sent}"
    )


# ---------------------------------------------------------------------------
# Proof 2: SKIP-LOCKED no double-send — two concurrent drain_once calls on
#          two separate sessions; fake called exactly once; row SENT, attempts==1.
# ---------------------------------------------------------------------------


async def test_skip_locked_no_double_send(app_under_test: Any) -> None:
    """Two concurrent drain_once calls on separate DB connections → exactly one send.

    Uses asyncio.gather with two distinct get_sessionmaker()() instances so the
    FOR UPDATE SKIP LOCKED is a genuine cross-connection race.
    """
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user_with_email(org_id, salt)  # ACTIVE, no opt-out row → eligible
    email_id = await _seed_email_row(org_id, user_id, salt)
    await _set_org_email_flag(org_id, enabled=True)  # org flag ON → passes at-send re-check

    settings = _configured_settings()  # non-empty smtp_host → drain proceeds (CI has no SMTP_HOST)

    # Two INDEPENDENT sessions/connections — this is the crucial part: separate instances
    # so SKIP LOCKED applies across real connections (not the same in-process session).
    shared_sender = FakeMailSender()

    async def _run_drain() -> dict[str, int]:
        async with get_sessionmaker()() as session:
            return await drain_once(session, shared_sender, settings, now=_T0)

    results = await asyncio.gather(_run_drain(), _run_drain())
    total_sent = sum(r["sent"] for r in results)

    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.SENT, "Row not SENT after concurrent drain"

    # The fake must have been called exactly once — SKIP LOCKED ensures the second
    # concurrent drain skips the row (it's either locked or already consumed).
    recipient_addr = f"e2e-user-{salt}@example.com"
    emails_to_recipient = [m for m in shared_sender.sent if m.to == recipient_addr]
    n_sent = len(emails_to_recipient)
    assert n_sent == 1, f"Expected exactly 1 send to recipient, got {n_sent}: {emails_to_recipient}"
    assert row.attempts == 1, f"Expected attempts==1 (sent once), got {row.attempts}"
    assert total_sent == 1, f"Expected combined sent count==1, got {total_sent}"


# ---------------------------------------------------------------------------
# Proof 3: delivery-failure ownership — exhausted PENDING row → FAILED +
#          system.email_delivery_failed Notification to admin, body has NO
#          subject identifier / title.
# ---------------------------------------------------------------------------


async def test_delivery_failure_emits_admin_notification_without_subject_metadata(
    app_under_test: Any,
) -> None:
    """An exhausted row (attempts == MAX) → FAILED + admin system notification.

    The admin notification body must contain NO document identifier or title text —
    the system.email_delivery_failed whitelist is operational-only (refutes L3-1).
    """
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]

    user_id = await _seed_user_with_email(org_id, salt)  # ACTIVE, no opt-out row → eligible
    admin_id = await _seed_admin(org_id, salt)
    # The at-send re-check must pass so the drain reaches the attempts>=MAX → FAILED path
    # (not SUPPRESSED): org flag ON + configured smtp + active recipient + no opt-out.
    await _set_org_email_flag(org_id, enabled=True)

    settings = _configured_settings()  # non-empty smtp_host → drain proceeds (CI has no SMTP_HOST)
    max_attempts = settings.notification_max_send_attempts

    # Seed a row already at MAX (so the very first drain look sees attempts >= MAX).
    email_id = await _seed_email_row(org_id, user_id, salt, attempts=max_attempts)

    sender = FakeMailSender()
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, settings, now=_T0)

    assert counts["failed"] >= 1

    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.FAILED
    assert row.failed_at is not None

    # The system.email_delivery_failed Notification row must exist for the admin.
    async with get_sessionmaker()() as s:
        admin_notes = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.org_id == org_id,
                        Notification.recipient_user_id == admin_id,
                        Notification.event_key == EVENT_EMAIL_DELIVERY_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(admin_notes) >= 1, "Expected a system.email_delivery_failed notification to admin"

    # The body must contain NO subject document identifier or title text.
    # The seed notification used salt-based title/body — assert neither appears in the admin note.
    admin_note = admin_notes[-1]  # most recent
    fake_doc_identifier = f"Seed {salt}"  # the title used when seeding (no real doc identifier)
    assert fake_doc_identifier not in admin_note.body, (
        f"Admin notification body must not contain subject title; found '{fake_doc_identifier}'"
    )
    # Also assert neither the recipient user's email-address-like salt identifier appears
    # in a way that identifies the document (the subject_id is None in our seeded note,
    # but the body template must only reference operational vars).
    # The whitelist only allows: recipient_email, attempts, last_error, notification_id, created_at.
    # Confirm the admin body does NOT contain the notification title text of the original note.
    assert "Seed" not in admin_note.body, (
        "Admin notification body must not contain document subject title text"
    )

    # No email row should be created for the admin system notification (operational-only, in-app).
    async with get_sessionmaker()() as s:
        admin_email_count: int = (
            await s.execute(
                sa.select(sa.func.count())
                .select_from(NotificationEmail)
                .join(Notification, NotificationEmail.notification_id == Notification.id)
                .where(
                    Notification.recipient_user_id == admin_id,
                    Notification.event_key == EVENT_EMAIL_DELIVERY_FAILED,
                )
            )
        ).scalar_one()
    assert admin_email_count == 0, (
        f"system.email_delivery_failed must be in-app only; got {admin_email_count} email rows"
    )


# ---------------------------------------------------------------------------
# Proof 4: no-leak render — render system.email_delivery_failed with a bogus
#          subject.title → output does NOT contain the value.
# ---------------------------------------------------------------------------


async def test_no_leak_render_drops_non_whitelisted_vars(app_under_test: Any) -> None:
    """render(system.email_delivery_failed, {... subject.title: 'SECRET' ...}) must NOT
    produce output containing 'SECRET' — the whitelist drops non-allowed variables."""
    bogus_secret = "SECRET_DOC_TITLE_MUST_NOT_LEAK"

    # Build a context that includes the allowed vars PLUS the non-whitelisted subject.title.
    context: dict[str, object] = {
        "recipient_email": "victim@example.com",
        "attempts": 5,
        "last_error": "connection refused",
        "notification_id": str(uuid.uuid4()),
        "created_at": _T0.isoformat(),
        # Non-whitelisted keys — must be silently dropped by the whitelist:
        "subject.title": bogus_secret,
        "subject.identifier": "DOC-SECRET-001",
    }

    async with get_sessionmaker()() as session:
        forms = await render(session, EVENT_EMAIL_DELIVERY_FAILED, context)

    assert forms is not None, (
        "render returned None — is the system.email_delivery_failed template seeded?"
    )

    for field_name, rendered_text in (
        ("in_app_title", forms.in_app_title),
        ("in_app_body", forms.in_app_body),
        ("email_subject", forms.email_subject),
        ("email_body", forms.email_body),
    ):
        assert bogus_secret not in rendered_text, (
            f"Non-whitelisted 'subject.title' value leaked into {field_name}: {rendered_text!r}"
        )
        identifier_secret = "DOC-SECRET-001"
        assert identifier_secret not in rendered_text, (
            f"Non-whitelisted 'subject.identifier' leaked into {field_name}: {rendered_text!r}"
        )
