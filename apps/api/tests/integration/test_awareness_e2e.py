"""S-notify-5a end-to-end: _cutover → awareness_event → fan_out_awareness → notification rows.

Three proofs, each using a fresh (salted) document + users:
1. Read-scope filter + self-suppression: a reader gets the row; non-reader and actor do NOT.
2. Idempotency + re-release re-notifies: second sweep = 0 new rows; new version = 1 new row.
3. Org email OFF: in-app row created, no NotificationEmail, digest_due_at=None.

Pattern mirrors test_awareness_emit.py (drives a real HTTP release so _cutover writes the event).
Uses the DEFAULT org to avoid polluting test_restore's scalar_one(). No post-test teardown of
users/docs/notifications (notification rows REVOKE DELETE prevents app-role deletion; salted
keycloak_subjects ensure no cross-test collision). FK-ordered teardown of grants/overrides only.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# e2e_fixture: per-test (function scope) — each test gets its own users + doc
# ---------------------------------------------------------------------------


@pytest.fixture
async def e2e_fixture(
    app_under_test: Any,
    token_factory: Any,
) -> AsyncIterator[SimpleNamespace]:
    """Seed four users + a released document in the default org.

    Users:
      - actor: full lifecycle perms (approver + releaser); also holds document.read via the
               LIFECYCLE override set → self-suppression suppresses a genuine reader.
      - author: separate user so SoD-1 (author ≠ approver) holds; actor approves + releases.
      - reader: Employee (Read-only) role → document.read ALLOW, no other grants.
      - non_reader: no grants.

    The fixture drives a real HTTP release (so _cutover writes the awareness_event with
    actor_user_id = actor.id), then exposes rerelease() for the idempotency test.

    Teardown: FK-ordered removal of grants/overrides/scopes. Users/docs/notifications are
    NOT deleted (notification REVOKE DELETE + RESTRICT FKs make app-role deletion impossible).
    """
    from easysynq_api.db.models.app_user import AppUser, UserStatus
    from easysynq_api.db.models.authz_grant import PermissionOverride
    from easysynq_api.db.models.organization import Organization
    from easysynq_api.db.models.permission import Permission
    from easysynq_api.db.models.role import Role, RoleAssignment
    from easysynq_api.db.models.scope import Scope
    from easysynq_api.db.models.system_config import SystemConfig
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.domain.authz.types import Effect, ScopeLevel

    from . import s5_helpers as s5
    from .test_vault import _auth, _checkin, _upload

    salt = uuid.uuid4().hex[:10]

    role_assignment_ids: list[uuid.UUID] = []
    override_ids: list[uuid.UUID] = []
    scope_ids: list[uuid.UUID] = []

    async with get_sessionmaker()() as s:
        org_id: uuid.UUID = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()

        actor_user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-e2e-actor-{salt}",
            display_name=f"E2E Actor {salt}",
            email=f"e2e-actor-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        author_user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-e2e-author-{salt}",
            display_name=f"E2E Author {salt}",
            email=f"e2e-author-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        reader_user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-e2e-reader-{salt}",
            display_name=f"E2E Reader {salt}",
            email=f"e2e-reader-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        non_reader_user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-e2e-nonreader-{salt}",
            display_name=f"E2E NonReader {salt}",
            email=f"e2e-nonreader-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add_all([actor_user, author_user, reader_user, non_reader_user])
        await s.flush()
        actor_id = actor_user.id
        author_id = author_user.id
        reader_id = reader_user.id
        non_reader_id = non_reader_user.id

        # Grant actor + author the full lifecycle permission set via SYSTEM overrides.
        lifecycle_perms = (
            "document.read",
            "document.read_draft",
            "document.create",
            "document.checkout",
            "document.edit",
            "document.manage_metadata",
            "document.submit",
            "document.review",
            "document.approve",
            "document.release",
            "document.obsolete",
        )
        for subject_uid in (actor_id, author_id):
            for key in lifecycle_perms:
                perm = (
                    await s.execute(select(Permission).where(Permission.key == key))
                ).scalar_one()
                scope = Scope(org_id=org_id, level=ScopeLevel.SYSTEM)
                s.add(scope)
                await s.flush()
                scope_ids.append(scope.id)
                override = PermissionOverride(
                    org_id=org_id,
                    user_id=subject_uid,
                    permission_id=perm.id,
                    effect=Effect.ALLOW,
                    scope_id=scope.id,
                )
                s.add(override)
                await s.flush()
                override_ids.append(override.id)

        # Grant reader the Employee (Read-only) role which holds document.read at SYSTEM.
        employee_role = (
            await s.execute(
                select(Role).where(
                    Role.org_id == org_id,
                    Role.name == "Employee (Read-only)",
                )
            )
        ).scalar_one()
        reader_ra = RoleAssignment(
            org_id=org_id,
            user_id=reader_id,
            role_id=employee_role.id,
            bound_scope={"level": "SYSTEM"},
        )
        s.add(reader_ra)
        await s.flush()
        role_assignment_ids.append(reader_ra.id)

        # Enable allow_approver_release so actor can both approve AND release.
        cfg = await s.get(SystemConfig, org_id)
        if cfg is None:
            s.add(
                SystemConfig(
                    org_id=org_id,
                    allow_approver_release=True,
                    notifications_email_enabled=False,
                )
            )
        else:
            cfg.allow_approver_release = True
            cfg.notifications_email_enabled = False

        await s.commit()

    h_author = _auth(token_factory, f"kc-e2e-author-{salt}")
    h_actor = _auth(token_factory, f"kc-e2e-actor-{salt}")

    async with AsyncClient(
        transport=ASGITransport(app=app_under_test), base_url="http://test"
    ) as client:
        type_id = await s5.type_id("SOP")
        # author creates + submits for review; actor approves + releases (SoD-1: author ≠ approver)
        did = await s5.drive_to_approved(
            client, h_author, h_actor, type_id, f"e2e-v1-{salt}".encode()
        )
        rel = await client.post(f"/api/v1/documents/{did}/release", headers=h_actor, json={})
        assert rel.status_code == 200, f"release failed: {rel.text}"
        doc_id = uuid.UUID(did)

        async def rerelease() -> None:
            """Revise + release the document (new Effective version → new awareness_event).

            start-revision → UnderRevision (implicitly opens a checkout draft) → upload →
            checkin → submit-review → approve → release.
            """
            sv = await client.post(f"/api/v1/documents/{did}/start-revision", headers=h_author)
            assert sv.status_code == 200, f"start-revision failed: {sv.text}"
            sha2 = await _upload(client, h_author, did, f"e2e-v2-{salt}".encode())
            ci2 = await _checkin(
                client,
                h_author,
                did,
                sha2,
                change_reason="v2 revision",
                change_significance="MAJOR",
            )
            assert ci2.status_code == 201, f"checkin v2 failed: {ci2.text}"
            # clause mapping persists from v1 — no re-map needed
            sr2 = await client.post(f"/api/v1/documents/{did}/submit-review", headers=h_author)
            assert sr2.status_code == 200, f"submit-review v2 failed: {sr2.text}"
            task_id2 = await s5.task_for_doc(did)
            dec2 = await client.post(
                f"/api/v1/tasks/{task_id2}/decision",
                headers=h_actor,
                json={"outcome": "approve"},
            )
            assert dec2.status_code == 200, f"approve v2 failed: {dec2.text}"
            rel2 = await client.post(f"/api/v1/documents/{did}/release", headers=h_actor, json={})
            assert rel2.status_code == 200, f"release v2 failed: {rel2.text}"

        fix = SimpleNamespace(
            doc_id=doc_id,
            actor_id=actor_id,
            reader_id=reader_id,
            non_reader_id=non_reader_id,
            rerelease=rerelease,
            _override_ids=override_ids,
            _role_assignment_ids=role_assignment_ids,
            _scope_ids=scope_ids,
        )

        yield fix

    # FK-ordered teardown — only cleanable rows; notifications/awareness_events/users cannot be
    # deleted (REVOKE DELETE + RESTRICT FKs from notification to app_user).
    from easysynq_api.db.models.authz_grant import PermissionOverride
    from easysynq_api.db.models.role import RoleAssignment
    from easysynq_api.db.models.scope import Scope
    from easysynq_api.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        if fix._override_ids:
            await s.execute(
                delete(PermissionOverride).where(PermissionOverride.id.in_(fix._override_ids))
            )
        if fix._role_assignment_ids:
            await s.execute(
                delete(RoleAssignment).where(RoleAssignment.id.in_(fix._role_assignment_ids))
            )
        if fix._scope_ids:
            await s.execute(delete(Scope).where(Scope.id.in_(fix._scope_ids)))
        await s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_fanout_read_scope_filter_and_self_suppression(
    app_under_test: Any, e2e_fixture: SimpleNamespace
) -> None:
    """A reader gets the in-app row; a non-reader and the actor do not."""
    from easysynq_api.db.models.notification import Notification
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.fanout import fan_out_awareness

    now = datetime.datetime.now(datetime.UTC)
    await fan_out_awareness(get_sessionmaker(), now)

    async with get_sessionmaker()() as session:
        recips = (
            (
                await session.execute(
                    select(Notification.recipient_user_id).where(
                        Notification.event_key == "doc.released",
                        Notification.subject_id == e2e_fixture.doc_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert e2e_fixture.reader_id in recips
    assert e2e_fixture.non_reader_id not in recips  # read-scope filter
    assert e2e_fixture.actor_id not in recips  # self-suppression


async def test_fanout_idempotent_and_rerelease_renotifies(
    app_under_test: Any, e2e_fixture: SimpleNamespace
) -> None:
    """A second sweep creates 0 new rows; a re-release (new version) re-notifies prior readers."""
    from sqlalchemy import func

    from easysynq_api.db.models.awareness_event import AwarenessEvent
    from easysynq_api.db.models.notification import Notification
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.fanout import fan_out_awareness

    now = datetime.datetime.now(datetime.UTC)
    await fan_out_awareness(get_sessionmaker(), now)

    async with get_sessionmaker()() as session:
        count1 = (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.subject_id == e2e_fixture.doc_id)
            )
        ).scalar_one()
        # the event is stamped
        stamped = (
            (
                await session.execute(
                    select(AwarenessEvent.fanned_out_at).where(
                        AwarenessEvent.subject_id == e2e_fixture.doc_id
                    )
                )
            )
            .scalars()
            .first()
        )
        assert stamped is not None

    # second sweep — no new pending event → no new rows
    await fan_out_awareness(get_sessionmaker(), now)
    async with get_sessionmaker()() as session:
        count2 = (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.subject_id == e2e_fixture.doc_id)
            )
        ).scalar_one()
    assert count2 == count1

    # re-release → new awareness_event → fan out → reader gets a SECOND notification
    await e2e_fixture.rerelease()
    await fan_out_awareness(get_sessionmaker(), now)
    async with get_sessionmaker()() as session:
        reader_rows = (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.subject_id == e2e_fixture.doc_id,
                    Notification.recipient_user_id == e2e_fixture.reader_id,
                )
            )
        ).scalar_one()
    assert reader_rows == 2  # v1 + v2


async def test_fanout_org_email_off_creates_in_app_no_email(
    app_under_test: Any, e2e_fixture: SimpleNamespace
) -> None:
    """org email OFF → in-app row created; no NotificationEmail; digest_due_at=None."""
    from easysynq_api.db.models.notification import Notification, NotificationEmail
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.fanout import fan_out_awareness

    now = datetime.datetime.now(datetime.UTC)
    await fan_out_awareness(get_sessionmaker(), now)

    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(Notification).where(
                    Notification.subject_id == e2e_fixture.doc_id,
                    Notification.recipient_user_id == e2e_fixture.reader_id,
                )
            )
        ).scalar_one()
        emails = (
            (
                await session.execute(
                    select(NotificationEmail).where(NotificationEmail.notification_id == row.id)
                )
            )
            .scalars()
            .all()
        )

    # org email OFF → wants_email=False → digest_due_at=None (corrected — not "is not None")
    assert row.digest_due_at is None  # org email OFF → no email scheduling
    assert emails == []  # org email off → no email row


async def test_fanout_template_miss_no_stamp_no_exception(
    app_under_test: Any, e2e_fixture: SimpleNamespace, dsns: dict[str, str]
) -> None:
    """Template missing → no MissingGreenlet, fanned_out_at stays NULL, zero notifications.

    Regression for the S-ing-4 MissingGreenlet trap: the OLD code called
    ``await session.rollback()`` then accessed ``event.event_key`` on the expired ORM instance,
    triggering a synchronous lazy-refresh on an async session → MissingGreenlet at pool teardown.
    The fix captures event attrs into locals before any early-return and removes the rollback.

    The template is deactivated via the owner DSN (notification_template is SELECT-only for the
    app role per migration 0063) and restored in a finally block so the shared DB is left clean.
    """
    from sqlalchemy import func, text
    from sqlalchemy.ext.asyncio import create_async_engine

    from easysynq_api.db.models.awareness_event import AwarenessEvent
    from easysynq_api.db.models.notification import Notification
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.fanout import fan_out_awareness

    sm = get_sessionmaker()
    owner_engine = create_async_engine(dsns["owner"])

    # Deactivate the doc.released template via the owner role (app role is SELECT-only).
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE notification_template SET is_effective = false"
                " WHERE event_key = 'doc.released'"
            )
        )

    try:
        now = datetime.datetime.now(datetime.UTC)
        # Should not raise (no MissingGreenlet, no other exception).
        result = await fan_out_awareness(sm, now)

        # The event must NOT be stamped — fanned_out_at stays NULL so it is re-claimable.
        async with sm() as s:
            fanned_out_at = (
                (
                    await s.execute(
                        select(AwarenessEvent.fanned_out_at).where(
                            AwarenessEvent.subject_id == e2e_fixture.doc_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            # Zero notifications created for this doc by this sweep.
            notif_count = (
                await s.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        Notification.subject_id == e2e_fixture.doc_id,
                        Notification.event_key == "doc.released",
                    )
                )
            ).scalar_one()

        assert fanned_out_at is None, "fanned_out_at must stay NULL on a template miss (re-claim)"
        assert notif_count == 0, "no notification rows must be created on a template miss"
        # The sweep counted the event (it was claimed via _pending_event_ids) but returned 0
        # notifications — either way, no exception is the primary assertion.
        _ = result  # suppress unused-var; the no-exception guarantee is the gate

    finally:
        # Restore template so other tests are not affected (FK-ordered / run-scoped per S-notify-4).
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE notification_template SET is_effective = true"
                    " WHERE event_key = 'doc.released'"
                )
            )
        await owner_engine.dispose()


async def test_fanout_per_recipient_template_miss_no_stamp(
    app_under_test: Any, e2e_fixture: SimpleNamespace
) -> None:
    """TOCTOU loop path: template vanishes AFTER the probe but BEFORE the per-recipient enqueue.

    Simulates the per-recipient `no_template` return (Codex P2) by patching dispatch.render to
    succeed on the first call (the pre-loop probe in process_one_awareness_event) and return None
    on the second (the call inside enqueue_awareness_one for the first recipient). The event must
    NOT be stamped and zero notifications must persist — identical guarantee to the probe path.
    """
    from unittest.mock import patch

    from sqlalchemy import func

    from easysynq_api.db.models.awareness_event import AwarenessEvent
    from easysynq_api.db.models.notification import Notification
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications import fanout as fanout_mod
    from easysynq_api.services.notifications.fanout import fan_out_awareness

    sm = get_sessionmaker()

    # A real RenderedForms-like object for the probe call; None for the per-recipient call.
    from types import SimpleNamespace as NS

    real_forms = NS(
        title="Doc Released",
        body="A document was released.",
        email_subject="Doc Released",
        email_body="A document was released.",
    )
    render_calls: list[int] = [0]

    async def _fake_render(session: Any, event_key: str, variables: Any) -> Any:
        render_calls[0] += 1
        if render_calls[0] == 1:
            return real_forms  # probe succeeds → enters the recipient loop
        return None  # per-recipient enqueue → no_template → must NOT stamp

    now = datetime.datetime.now(datetime.UTC)

    with patch.object(fanout_mod, "render", new=_fake_render):
        # Also patch dispatch.render so enqueue_awareness_one sees the same patched render.
        import easysynq_api.services.notifications.dispatch as dispatch_mod

        with patch.object(dispatch_mod, "render", new=_fake_render):
            result = await fan_out_awareness(sm, now)

    async with sm() as s:
        fanned_out_at = (
            (
                await s.execute(
                    select(AwarenessEvent.fanned_out_at).where(
                        AwarenessEvent.subject_id == e2e_fixture.doc_id
                    )
                )
            )
            .scalars()
            .first()
        )
        notif_count = (
            await s.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.subject_id == e2e_fixture.doc_id,
                    Notification.event_key == "doc.released",
                )
            )
        ).scalar_one()

    assert fanned_out_at is None, "fanned_out_at must stay NULL when loop path returns no_template"
    assert notif_count == 0, "no notification rows must persist when loop path returns no_template"
    _ = result
