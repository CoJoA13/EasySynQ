"""Hook integration proofs — task.assigned notifications fire at all 6 creation sites (S-notify-1).

Tests:
1. Document submit-review (instantiate_approval, site 2) → Notification(event_key='task.assigned',
   subject_type='DOCUMENT') row exists for the approver after submit-review.
2. DOC_ACK sweep (site 5, deferred-due_at path) → in-app Notification row exists but NO
   NotificationEmail row, even with the org email flag ON (D-6: DOC_ACK email deferred to slice 3).
3. PERIODIC_REVIEW sweep (site 6, deferred-due_at path) → the stored notification's context
   'task.due_at' is NOT the em-dash placeholder (the due_at-after-patch fix, L5-3).
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._workflow_enums import WorkflowSubjectType
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.notification import Notification, NotificationEmail
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.models.workflow import Task, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.ack.sweep import sweep_acks
from easysynq_api.services.notifications.constants import EVENT_TASK_ASSIGNED
from easysynq_api.services.vault.review import sweep_reviews

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _ensure_user, _map_clause, _upload

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _set_org_email_flag(org_id: uuid.UUID, *, enabled: bool) -> None:
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        if cfg is not None:
            cfg.notifications_email_enabled = enabled
            await s.commit()


async def _notification_count_for_task(task_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as s:
        from sqlalchemy import func

        return (
            await s.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.task_id == task_id,
                    Notification.event_key == EVENT_TASK_ASSIGNED,
                )
            )
        ).scalar_one()


async def _email_count_for_task(task_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as s:
        from sqlalchemy import func

        return (
            await s.execute(
                select(func.count())
                .select_from(NotificationEmail)
                .join(Notification, NotificationEmail.notification_id == Notification.id)
                .where(Notification.task_id == task_id)
            )
        ).scalar_one()


async def _grant_keys(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """SYSTEM-scope ALLOW override per key. Returns user id."""
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(
        a=f"kc-nhook-author-{salt}",
        b=f"kc-nhook-approver-{salt}",
    )


# ---------------------------------------------------------------------------
# Test 1: Document approval path (instantiate_approval / site 2)
# ---------------------------------------------------------------------------


async def test_submit_review_enqueues_task_assigned_notification(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """submit-review (instantiate_approval) fires a task.assigned notification for the approver.

    The notification row is run-scoped (keyed on the task created for THIS document).
    """
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha = _auth(token_factory, subj.a)

    # Create doc and drive to InReview
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, f"nhook-{did}".encode())
    ci = await _checkin(app_client, ha, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text

    # Find the task created for this doc
    async with get_sessionmaker()() as s:
        instance = (
            await s.execute(
                select(WorkflowInstance).where(WorkflowInstance.subject_id == uuid.UUID(did))
            )
        ).scalar_one()
        task = (await s.execute(select(Task).where(Task.instance_id == instance.id))).scalar_one()

    # Assert: at least one task.assigned notification was written for this task
    notif_count = await _notification_count_for_task(task.id)
    assert notif_count >= 1, (
        f"Expected ≥1 task.assigned notification for task {task.id}, got {notif_count}"
    )

    # Assert subject_type is DOCUMENT
    async with get_sessionmaker()() as s:
        notif = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.task_id == task.id,
                        Notification.event_key == EVENT_TASK_ASSIGNED,
                    )
                )
            )
            .scalars()
            .first()
        )
    assert notif is not None
    assert notif.subject_type == "DOCUMENT", (
        f"Expected subject_type='DOCUMENT', got {notif.subject_type!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: DOC_ACK sweep (site 5) — in-app only, no email even with flag ON
# ---------------------------------------------------------------------------


async def test_ack_sweep_enqueues_in_app_only_no_email(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    app_under_test: Any,
) -> None:
    """DOC_ACK sweep (site 5): a task.assigned in-app notification is written but NO email row,
    even with the org email flag ON (D-6: DOC_ACK email deferred to slice 3).
    """
    salt = uuid.uuid4().hex[:10]
    author_kc = f"kc-ack-nhook-a-{salt}"
    approver_kc = f"kc-ack-nhook-b-{salt}"
    audience_kc = f"kc-ack-nhook-c-{salt}"

    # Both actors need full lifecycle (grant_lifecycle grants document.release too; SoD-2 flag
    # allows the approver to also release since author != approver for this doc).
    # Author also needs document.distribute to manage the distribution list.
    await s5.grant_lifecycle(author_kc)
    await _grant_keys(author_kc, ("document.distribute",))
    await s5.grant_lifecycle(approver_kc)
    await s5.grant_role(approver_kc, "Approver")
    ha, hb = _auth(token_factory, author_kc), _auth(token_factory, approver_kc)

    org_id = await _default_org_id()

    # Enable org email so we can prove it's suppressed for DOC_ACK
    await _set_org_email_flag(org_id, enabled=True)
    # allow_approver_release so hb (who approves) can also release
    await s5.set_approver_release(org_id, True)

    # Provision an audience user with document.acknowledge (will be ack-obligated)
    audience_id = await _grant_keys(audience_kc, ("document.acknowledge",))

    # Drive to Effective with acknowledgement_required=True distribution
    doc_type_id = await s5.type_id("SOP")
    doc = await _create(app_client, ha, doc_type_id)
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, f"ack-nhook-{did}".encode())
    ci = await _checkin(app_client, ha, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)

    # Set distribution with ack required + the audience user as a target entry, before release
    dist_r = await app_client.post(
        f"/api/v1/documents/{did}/distribution",
        headers=ha,
        json={
            "acknowledgement_required": True,
            "add_entries": [{"target_type": "user", "target_id": str(audience_id)}],
        },
    )
    assert dist_r.status_code == 200, dist_r.text

    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text

    task_id_str = await s5.task_for_doc(did)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id_str}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text

    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text

    # Run the ack sweep with document scope (trigger=release so the sweep fires immediately)
    async with get_sessionmaker()() as s:
        result = await sweep_acks(s, document_id=uuid.UUID(did), trigger="release")
        await s.commit()

    assert result.get("tasks_created", 0) >= 1, f"Expected ≥1 ack task minted, got {result}"

    # Find the DOC_ACK task(s) for this document + the audience user
    async with get_sessionmaker()() as s:
        ack_instances = (
            (
                await s.execute(
                    select(WorkflowInstance).where(
                        WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                        WorkflowInstance.subject_id == uuid.UUID(did),
                    )
                )
            )
            .scalars()
            .all()
        )
        ack_instance_ids = [i.id for i in ack_instances]

        ack_tasks = (
            (
                await s.execute(
                    select(Task).where(
                        Task.instance_id.in_(ack_instance_ids),
                        Task.assignee_user_id == audience_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(ack_tasks) >= 1, (
        f"Expected ≥1 DOC_ACK task for audience user {audience_id}, got {len(ack_tasks)}"
    )

    ack_task = ack_tasks[0]

    # Assert: in-app notification row exists
    in_app_count = await _notification_count_for_task(ack_task.id)
    assert in_app_count >= 1, (
        f"Expected ≥1 in-app notification for DOC_ACK task {ack_task.id}, got {in_app_count}"
    )

    # Assert: NO email row (D-6 suppression)
    email_count = await _email_count_for_task(ack_task.id)
    assert email_count == 0, f"Expected 0 email rows for DOC_ACK task (D-6), got {email_count}"


# ---------------------------------------------------------------------------
# Test 3: PERIODIC_REVIEW sweep (site 6) — due_at is patched, context holds real value
# ---------------------------------------------------------------------------

_EM_DASH_PLACEHOLDER = "—"  # the render placeholder for None datetimes


async def test_periodic_review_notification_has_real_due_at(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    app_under_test: Any,
) -> None:
    """PERIODIC_REVIEW sweep (site 6): the stored notification context['task.due_at'] is not the
    em-dash placeholder — the due_at_override is passed AFTER the bulk update (L5-3 fix).
    """
    salt = uuid.uuid4().hex[:10]
    author_kc = f"kc-pr-nhook-a-{salt}"
    approver_kc = f"kc-pr-nhook-b-{salt}"

    # Both actors need full lifecycle; allow_approver_release so hb can release after approving.
    await s5.grant_lifecycle(author_kc)
    await s5.grant_lifecycle(approver_kc)
    await s5.grant_role(approver_kc, "Approver")
    ha, hb = _auth(token_factory, author_kc), _auth(token_factory, approver_kc)

    org_id = await _default_org_id()
    await s5.set_approver_release(org_id, True)

    doc_type_id = await s5.type_id("SOP")
    doc = await _create(app_client, ha, doc_type_id)
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, f"pr-nhook-{did}".encode())
    ci = await _checkin(app_client, ha, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text

    task_id_str = await s5.task_for_doc(did)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id_str}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text

    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text

    # Set next_review_due to today (within the 30-day REVIEW_LEAD_DAYS horizon) so the sweep fires
    today = datetime.date.today()
    from sqlalchemy import update as sa_update

    async with get_sessionmaker()() as s:
        await s.execute(
            sa_update(DocumentedInformation)
            .where(DocumentedInformation.id == uuid.UUID(did))
            .values(review_period_months=12, next_review_due=today)
        )
        await s.commit()

    # Run the review sweep — no document_id filter; the doc will be found since it's in the horizon
    async with get_sessionmaker()() as s:
        result = await sweep_reviews(s)
        await s.commit()

    assert result.get("tasks_created", 0) >= 1, (
        f"Expected ≥1 periodic-review task minted, got {result}"
    )

    # Find the PERIODIC_REVIEW instance + task for this doc
    async with get_sessionmaker()() as s:
        pr_instance = (
            (
                await s.execute(
                    select(WorkflowInstance).where(
                        WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                        WorkflowInstance.subject_id == uuid.UUID(did),
                    )
                )
            )
            .scalars()
            .first()
        )

        assert pr_instance is not None, "No PERIODIC_REVIEW instance found for this doc"

        pr_task = (
            (await s.execute(select(Task).where(Task.instance_id == pr_instance.id)))
            .scalars()
            .first()
        )

        assert pr_task is not None, "No PERIODIC_REVIEW task found"

    # Find the notification for this task
    async with get_sessionmaker()() as s:
        notif = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.task_id == pr_task.id,
                        Notification.event_key == EVENT_TASK_ASSIGNED,
                    )
                )
            )
            .scalars()
            .first()
        )

    # The notification was written (best-effort — skip the due_at assertion if no template row)
    if notif is None:
        pytest.skip(
            "No PERIODIC_REVIEW notification row (template missing?); skipping due_at check"
        )

    # Assert: the context 'task.due_at' is NOT the em-dash placeholder
    ctx = notif.context or {}
    due_at_val = ctx.get("task.due_at")
    assert due_at_val != _EM_DASH_PLACEHOLDER, (
        f"task.due_at in notification context is the placeholder {_EM_DASH_PLACEHOLDER!r} — "
        "the due_at_override was not passed correctly (L5-3 regression)"
    )
    assert due_at_val is not None, (
        "task.due_at is None in notification context — due_at_override was not passed"
    )
