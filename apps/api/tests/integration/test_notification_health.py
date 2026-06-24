"""S-notify-5b: the read-only delivery-health aggregator (services/notifications/health.py)."""

from __future__ import annotations

import datetime
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from easysynq_api.db.models._notification_enums import NotificationEmailStatus
from easysynq_api.db.models.awareness_event import AwarenessEvent
from easysynq_api.db.models.notification import NotificationEmail
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.health import get_delivery_health

pytestmark = pytest.mark.integration

_SCHED = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)  # far future → never claimable + sorts first


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def test_get_delivery_health_aggregates_and_isolates(app_under_test: object) -> None:
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    aw_id = uuid.uuid4()
    async with get_sessionmaker()() as s:
        base = await get_delivery_health(s, org_id)
        s.add_all(
            [
                NotificationEmail(
                    org_id=org_id, recipient_email=f"fail-{salt}@x.test", subject="s", body="b",
                    status=NotificationEmailStatus.FAILED, attempts=5,
                    last_error="SMTP 550 mailbox unavailable", failed_at=_SCHED,
                ),
                NotificationEmail(
                    org_id=org_id, recipient_email=f"now-{salt}@x.test", subject="s", body="b",
                    status=NotificationEmailStatus.PENDING, next_attempt_at=None,
                ),
                NotificationEmail(
                    org_id=org_id, recipient_email=f"sched-{salt}@x.test", subject="s", body="b",
                    status=NotificationEmailStatus.PENDING, next_attempt_at=_SCHED,
                ),
                NotificationEmail(
                    org_id=org_id, recipient_email=f"supp-{salt}@x.test", subject="s", body="b",
                    status=NotificationEmailStatus.SUPPRESSED,
                ),
            ]
        )
        s.add(
            AwarenessEvent(
                id=aw_id, org_id=org_id, event_key="doc.released", subject_type="DOCUMENT",
                subject_id=uuid.uuid4(), occurred_at=_SCHED,
            )
        )
        await s.commit()
        try:
            after = await get_delivery_health(s, org_id)
            assert after["email"]["failed"] == base["email"]["failed"] + 1
            assert after["email"]["pending_now"] == base["email"]["pending_now"] + 1
            assert after["email"]["pending_scheduled"] == base["email"]["pending_scheduled"] + 1
            assert after["email"]["suppressed"] == base["email"]["suppressed"] + 1
            assert after["awareness"]["pending"] == base["awareness"]["pending"] + 1
            assert after["email"]["oldest_pending_at"] is not None
            top = after["recent_failures"][0]
            assert top["recipient_email"] == f"fail-{salt}@x.test"
            assert top["last_error"] == "SMTP 550 mailbox unavailable"
            assert top["attempts"] == 5
            assert top["email_kind"] == "single"
            assert top["failed_at"] is not None
            assert isinstance(after["org_email_enabled"], bool)
            # Org isolation: a random (non-existent) org id matches no rows + no config.
            empty = await get_delivery_health(s, uuid.uuid4())
            assert empty["email"]["failed"] == 0
            assert empty["email"]["pending_now"] == 0
            assert empty["recent_failures"] == []
            assert empty["awareness"]["pending"] == 0
            assert empty["org_email_enabled"] is False
        finally:
            # easysynq_app has REVOKE DELETE on notification_email and awareness_event (migrations
            # 0063/0066 enforce the append-only-ish posture). Neutralise test rows via UPDATE so
            # they are invisible to future count queries: SENT emails are excluded from all status
            # buckets; a non-null fanned_out_at removes the awareness row from the pending backlog.
            _now = datetime.datetime.now(datetime.UTC)
            await s.execute(
                sa.update(NotificationEmail)
                .where(NotificationEmail.recipient_email.like(f"%{salt}%"))
                .values(status=NotificationEmailStatus.SENT, sent_at=_now)
            )
            await s.execute(
                sa.update(AwarenessEvent)
                .where(AwarenessEvent.id == aw_id)
                .values(fanned_out_at=_now)
            )
            await s.commit()
