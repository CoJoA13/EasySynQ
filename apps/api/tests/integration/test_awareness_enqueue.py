"""enqueue_awareness_one — in-app row created; version-discriminated dedup; re-release re-notifies.

Tests run against a real migrated PG16 via testcontainers. The migration (0066) seeds the
``doc.released`` template, so no manual template row is needed.

Three cases:
1. First call: ``created`` — in-app row with task_id=NULL, subject_version_id set.
   org_enabled=False → no email row (AWARENESS defaults to DAILY; digest_due_at=None when off).
2. Second call with the SAME subject_version_id: ``deduped`` — the awareness dedup index fires.
3. Third call with a DIFFERENT subject_version_id (re-release): ``created`` — 2 distinct rows.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixture: one org + one active recipient
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AwarenessFixture:
    org_id: uuid.UUID
    user_id: uuid.UUID
    doc_id: uuid.UUID
    recipient: Any  # Recipient dataclass from services/notifications/recipients.py


@pytest.fixture
async def awareness_fix(app_under_test: Any) -> AwarenessFixture:  # type: ignore[misc]
    """Seed one org (reuse seeded DEFAULT org) + one ACTIVE AppUser + a Recipient."""
    from easysynq_api.db.models.app_user import AppUser, UserStatus
    from easysynq_api.db.models.organization import Organization
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.recipients import Recipient

    async with get_sessionmaker()() as s:
        org_id: uuid.UUID = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()

    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-awareness-{salt}",
            display_name=f"Awareness Test {salt}",
            email=f"awareness-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        user_id = user.id

    doc_id = uuid.uuid4()  # phantom doc id — no FK constraint on subject_id

    recipient = Recipient(
        user_id=user_id,
        email=f"awareness-{salt}@example.com",
        display_name=f"Awareness Test {salt}",
        first_name="Awareness",
        email_enabled=True,
    )

    yield AwarenessFixture(org_id=org_id, user_id=user_id, doc_id=doc_id, recipient=recipient)
    # No teardown: notification rows REVOKE DELETE prevents app-role deletion (S-notify-1 pattern).
    # The user is unique per run (keycloak_subject salt) + the RESTRICT FK from notification keeps
    # it alive — mirrors the approach in test_notification_dispatch.py (no post-test cleanup).


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_enqueue_awareness_created_then_deduped_then_reversion(
    app_under_test: Any, awareness_fix: AwarenessFixture
) -> None:
    """created → deduped on same version → created on new version (2 rows, both task_id=NULL)."""
    from easysynq_api.db.models.notification import Notification
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.dispatch import enqueue_awareness_one
    from easysynq_api.services.notifications.subjects import SubjectInfo

    now = datetime.datetime.now(datetime.UTC)
    subj = SubjectInfo(
        identifier="SOP-1", title="A", kind="DOCUMENT", deep_link="http://x/documents/1"
    )
    v1, v2 = uuid.uuid4(), uuid.uuid4()

    # --- Call 1: first enqueue for v1 → created
    async with get_sessionmaker()() as session:
        o1 = await enqueue_awareness_one(
            session,
            org_id=awareness_fix.org_id,
            subject=subj,
            subject_id=awareness_fix.doc_id,
            subject_version_id=v1,
            recipient=awareness_fix.recipient,
            event_key="doc.released",
            context_vars={"version.label": "1.0"},
            now=now,
            org_enabled=False,
            org_pierce=False,
        )
        await session.commit()
    assert o1 == "created"

    # --- Call 2: same version → deduped
    async with get_sessionmaker()() as session:
        o2 = await enqueue_awareness_one(
            session,
            org_id=awareness_fix.org_id,
            subject=subj,
            subject_id=awareness_fix.doc_id,
            subject_version_id=v1,
            recipient=awareness_fix.recipient,
            event_key="doc.released",
            context_vars={"version.label": "1.0"},
            now=now,
            org_enabled=False,
            org_pierce=False,
        )
        await session.commit()
    assert o2 == "deduped"  # same version → suppressed

    # --- Call 3: new version → created (re-release re-notifies)
    async with get_sessionmaker()() as session:
        o3 = await enqueue_awareness_one(
            session,
            org_id=awareness_fix.org_id,
            subject=subj,
            subject_id=awareness_fix.doc_id,
            subject_version_id=v2,
            recipient=awareness_fix.recipient,
            event_key="doc.released",
            context_vars={"version.label": "2.0"},
            now=now,
            org_enabled=False,
            org_pierce=False,
        )
        await session.commit()

        rows = (
            (
                await session.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == awareness_fix.recipient.user_id,
                        Notification.event_key == "doc.released",
                    )
                )
            )
            .scalars()
            .all()
        )

    assert o3 == "created"  # NEW version re-notifies (the re-release fix)
    assert len(rows) == 2, f"Expected 2 rows (v1 + v2), got {len(rows)}"
    assert all(r.task_id is None for r in rows), "All awareness rows must have task_id=NULL"
    assert {r.subject_version_id for r in rows} == {v1, v2}, (
        "Rows must carry the two distinct version ids"
    )
    # org_enabled=False → wants_email=False → digest_due_at=None (no email scheduling when off).
    # The digest machinery only fires when email is on; this confirms no email row side-effect.
    assert all(r.digest_due_at is None for r in rows), (
        "org_enabled=False → digest_due_at must be None (no email scheduling)"
    )
