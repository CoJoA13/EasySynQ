"""S-cleanup-bundle #6: the admin requeue-failed-notifications endpoint gate (config.update)."""

from __future__ import annotations

import datetime
import logging
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
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.requeue import requeue_failed

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


async def _set_email_enabled(org_id: uuid.UUID, value: bool) -> bool:
    """Set the org's email-delivery flag (the route guards requeue on it); return the prior value so
    the caller can restore the shared org's config in a finally."""
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        assert cfg is not None
        prev = cfg.notifications_email_enabled
        cfg.notifications_email_enabled = value
        await s.commit()
        return prev


async def _caller_org(user_id: uuid.UUID) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        caller = await s.get(AppUser, user_id)
        assert caller is not None
        return caller.org_id


async def test_requeue_resets_failed_and_leaves_other_statuses(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"rq-admin-{uuid.uuid4().hex[:8]}"
    user_id = await _grant(subject, ("config.update",))
    org_id = await _caller_org(user_id)

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as s:
        failed = _email(org_id, NotificationEmailStatus.FAILED, failed_at=now, attempts=5)
        sent = _email(org_id, NotificationEmailStatus.SENT)
        suppressed = _email(org_id, NotificationEmailStatus.SUPPRESSED)
        s.add_all([failed, sent, suppressed])
        await s.commit()
        failed_id, sent_id, suppressed_id = failed.id, sent.id, suppressed.id

    prev = await _set_email_enabled(
        org_id, True
    )  # requeue only proceeds while email delivery is on
    try:
        resp = await app_client.post(
            "/api/v1/admin/notifications/requeue-failed", headers=_auth(token_factory, subject)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["requeued"] >= 1
    finally:
        await _set_email_enabled(org_id, prev)

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


async def test_requeue_noop_when_email_disabled(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """With email delivery OFF, requeue must leave FAILED rows untouched — requeuing them would only
    let the next drain terminally SUPPRESS them (unrecoverable once email is re-enabled)."""
    subject = f"rq-off-{uuid.uuid4().hex[:8]}"
    user_id = await _grant(subject, ("config.update",))
    org_id = await _caller_org(user_id)

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as s:
        failed = _email(org_id, NotificationEmailStatus.FAILED, failed_at=now, attempts=5)
        s.add(failed)
        await s.commit()
        failed_id = failed.id

    prev = await _set_email_enabled(org_id, False)
    try:
        resp = await app_client.post(
            "/api/v1/admin/notifications/requeue-failed", headers=_auth(token_factory, subject)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["requeued"] == 0
    finally:
        await _set_email_enabled(org_id, prev)

    async with get_sessionmaker()() as s:
        row = (
            await s.execute(select(NotificationEmail).where(NotificationEmail.id == failed_id))
        ).scalar_one()
        assert row.status == NotificationEmailStatus.FAILED  # untouched: email delivery is off
        assert row.attempts == 5


async def test_requeue_forbidden_without_config_update(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"rq-noperm-{uuid.uuid4().hex[:8]}"
    await _grant(subject, ("document.read",))  # exists, but lacks config.update
    resp = await app_client.post(
        "/api/v1/admin/notifications/requeue-failed", headers=_auth(token_factory, subject)
    )
    assert resp.status_code == 403, resp.text


async def test_requeue_is_org_scoped(app_under_test: object) -> None:
    """Org-scoping: requeue_failed(orgA) must NOT touch orgB's FAILED rows. Service-level + rollback
    so the throwaway 2nd org never commits (leak-free; dodges the test_restore single-org scalar_one
    trap). Mutation-distinguishing: drop the org_id predicate and orgB's row would flip too."""
    async with get_sessionmaker()() as s:
        org_a = Organization(
            legal_name="Requeue A", short_code=f"RQA{uuid.uuid4().hex[:6].upper()}"
        )
        org_b = Organization(
            legal_name="Requeue B", short_code=f"RQB{uuid.uuid4().hex[:6].upper()}"
        )
        s.add_all([org_a, org_b])
        await s.flush()
        now = datetime.datetime.now(datetime.UTC)
        a = _email(org_a.id, NotificationEmailStatus.FAILED, failed_at=now)
        b = _email(org_b.id, NotificationEmailStatus.FAILED, failed_at=now)
        s.add_all([a, b])
        await s.flush()
        a_id, b_id = a.id, b.id

        count = await requeue_failed(s, org_a.id)
        await s.flush()

        a_after = (
            await s.execute(
                select(NotificationEmail)
                .where(NotificationEmail.id == a_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        b_after = (
            await s.execute(
                select(NotificationEmail)
                .where(NotificationEmail.id == b_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert a_after.status == NotificationEmailStatus.PENDING
        assert b_after.status == NotificationEmailStatus.FAILED  # untouched: different org
        assert count == 1
        await s.rollback()  # never commit the throwaway orgs → leak-free


async def test_requeue_logs_structured_fields_after_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], caplog: pytest.LogCaptureFixture
) -> None:
    """The requeue log is the SOLE record of the action (no audit_event by design). It must fire
    from the ROUTE after commit, with its fields nested under ``extra_fields`` — the JsonFormatter
    emits ONLY those (flat ``extra`` is dropped). Mutation-distinguishing on both: flat extra ⇒
    ``record.extra_fields`` absent; a pre-commit emit would move it back into the service."""
    subject = f"rq-log-{uuid.uuid4().hex[:8]}"
    user_id = await _grant(subject, ("config.update",))
    org_id = await _caller_org(user_id)

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as s:
        s.add(_email(org_id, NotificationEmailStatus.FAILED, failed_at=now))
        await s.commit()

    prev = await _set_email_enabled(org_id, True)
    try:
        with caplog.at_level(logging.INFO, logger="easysynq.notifications.requeue"):
            resp = await app_client.post(
                "/api/v1/admin/notifications/requeue-failed", headers=_auth(token_factory, subject)
            )
        assert resp.status_code == 200, resp.text
    finally:
        await _set_email_enabled(org_id, prev)

    recs = [r for r in caplog.records if r.getMessage() == "notifications.requeued"]
    assert recs, "expected a notifications.requeued log record"
    fields = getattr(recs[-1], "extra_fields", None)
    assert fields is not None, (
        "requeue fields must nest under extra_fields (JsonFormatter drops flat extra)"
    )
    assert fields["count"] >= 1
    assert fields["org_id"] == str(org_id)
    assert fields["actor_id"] == str(user_id)
