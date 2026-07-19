"""S-cleanup-bundle #6: the admin requeue-failed-notifications endpoint gate (config.update)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._notification_enums import (
    NotificationEmailKind,
    NotificationEmailStatus,
)
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.notification import NotificationEmail
from easysynq_api.db.session import get_sessionmaker

from .test_capa import _grant  # SYSTEM-scope PermissionOverride grant helper → user id
from .test_vault import _auth  # bearer-header builder

pytestmark = pytest.mark.integration


def _email(org_id: uuid.UUID, status: NotificationEmailStatus, **over: Any) -> NotificationEmail:
    return NotificationEmail(
        id=uuid.uuid4(),
        org_id=org_id,
        recipient_email="ops@example.com",
        subject="s",
        body="b",
        status=status,
        attempts=over.get("attempts", 5),
        next_attempt_at=over.get("next_attempt_at"),
        last_error=over.get("last_error", "smtp down"),
        failed_at=over.get("failed_at"),
        email_kind=NotificationEmailKind.SINGLE,
    )


async def test_requeue_resets_failed_and_leaves_other_statuses(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"rq-admin-{uuid.uuid4().hex[:8]}"
    user_id = await _grant(subject, ("config.update",))
    async with get_sessionmaker()() as s:
        caller = await s.get(AppUser, user_id)
        assert caller is not None
        org_id = caller.org_id

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as s:
        failed = _email(org_id, NotificationEmailStatus.FAILED, failed_at=now, attempts=5)
        sent = _email(org_id, NotificationEmailStatus.SENT)
        suppressed = _email(org_id, NotificationEmailStatus.SUPPRESSED)
        s.add_all([failed, sent, suppressed])
        await s.commit()
        failed_id, sent_id, suppressed_id = failed.id, sent.id, suppressed.id

    resp = await app_client.post(
        "/api/v1/admin/notifications/requeue-failed", headers=_auth(token_factory, subject)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["requeued"] >= 1

    async with get_sessionmaker()() as s:
        row = (
            await s.execute(select(NotificationEmail).where(NotificationEmail.id == failed_id))
        ).scalar_one()
        assert row.status == NotificationEmailStatus.PENDING
        assert row.attempts == 0
        assert row.next_attempt_at is None and row.failed_at is None and row.last_error is None
        # the status filter holds: SENT and SUPPRESSED rows are untouched
        sent_row = (
            await s.execute(select(NotificationEmail).where(NotificationEmail.id == sent_id))
        ).scalar_one()
        supp_row = (
            await s.execute(select(NotificationEmail).where(NotificationEmail.id == suppressed_id))
        ).scalar_one()
        assert sent_row.status == NotificationEmailStatus.SENT
        assert supp_row.status == NotificationEmailStatus.SUPPRESSED


async def test_requeue_forbidden_without_config_update(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"rq-noperm-{uuid.uuid4().hex[:8]}"
    await _grant(subject, ("document.read",))  # exists, but lacks config.update
    resp = await app_client.post(
        "/api/v1/admin/notifications/requeue-failed", headers=_auth(token_factory, subject)
    )
    assert resp.status_code == 403, resp.text
