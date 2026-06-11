"""S-ack-1 integration proofs: the full distribute→release→mint→acknowledge→coverage loop,
R15 target-entry catch-up, MINOR carry-forward / MAJOR re-arm, the decide authz matrix,
append-only DB grants, and sweep idempotency (spec §8)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from easysynq_api.db.models._ack_enums import AckCreatedReason
from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._vault_enums import ChangeSignificance, VersionState
from easysynq_api.db.models._workflow_enums import TaskState, WorkflowSubjectType
from easysynq_api.db.models.acknowledgement import Acknowledgement
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.workflow import Task, TaskOutcome, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.problems import ProblemException
from easysynq_api.services.ack.sweep import _cancel_instance, sweep_acks
from easysynq_api.services.workflow import engine as wf_engine
from easysynq_api.services.workflow import repository as wf_repo

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _ensure_user, _map_clause, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(
        a=f"kc-ack-author-{salt}",  # author/distributor
        b=f"kc-ack-approver-{salt}",  # approver/releaser
        sam=f"kc-ack-sam-{salt}",  # the acknowledger
        u2=f"kc-ack-second-{salt}",  # a second audience member (key-less in test 5)
        outsider=f"kc-ack-outsider-{salt}",  # holds nothing, in no audience
    )


async def grant_keys(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """SYSTEM-scope ALLOW override per key (grant_lifecycle's body with a ``keys`` param). An
    empty ``keys`` just ensures the app_user row exists and returns its id."""
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


async def _setup_actors(subj: SimpleNamespace) -> uuid.UUID:
    """The standard cast: a = lifecycle + document.distribute, b = lifecycle (approver/releaser,
    with the SoD-2 approver-release flag on), sam = document.acknowledge. Returns sam's
    app_user id."""
    await s5.grant_lifecycle(subj.a)
    await grant_keys(subj.a, ("document.distribute",))
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    return await grant_keys(subj.sam, ("document.acknowledge",))


async def _ack_task_for(doc_uuid: uuid.UUID, user_id: uuid.UUID) -> str:
    """The open PENDING DOC_ACK task id for (doc, user)."""
    async with get_sessionmaker()() as s:
        task = (
            await s.execute(
                select(Task)
                .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                    WorkflowInstance.subject_id == doc_uuid,
                    Task.assignee_user_id == user_id,
                    Task.state == TaskState.PENDING,
                )
                .limit(1)
            )
        ).scalar_one()
        return str(task.id)


async def _open_ack_rows(
    doc_uuid: uuid.UUID,
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, dict[str, Any]]]:
    """(task_id, instance_id, assignee_user_id, instance_context) for every open (PENDING)
    DOC_ACK task on this doc — the run-scoped snapshot the assertions key on."""
    async with get_sessionmaker()() as s:
        pairs = (
            await s.execute(
                select(Task, WorkflowInstance)
                .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                    WorkflowInstance.subject_id == doc_uuid,
                    Task.state == TaskState.PENDING,
                )
            )
        ).all()
        return [(t.id, i.id, t.assignee_user_id, dict(i.context or {})) for t, i in pairs]


async def _run_sweep(
    document_id: uuid.UUID | None = None, trigger: str | None = None
) -> dict[str, int]:
    async with get_sessionmaker()() as session:
        return await sweep_acks(session, document_id=document_id, trigger=trigger)


async def _release_ack_doc(
    app_client: AsyncClient,
    ha: dict[str, str],
    hb: dict[str, str],
    type_id: str,
    content: bytes,
    entries: list[dict[str, Any]],
) -> tuple[str, uuid.UUID]:
    """create → checkout → upload unique bytes → checkin MAJOR → map clause → POST distribution
    (flag on + ``entries``, as the document.distribute-holding author) → submit-review → approve
    (b via task_for_doc) → release (b). Returns (doc_id_str, doc_uuid)."""
    doc = await _create(app_client, ha, type_id)
    did = doc["id"]
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, content)
    ci = await _checkin(app_client, ha, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)
    dist = await app_client.post(
        f"/api/v1/documents/{did}/distribution",
        headers=ha,
        json={"acknowledgement_required": True, "add_entries": entries},
    )
    assert dist.status_code == 200, dist.text
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text
    task_id = await s5.task_for_doc(did)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text
    return did, uuid.UUID(did)


async def _rerelease(
    app_client: AsyncClient,
    ha: dict[str, str],
    hb: dict[str, str],
    did: str,
    content: bytes,
    significance: str,
) -> str:
    """start-revision → upload NEW bytes → checkin (MINOR/MAJOR) → submit → approve → release.
    Returns the NEW current_effective_version_id (str)."""
    sr = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    assert sr.status_code == 200, sr.text
    sha = await _upload(app_client, ha, did, content)
    ci = await _checkin(
        app_client, ha, did, sha, change_reason="rev", change_significance=significance
    )
    assert ci.status_code == 201, ci.text
    sub = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sub.status_code == 200, sub.text
    task_id = await s5.task_for_doc(did)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text
    new_ver = rel.json()["current_effective_version_id"]
    assert new_ver is not None
    return str(new_ver)


# ---------------------------------------------------------------------------
# 1. The thesis loop
# ---------------------------------------------------------------------------


async def test_full_loop_distribute_release_mint_ack_coverage(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """distribute (flag + user target) → release → sweep(trigger=release) mints exactly ONE
    DOC_ACK task (created_reason=release) → Sam's inbox carries it → acknowledge → 200 +
    COMPLETED + the immutable evidence row + DOCUMENT_ACKNOWLEDGED audit → coverage 1/1/0/0 →
    a re-sweep mints nothing (idempotency)."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    type_id = await s5.type_id("SOP")
    content = f"ack-full-loop-{subj.a}".encode()

    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        content,
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )

    # Doc-scoped sweep (the release trigger) — run-scoped: only this doc can mint here.
    result = await _run_sweep(document_id=doc_uuid, trigger="release")
    assert result["tasks_created"] == 1, result

    task_id = await _ack_task_for(doc_uuid, sam_id)

    # Sam's self-scoped inbox carries the DOC_ACK task.
    inbox = await app_client.get("/api/v1/tasks?type=DOC_ACK&state=PENDING", headers=hs)
    assert inbox.status_code == 200, inbox.text
    assert task_id in {t["id"] for t in inbox.json()}, "Sam's inbox must carry the DOC_ACK task"

    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert dr.status_code == 200, dr.text
    body = dr.json()
    assert body["current_state"] == "COMPLETED"
    assert body["acknowledgement_id"] is not None

    # DB: exactly 1 Acknowledgement row for (doc, sam) + exactly 1 DOCUMENT_ACKNOWLEDGED audit.
    async with get_sessionmaker()() as s:
        acks = (
            (
                await s.execute(
                    select(Acknowledgement).where(
                        Acknowledgement.document_id == doc_uuid,
                        Acknowledgement.user_id == sam_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(acks) == 1, f"expected exactly 1 ack row, got {len(acks)}"
        assert acks[0].created_reason is AckCreatedReason.release
        assert str(acks[0].id) == body["acknowledgement_id"]
        events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_type == AuditObjectType.document,
                        AuditEvent.object_id == doc_uuid,
                        AuditEvent.event_type == EventType.DOCUMENT_ACKNOWLEDGED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1, f"expected exactly 1 DOCUMENT_ACKNOWLEDGED, got {len(events)}"
        assert events[0].scope_ref is not None

    cov = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()[
        "coverage"
    ]
    assert cov == {"required": 1, "acknowledged": 1, "pending": 0, "overdue": 0}

    # Re-sweep (doc-scoped) — mints nothing: the obligation is satisfied.
    result2 = await _run_sweep(document_id=doc_uuid)
    assert result2["tasks_created"] == 0, result2


# ---------------------------------------------------------------------------
# 2. R15 target-entry catch-up
# ---------------------------------------------------------------------------


async def test_r15_target_entry_catchup(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """A role-targeted doc mints nothing for Sam while he lacks the role (even though he already
    holds document.acknowledge); once he gains the role, the ORG-WIDE sweep (document_id=None)
    catches him up with created_reason=target_entry; after his ack a further org-wide sweep adds
    no open obligation for this doc (delta-based — the shared DB may hold foreign role
    members, so absolutes on the role's audience are off-limits)."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    type_id = await s5.type_id("SOP")
    content = f"ack-r15-{subj.a}".encode()

    org_id = await s5.default_org_id()
    async with get_sessionmaker()() as s:
        role_id = (
            await s.execute(
                select(Role.id).where(Role.org_id == org_id, Role.name == "Employee (Read-only)")
            )
        ).scalar_one()

    _did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        content,
        entries=[{"target_type": "org_role", "target_id": str(role_id)}],
    )

    # Sam holds the key but NOT the role → the sweep must not mint for him.
    await _run_sweep(document_id=doc_uuid)
    rows = await _open_ack_rows(doc_uuid)
    assert sam_id not in {assignee for _, _, assignee, _ in rows}, (
        "Sam is not in the role audience yet — no obligation may exist for him"
    )

    # Sam joins the role → the ORG-WIDE catch-up sweep mints his task (R15).
    await s5.grant_role(subj.sam, "Employee (Read-only)")
    await grant_keys(subj.sam, ("document.acknowledge",))
    await _run_sweep()
    task_id = await _ack_task_for(doc_uuid, sam_id)
    rows = await _open_ack_rows(doc_uuid)
    sam_rows = [r for r in rows if r[2] == sam_id]
    assert len(sam_rows) == 1, f"expected exactly 1 open obligation for Sam, got {len(sam_rows)}"
    # created_reason is checked on the ORM evidence row after Sam acks (authoritative — see below).

    # Sam acknowledges.
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert dr.status_code == 200, dr.text
    assert dr.json()["current_state"] == "COMPLETED"

    # The evidence row records WHY the obligation existed (doc 17's discriminator) — read the
    # ORM row, not the engine's internal context bag.
    async with get_sessionmaker()() as s:
        ack = (
            await s.execute(
                select(Acknowledgement).where(
                    Acknowledgement.document_id == doc_uuid,
                    Acknowledgement.user_id == sam_id,
                )
            )
        ).scalar_one()
        assert ack.created_reason is AckCreatedReason.target_entry

    # A SECOND org-wide sweep mints nothing further for this doc (delta-based + Sam-scoped).
    before = {t for t, _, _, _ in await _open_ack_rows(doc_uuid)}
    await _run_sweep()
    after_rows = await _open_ack_rows(doc_uuid)
    assert {t for t, _, _, _ in after_rows} == before, (
        "the second org-wide sweep must not change this doc's open obligations"
    )
    assert sam_id not in {assignee for _, _, assignee, _ in after_rows}, (
        "Sam is satisfied — no re-mint"
    )


# ---------------------------------------------------------------------------
# 3. MINOR carry-forward
# ---------------------------------------------------------------------------


async def test_minor_release_carries_forward(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """R43 carry-forward: Sam's v1 ack satisfies across a MINOR re-release — the post-release
    sweep mints nothing and coverage stays 1/1/0/0 (the boundary is still v1's MAJOR seq)."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    type_id = await s5.type_id("SOP")

    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-minor-v1-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    task_id = await _ack_task_for(doc_uuid, sam_id)
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert dr.status_code == 200, dr.text

    await _rerelease(app_client, ha, hb, did, f"ack-minor-v2-{subj.a}".encode(), "MINOR")
    result = await _run_sweep(document_id=doc_uuid, trigger="release")
    assert result["tasks_created"] == 0, result

    cov = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()[
        "coverage"
    ]
    assert cov == {"required": 1, "acknowledged": 1, "pending": 0, "overdue": 0}
    assert await _open_ack_rows(doc_uuid) == [], (
        "a MINOR re-release must leave no fresh obligation on this doc"
    )


# ---------------------------------------------------------------------------
# 4. MAJOR re-arm: stale-pin cancel + fresh mint
# ---------------------------------------------------------------------------


async def test_major_release_rearms_and_cancels_stale(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """An undecided obligation pinned to v1 is cancelled by the post-MAJOR sweep (task SKIPPED,
    instance CANCELLED, the disappearance audited) and ONE fresh PENDING task pinned to the new
    version is minted in the SAME pass; deciding the stale task id is a 409 ack_superseded."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    type_id = await s5.type_id("SOP")

    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-major-v1-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    old_task_id = await _ack_task_for(doc_uuid, sam_id)
    rows = await _open_ack_rows(doc_uuid)
    assert len(rows) == 1
    old_instance_id = rows[0][1]
    old_pinned = rows[0][3].get("document_version_id")

    # Sam does NOT ack. A MAJOR re-release supersedes the pinned version.
    new_ver_id = await _rerelease(
        app_client, ha, hb, did, f"ack-major-v2-{subj.a}".encode(), "MAJOR"
    )
    assert new_ver_id != old_pinned

    result = await _run_sweep(document_id=doc_uuid, trigger="release")
    assert result["tasks_created"] == 1, result
    assert result["tasks_cancelled"] == 1, result

    async with get_sessionmaker()() as s:
        old_task = await s.get(Task, uuid.UUID(old_task_id))
        assert old_task is not None
        assert old_task.state is TaskState.SKIPPED, (
            f"stale task must be SKIPPED, got {old_task.state}"
        )
        old_instance = await s.get(WorkflowInstance, old_instance_id)
        assert old_instance is not None
        assert old_instance.current_state == "CANCELLED"
        # The obligation's disappearance left a trace (spec §7).
        cancel_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_type == AuditObjectType.document,
                        AuditEvent.object_id == doc_uuid,
                        AuditEvent.event_type == EventType.STAGE_FAILED,
                        AuditEvent.after["instance_id"].astext == str(old_instance_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(cancel_events) == 1, f"expected 1 cancel audit, got {len(cancel_events)}"
        after = cancel_events[0].after or {}
        assert after.get("event") == "ack_obligation_cancelled"
        # "why" deliberately un-pinned — internal detail.

    # Exactly ONE fresh PENDING task, pinned to the NEW version.
    rows = await _open_ack_rows(doc_uuid)
    assert len(rows) == 1, f"expected exactly 1 fresh open obligation, got {len(rows)}"
    new_task_id, _new_instance_id, assignee, ctx = rows[0]
    assert str(new_task_id) != old_task_id
    assert assignee == sam_id
    assert ctx.get("document_version_id") == new_ver_id

    # Deciding the OLD task id is dead: 409 ack_superseded.
    dr = await app_client.post(
        f"/api/v1/tasks/{old_task_id}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert dr.status_code == 409, dr.text
    assert dr.json()["code"] == "ack_superseded"


async def test_major_release_rearms_after_satisfied(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """The satisfied-rearm leg: Sam acks v1, then a MAJOR re-release resets coverage to 0 and the
    sweep mints a FRESH task pinned to the new version — while the v1 Acknowledgement row is never
    touched (evidence is immutable; satisfaction is the COVERAGE computation, R43)."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    type_id = await s5.type_id("SOP")

    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-rearm-v1-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    v1_id = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()[
        "current_effective_version_id"
    ]
    assert v1_id is not None

    await _run_sweep(document_id=doc_uuid, trigger="release")
    task_id = await _ack_task_for(doc_uuid, sam_id)
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert dr.status_code == 200, dr.text

    new_ver_id = await _rerelease(
        app_client, ha, hb, did, f"ack-rearm-v2-{subj.a}".encode(), "MAJOR"
    )
    result = await _run_sweep(document_id=doc_uuid, trigger="release")
    assert result["tasks_created"] == 1, result

    cov = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()[
        "coverage"
    ]
    assert cov == {"required": 1, "acknowledged": 0, "pending": 1, "overdue": 0}

    rows = await _open_ack_rows(doc_uuid)
    assert len(rows) == 1
    assert rows[0][2] == sam_id
    assert rows[0][3].get("document_version_id") == new_ver_id

    # The v1 evidence row survives the re-arm untouched.
    async with get_sessionmaker()() as s:
        v1_acks = (
            (
                await s.execute(
                    select(Acknowledgement).where(
                        Acknowledgement.user_id == sam_id,
                        Acknowledgement.document_version_id == uuid.UUID(v1_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(v1_acks) == 1, "the v1 Acknowledgement row must survive the MAJOR re-arm"


# ---------------------------------------------------------------------------
# 5. The decide authz matrix
# ---------------------------------------------------------------------------


async def test_decide_authz_matrix(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """(a) a non-member 404-collapses; (b) an audience member WITHOUT document.acknowledge gets a
    calm 403 permission_denied; (c) a non-ack outcome is 422; (d) an Idempotency-Key replay is
    200-parity (same acknowledgement_id, exactly ONE row); (e) the flag flipped off mid-flight is
    a 409 ack_obligation_lapsed."""
    sam_id = await _setup_actors(subj)
    await grant_keys(subj.outsider, ())  # row only — NO keys, NOT in any audience
    u2_id = await grant_keys(subj.u2, ())  # row only — joins the audience key-less below
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    ho = _auth(token_factory, subj.outsider)
    h2 = _auth(token_factory, subj.u2)
    type_id = await s5.type_id("SOP")

    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-authz-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    sam_task = await _ack_task_for(doc_uuid, sam_id)

    # (a) outsider — not the assignee, not a candidate → 404 (the sensitive collapse).
    ra = await app_client.post(
        f"/api/v1/tasks/{sam_task}/decision", headers=ho, json={"outcome": "acknowledge"}
    )
    assert ra.status_code == 404, ra.text

    # (b) u2 joins the audience but holds no document.acknowledge → the task is honestly theirs,
    # the capability is missing: a calm 403 permission_denied (never a 404).
    add = await app_client.post(
        f"/api/v1/documents/{did}/distribution",
        headers=ha,
        json={"add_entries": [{"target_type": "user", "target_id": str(u2_id)}]},
    )
    assert add.status_code == 200, add.text
    await _run_sweep(document_id=doc_uuid)
    u2_task = await _ack_task_for(doc_uuid, u2_id)
    rb = await app_client.post(
        f"/api/v1/tasks/{u2_task}/decision", headers=h2, json={"outcome": "acknowledge"}
    )
    assert rb.status_code == 403, rb.text
    assert rb.json()["code"] == "permission_denied"

    # (c) Sam (with the key) but a non-ack outcome → 422.
    rc = await app_client.post(
        f"/api/v1/tasks/{sam_task}/decision", headers=hs, json={"outcome": "approve"}
    )
    assert rc.status_code == 422, rc.text

    # (d) Idempotency-Key replay parity: both 200, same acknowledgement_id, ONE row.
    idem = uuid.uuid4().hex
    r1 = await app_client.post(
        f"/api/v1/tasks/{sam_task}/decision",
        headers={**hs, "Idempotency-Key": idem},
        json={"outcome": "acknowledge"},
    )
    assert r1.status_code == 200, r1.text
    r2 = await app_client.post(
        f"/api/v1/tasks/{sam_task}/decision",
        headers={**hs, "Idempotency-Key": idem},
        json={"outcome": "acknowledge"},
    )
    assert r2.status_code == 200, r2.text
    b1, b2 = r1.json(), r2.json()
    assert b1["acknowledgement_id"] is not None
    assert b2["acknowledgement_id"] == b1["acknowledgement_id"], (
        f"replayed acknowledgement_id {b2['acknowledgement_id']!r} "
        f"!= original {b1['acknowledgement_id']!r}"
    )
    assert b2["replayed"] is True
    pinned_version = uuid.UUID(b1["document_version_id"])
    async with get_sessionmaker()() as s:
        sam_acks = (
            (
                await s.execute(
                    select(Acknowledgement).where(
                        Acknowledgement.user_id == sam_id,
                        Acknowledgement.document_version_id == pinned_version,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(sam_acks) == 1, f"replay must not add a second ack row, got {len(sam_acks)}"

    # (e) flag-off mid-flight: the obligation no longer stands → 409 ack_obligation_lapsed.
    async with get_sessionmaker()() as s:
        await s.execute(
            text(
                "UPDATE documented_information SET acknowledgement_required = false WHERE id = :id"
            ),
            {"id": doc_uuid},
        )
        await s.commit()
    re_ = await app_client.post(
        f"/api/v1/tasks/{sam_task}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert re_.status_code == 409, re_.text
    assert re_.json()["code"] == "ack_obligation_lapsed"


# ---------------------------------------------------------------------------
# 6. Left the audience → sweep cancels
# ---------------------------------------------------------------------------


async def test_left_audience_cancelled_by_sweep(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """Removing the entry (DELETE → 204, audited DISTRIBUTION_UPDATED with scope_ref=identifier)
    makes the next sweep cancel the open obligation; coverage required drops to 0."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-left-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    await _ack_task_for(doc_uuid, sam_id)  # the obligation exists
    rows = await _open_ack_rows(doc_uuid)
    assert len(rows) == 1
    instance_id = rows[0][1]

    doc_body = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()
    identifier = doc_body["identifier"]

    dist = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()
    assert len(dist["entries"]) == 1
    entry_id = dist["entries"][0]["id"]

    rd = await app_client.delete(f"/api/v1/documents/{did}/distribution/{entry_id}", headers=ha)
    assert rd.status_code == 204, rd.text

    # The distribution change is audited and surfaces on the per-document trail (scope_ref).
    async with get_sessionmaker()() as s:
        events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_type == AuditObjectType.document,
                        AuditEvent.object_id == doc_uuid,
                        AuditEvent.event_type == EventType.DISTRIBUTION_UPDATED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert events, "expected DISTRIBUTION_UPDATED audit events for this doc"
        assert all(e.scope_ref == identifier for e in events)
        assert any((e.before or {}).get("id") == entry_id for e in events), (
            "the DELETE's audit event must carry the removed entry in `before`"
        )

    await _run_sweep(document_id=doc_uuid)
    async with get_sessionmaker()() as s:
        inst = await s.get(WorkflowInstance, instance_id)
        assert inst is not None
        assert inst.current_state == "CANCELLED", (
            f"a left-audience obligation must be CANCELLED, got {inst.current_state}"
        )

    cov = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()[
        "coverage"
    ]
    assert cov["required"] == 0


# ---------------------------------------------------------------------------
# 7. Target-kind validation (R43 enum-4-accept-2)
# ---------------------------------------------------------------------------


async def test_target_kind_422_and_unknown_target_404(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """process → 422 target_kind_deferred; an unknown user uuid → 404; a nonsense kind → 422
    validation_error. None of the rejected adds may land."""
    await s5.grant_lifecycle(subj.a)
    await grant_keys(subj.a, ("document.distribute",))
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")
    doc = await _create(app_client, ha, type_id)
    did = doc["id"]

    rp = await app_client.post(
        f"/api/v1/documents/{did}/distribution",
        headers=ha,
        json={"add_entries": [{"target_type": "process", "target_id": str(uuid.uuid4())}]},
    )
    assert rp.status_code == 422, rp.text
    assert rp.json()["code"] == "target_kind_deferred"

    ru = await app_client.post(
        f"/api/v1/documents/{did}/distribution",
        headers=ha,
        json={"add_entries": [{"target_type": "user", "target_id": str(uuid.uuid4())}]},
    )
    assert ru.status_code == 404, ru.text

    rn = await app_client.post(
        f"/api/v1/documents/{did}/distribution",
        headers=ha,
        json={"add_entries": [{"target_type": "nonsense", "target_id": str(uuid.uuid4())}]},
    )
    assert rn.status_code == 422, rn.text
    assert rn.json()["code"] == "validation_error"

    dist = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()
    assert dist["entries"] == [], "no rejected add may have landed"


# ---------------------------------------------------------------------------
# 8. Append-only DB grant (migration 0048 REVOKE)
# ---------------------------------------------------------------------------


async def test_acknowledgement_append_only_db_grant(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """The 0048 REVOKE UPDATE,DELETE is live: the app role cannot mutate or delete an
    acknowledgement row. Real because the conftest wires the app (and get_sessionmaker) to the
    NON-OWNER easysynq_app role — the rejection is the grant, not a mock."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    type_id = await s5.type_id("SOP")

    _did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-worm-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    task_id = await _ack_task_for(doc_uuid, sam_id)
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert dr.status_code == 200, dr.text
    ack_id = uuid.UUID(dr.json()["acknowledgement_id"])

    async with get_sessionmaker()() as s:
        with pytest.raises(ProgrammingError):
            await s.execute(
                text("UPDATE acknowledgement SET client_ip = 'tamper' WHERE id = :id"),
                {"id": ack_id},
            )
    async with get_sessionmaker()() as s:
        with pytest.raises(ProgrammingError):
            await s.execute(text("DELETE FROM acknowledgement WHERE id = :id"), {"id": ack_id})

    # The row is intact.
    async with get_sessionmaker()() as s:
        row = await s.get(Acknowledgement, ack_id)
        assert row is not None
        assert row.client_ip != "tamper"


# ---------------------------------------------------------------------------
# 9. The named matrix: gate + shape
# ---------------------------------------------------------------------------


async def test_matrix_gated_and_shaped(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """GET /documents/{id}/acknowledgements is R42-gated (Sam without document.distribute → 403)
    and shaped: pending-with-due_at before the ack, acknowledged-with-label after."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    type_id = await s5.type_id("SOP")

    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-matrix-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    task_id = await _ack_task_for(doc_uuid, sam_id)

    # Sam holds document.acknowledge but NOT document.distribute → 403 on the named matrix.
    rs = await app_client.get(f"/api/v1/documents/{did}/acknowledgements", headers=hs)
    assert rs.status_code == 403, rs.text

    # The distributor sees the matrix; Sam's row is pending with a due date.
    rm = await app_client.get(f"/api/v1/documents/{did}/acknowledgements", headers=ha)
    assert rm.status_code == 200, rm.text
    sam_rows = [r for r in rm.json() if r["user_id"] == str(sam_id)]
    assert len(sam_rows) == 1, f"expected exactly 1 matrix row for Sam, got {len(sam_rows)}"
    assert sam_rows[0]["status"] == "pending"
    assert sam_rows[0]["due_at"] is not None
    assert sam_rows[0]["acknowledged_at"] is None
    assert sam_rows[0]["acknowledged_revision_label"] is None

    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert dr.status_code == 200, dr.text

    rm2 = await app_client.get(f"/api/v1/documents/{did}/acknowledgements", headers=ha)
    assert rm2.status_code == 200, rm2.text
    sam_rows2 = [r for r in rm2.json() if r["user_id"] == str(sam_id)]
    assert len(sam_rows2) == 1
    assert sam_rows2[0]["status"] == "acknowledged"
    assert sam_rows2[0]["acknowledged_at"] is not None
    assert sam_rows2[0]["acknowledged_revision_label"] is not None
    assert sam_rows2[0]["due_at"] is None


# ---------------------------------------------------------------------------
# 10. Obsolete cancels obligations (Pass B)
# ---------------------------------------------------------------------------


async def test_obsolete_cancels_obligations(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """Obsoleting the doc clears the Effective pointer; the next sweep's Pass B cancels the open
    obligation (instance CANCELLED, task SKIPPED) and coverage degrades to an honest null."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-obsolete-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    task_id = await _ack_task_for(doc_uuid, sam_id)
    rows = await _open_ack_rows(doc_uuid)
    assert len(rows) == 1
    instance_id = rows[0][1]

    ro = await app_client.post(
        f"/api/v1/documents/{did}/obsolete",
        headers=ha,
        json={"reason": "ack obsolete-cancel test"},
    )
    assert ro.status_code == 200, ro.text

    await _run_sweep(document_id=doc_uuid)
    async with get_sessionmaker()() as s:
        inst = await s.get(WorkflowInstance, instance_id)
        assert inst is not None
        assert inst.current_state == "CANCELLED", (
            f"an obsoleted doc's obligation must be CANCELLED, got {inst.current_state}"
        )
        task_row = await s.get(Task, uuid.UUID(task_id))
        assert task_row is not None
        assert task_row.state is TaskState.SKIPPED

    # Coverage degrades to an honest null (no Effective version), not a 0/0.
    cov = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()[
        "coverage"
    ]
    assert cov is None


# ---------------------------------------------------------------------------
# 11. The snapshot freeze (Task 10)
# ---------------------------------------------------------------------------


async def test_snapshot_carries_ack_keys(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """doc 04 §6.1: a check-in freezes the flag + entry list into the new version's
    metadata_snapshot (the version self-describes its audience/ack policy)."""
    await s5.grant_lifecycle(subj.a)
    await grant_keys(subj.a, ("document.distribute",))
    sam_id = await grant_keys(subj.sam, ())  # row only — the entry's target principal
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")

    doc = await _create(app_client, ha, type_id)
    did = doc["id"]
    doc_uuid = uuid.UUID(did)

    # v1: checked in BEFORE any distribution exists → its snapshot must freeze False/[].
    co1 = await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    assert co1.status_code == 200, co1.text
    sha1 = await _upload(app_client, ha, did, f"ack-snapshot-v1-{subj.a}".encode())
    ci1 = await _checkin(app_client, ha, did, sha1, change_reason="v1", change_significance="MAJOR")
    assert ci1.status_code == 201, ci1.text

    # The distribution lands AFTER v1's check-in (flag on + Sam as a user target).
    dist = await app_client.post(
        f"/api/v1/documents/{did}/distribution",
        headers=ha,
        json={
            "acknowledgement_required": True,
            "add_entries": [{"target_type": "user", "target_id": str(sam_id)}],
        },
    )
    assert dist.status_code == 200, dist.text

    # v2: the next check-in freezes the NOW-current flag + entries.
    co2 = await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    assert co2.status_code == 200, co2.text
    sha2 = await _upload(app_client, ha, did, f"ack-snapshot-v2-{subj.a}".encode())
    ci2 = await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MINOR")
    assert ci2.status_code == 201, ci2.text

    async with get_sessionmaker()() as s:
        versions = (
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == doc_uuid)
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )
    assert len(versions) == 2, f"expected exactly 2 versions, got {len(versions)}"

    # v1's snapshot is point-in-time: the distribution did not exist at its check-in.
    ms1 = versions[0].metadata_snapshot
    assert ms1["acknowledgement_required"] is False
    assert ms1["distribution"] == []

    # v2 (the newest) carries the flag + the serialized entry list.
    ms2 = versions[-1].metadata_snapshot
    assert ms2["acknowledgement_required"] is True
    assert ms2["distribution"] == [
        {"target_type": "user", "target_id": str(sam_id), "ack_required": True}
    ]


# ---------------------------------------------------------------------------
# 12. A never-Effective MAJOR draft must not move the R43 boundary
# ---------------------------------------------------------------------------


async def test_abandoned_major_draft_does_not_move_boundary(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """diff-critic MAJOR: a never-Effective MAJOR draft below a later MINOR release must not
    re-arm the audience (R43 carry-forward holds over the ever-governed chain only)."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    hs = _auth(token_factory, subj.sam)
    type_id = await s5.type_id("SOP")

    # v1 (seq 1, MAJOR) releases; Sam acknowledges it — satisfied at the seq-1 boundary.
    did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-phantom-v1-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    task_id = await _ack_task_for(doc_uuid, sam_id)
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hs, json={"outcome": "acknowledge"}
    )
    assert dr.status_code == 200, dr.text

    # The abandoned MAJOR draft (seq 2). Flow choice: T8 discard-draft is deferred to v1 (absent
    # from the FSM table, domain/vault/lifecycle.py — no delete-draft route exists), and a
    # check-in ALWAYS creates a NEW DocumentVersion row (services/vault/service.py::checkin),
    # so the SIMPLEST legal flow that strands a MAJOR row in a never-Effective state is:
    # start-revision → check in a MAJOR draft → never submit it. The check-in releases the edit
    # lock and the checkout endpoint guards only the lock (no doc-state gate), so a fresh
    # checkout → MINOR check-in mints seq 3; T9 submit acts on the LATEST version
    # (repository.latest_version), so seq 3 reviews/releases while seq 2 sits at
    # version_state=Draft forever — exactly the phantom the boundary must ignore.
    sr = await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    assert sr.status_code == 200, sr.text
    sha2 = await _upload(app_client, ha, did, f"ack-phantom-abandoned-{subj.a}".encode())
    ci2 = await _checkin(
        app_client, ha, did, sha2, change_reason="abandoned cut", change_significance="MAJOR"
    )
    assert ci2.status_code == 201, ci2.text

    # The replacement MINOR cut (seq 3) goes through review and releases.
    co = await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    assert co.status_code == 200, co.text
    sha3 = await _upload(app_client, ha, did, f"ack-phantom-v3-{subj.a}".encode())
    ci3 = await _checkin(
        app_client, ha, did, sha3, change_reason="minor rev", change_significance="MINOR"
    )
    assert ci3.status_code == 201, ci3.text
    sub = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sub.status_code == 200, sub.text
    wf_task = await s5.task_for_doc(did)
    dec = await app_client.post(
        f"/api/v1/tasks/{wf_task}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text

    # Pin the defect's precondition: seq 2 IS a MAJOR row stuck at Draft (never governed) BELOW
    # the released MINOR seq 3 — without the ever-governed filter it becomes the boundary.
    async with get_sessionmaker()() as s:
        states = {
            seq: (sig, state)
            for seq, sig, state in (
                await s.execute(
                    select(
                        DocumentVersion.version_seq,
                        DocumentVersion.change_significance,
                        DocumentVersion.version_state,
                    ).where(DocumentVersion.document_id == doc_uuid)
                )
            ).all()
        }
    assert states[2] == (ChangeSignificance.MAJOR, VersionState.Draft), states
    assert states[3] == (ChangeSignificance.MINOR, VersionState.Effective), states

    # R43: the phantom must not move the boundary — the post-release sweep mints nothing,
    # Sam's v1 ack carries forward, and no open obligation exists on this doc.
    result = await _run_sweep(document_id=doc_uuid, trigger="release")
    assert result["tasks_created"] == 0, result

    cov = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()[
        "coverage"
    ]
    assert cov == {"required": 1, "acknowledged": 1, "pending": 0, "overdue": 0}
    assert await _open_ack_rows(doc_uuid) == [], (
        "an abandoned MAJOR draft must leave no fresh obligation on this doc"
    )


# ---------------------------------------------------------------------------
# 13. Engine lock freshness — the S-drift-1 identity-map trap, engine edition
# ---------------------------------------------------------------------------


async def test_decide_sees_sweep_cancel_committed_mid_wait(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """The S-drift-1 trap, engine edition: a decide that waited on the instance lock while a
    sweep-cancel committed must see the FRESH (SKIPPED/CANCELLED) state — 409, never a clobber
    of CANCELLED→COMPLETED (engineering-patterns: prime via session.get, commit via B, locked
    load on S, assert B's value)."""
    sam_id = await _setup_actors(subj)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    _did, doc_uuid = await _release_ack_doc(
        app_client,
        ha,
        hb,
        type_id,
        f"ack-lockfresh-{subj.a}".encode(),
        entries=[{"target_type": "user", "target_id": str(sam_id)}],
    )
    await _run_sweep(document_id=doc_uuid, trigger="release")
    task_uuid = uuid.UUID(await _ack_task_for(doc_uuid, sam_id))

    sm = get_sessionmaker()
    async with sm() as s_a, sm() as s_b:
        # Session A — the route-shaped pre-load (decide_endpoint's get_task + get_instance for
        # dispatch): both rows enter A's identity map in their OPEN state (the trap's
        # precondition).
        task_a = await wf_repo.get_task(s_a, task_uuid)
        assert task_a is not None and task_a.state is TaskState.PENDING
        instance_a = await wf_repo.get_instance(s_a, task_a.instance_id)
        assert instance_a is not None
        instance_id = instance_a.id
        assert instance_a.current_state != "CANCELLED"
        actor = await s_a.get(AppUser, sam_id)
        assert actor is not None

        # Session B — the sweep's OWN cancel write (PENDING task → SKIPPED, instance →
        # CANCELLED) commits while A still holds the stale snapshots. No real lock-wait is
        # needed: the staleness lives in A's identity map regardless of whether A blocked.
        assert await _cancel_instance(s_b, instance_id) is True
        await s_b.commit()

        # Session A decides. The engine's locked loads carry populate_existing, so they see
        # B's committed SKIPPED/CANCELLED — the bare-SKIPPED "Task not decidable" 409. Without
        # the fix the pre-lock PENDING snapshot survives the lock and decide writes the
        # clobber (SKIPPED→DONE, CANCELLED→COMPLETED, plus a TaskOutcome row).
        with pytest.raises(ProblemException) as excinfo:
            await wf_engine.decide(
                s_a, task_a, actor, outcome="acknowledge", comment=None, idempotency_key=None
            )
        assert excinfo.value.status == 409
        assert excinfo.value.title == "Task not decidable"
        await s_a.rollback()

    # Fresh session: B's committed cancel stands untouched — no clobber, no outcome row.
    async with sm() as s:
        task_row = await s.get(Task, task_uuid)
        assert task_row is not None and task_row.state is TaskState.SKIPPED
        inst_row = await s.get(WorkflowInstance, instance_id)
        assert inst_row is not None and inst_row.current_state == "CANCELLED"
        outcome_row = (
            await s.execute(select(TaskOutcome).where(TaskOutcome.task_id == task_uuid))
        ).scalar_one_or_none()
        assert outcome_row is None, "a 409'd decide must leave no TaskOutcome row"
