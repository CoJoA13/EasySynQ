"""S-wf-engine integration proofs — the declarative multi-stage engine at the SERVICE level (no HTTP
yet; the per-subject permission/scope endpoint wiring lands in S-capa-2), over testcontainer PG.

The engine is exercised against SYNTHETIC ``workflow_definition``s for a synthetic subject (the
subject_id has no FK). Every definition key is uuid-salted so per-test seeds never collide on
``uq_workflow_definition_effective_per_key`` in the shared session DB; rows leak but never collide
(the test_approval salt convention) → no cleanup fixture. The S5 DOCUMENT approval path
(test_approval.py) stays byte-identical and is the regression backstop.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._workflow_enums import (
    TaskState,
    WorkflowStageMode,
    WorkflowSubjectType,
)
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.models.workflow import (
    Task,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowStage,
)
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.problems import ProblemException
from easysynq_api.services.workflow import engine

from .test_vault import _ensure_user

pytestmark = pytest.mark.integration


# --- helpers --------------------------------------------------------------------------------


async def _org_id() -> uuid.UUID:
    from easysynq_api.db.models.organization import Organization

    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _user(prefix: str) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        u = await _ensure_user(s, f"kc-{prefix}-{uuid.uuid4().hex[:10]}")
        await s.commit()
        return u.id


async def _role_with(members: list[uuid.UUID]) -> str:
    """Create a uniquely-named role and assign ``members`` to it (the candidate-pool seam)."""
    name = f"WFRole-{uuid.uuid4().hex[:10]}"
    async with get_sessionmaker()() as s:
        org_id = (await s.get(AppUser, members[0])).org_id if members else await _org_id()
        role = Role(org_id=org_id, name=name, description="engine test role", is_reserved=False)
        s.add(role)
        await s.flush()
        for uid in members:
            s.add(
                RoleAssignment(
                    org_id=org_id, user_id=uid, role_id=role.id, bound_scope={"level": "SYSTEM"}
                )
            )
        await s.commit()
    return name


async def _seed_definition(
    org_id: uuid.UUID,
    stages: list[dict[str, Any]],
    *,
    entry: str | None = None,
    default_sla: dict[str, Any] | None = None,
) -> str:
    """Seed an effective definition + its stages; returns the uuid-salted key."""
    key = f"wf_engine_test_{uuid.uuid4().hex[:8]}"
    async with get_sessionmaker()() as s:
        definition = WorkflowDefinition(
            org_id=org_id,
            key=key,
            version=1,
            effective=True,
            subject_type=WorkflowSubjectType.CAPA,
            stages={"entry": entry or stages[0]["key"]},
            default_sla=default_sla,
        )
        s.add(definition)
        await s.flush()
        for st in stages:
            s.add(
                WorkflowStage(
                    org_id=org_id,
                    definition_id=definition.id,
                    key=st["key"],
                    mode=WorkflowStageMode(st.get("mode", "PARALLEL")),
                    assignees=st.get("assignees"),
                    quorum=st.get("quorum"),
                    transitions=st.get("transitions"),
                    sla=st.get("sla"),
                    signature=st.get("signature"),
                )
            )
        await s.commit()
    return key


async def _instantiate(
    key: str, subject_id: uuid.UUID, context: dict[str, Any] | None, actor_id: uuid.UUID
) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        actor = await s.get(AppUser, actor_id)
        inst = await engine.instantiate(
            s,
            org_id=actor.org_id,
            definition_key=key,
            subject_type=WorkflowSubjectType.CAPA,
            subject_id=subject_id,
            context=context,
            actor=actor,
        )
        await s.commit()
        return inst.id


async def _decide(
    task_id: uuid.UUID, actor_id: uuid.UUID, outcome: str, *, key: str | None = None
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        task = await s.get(Task, task_id)
        actor = await s.get(AppUser, actor_id)
        return await engine.decide(
            s, task, actor, outcome=outcome, comment=None, idempotency_key=key
        )


async def _instance(instance_id: uuid.UUID) -> WorkflowInstance:
    async with get_sessionmaker()() as s:
        return await s.get(WorkflowInstance, instance_id)


async def _stage_tasks(instance_id: uuid.UUID, stage_key: str) -> list[Task]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(Task).where(Task.instance_id == instance_id, Task.stage_key == stage_key)
                )
            )
            .scalars()
            .all()
        )


async def _audit_count(instance_id: uuid.UUID, event_type: EventType) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == instance_id,
                    AuditEvent.object_type == AuditObjectType.workflow_instance,
                    AuditEvent.event_type == event_type,
                )
            )
        ).scalar_one()


def _approve_stage(key: str, role: str, *, quorum: dict[str, Any], to: str | None = None) -> dict:
    transitions = [{"on": "reject", "to": "REJECTED"}]
    if to:
        transitions.insert(0, {"on": "satisfied", "to": to})
    return {
        "key": key,
        "mode": "PARALLEL",
        "assignees": {"roles": [role], "task_type": "APPROVE", "action_expected": "approve"},
        "quorum": quorum,
        "transitions": transitions,
    }


# --- tests ----------------------------------------------------------------------------------


async def test_multi_stage_sequential_flow_happy_path(app_under_test: object) -> None:
    org = await _org_id()
    a, b = await _user("seqa"), await _user("seqb")
    ra, rb = await _role_with([a]), await _role_with([b])
    key = await _seed_definition(
        org,
        [
            _approve_stage("first", ra, quorum={"type": "ANY"}, to="second"),
            _approve_stage("second", rb, quorum={"type": "ANY"}),
        ],
        entry="first",
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    assert (await _instance(iid)).current_state == "first"

    t1 = (await _stage_tasks(iid, "first"))[0]
    r1 = await _decide(t1.id, a, "approve")
    assert r1["stage_state"] == "MET"
    assert (await _instance(iid)).current_state == "second"

    t2 = (await _stage_tasks(iid, "second"))[0]
    r2 = await _decide(t2.id, b, "approve")
    assert r2["stage_state"] == "MET"
    assert (await _instance(iid)).current_state == "COMPLETED"
    # one TASK_DECIDED per decision; STAGE_ADVANCED on instantiate + each advance.
    assert await _audit_count(iid, EventType.TASK_DECIDED) == 2
    assert await _audit_count(iid, EventType.STAGE_ADVANCED) >= 2


async def test_parallel_n_of_m_distinct_approvers_advances(app_under_test: object) -> None:
    org = await _org_id()
    a, b = await _user("nm-a"), await _user("nm-b")
    role = await _role_with([a, b])
    key = await _seed_definition(
        org, [_approve_stage("gate", role, quorum={"type": "N_OF_M", "n": 2, "m": 2})], entry="gate"
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    tasks = await _stage_tasks(iid, "gate")
    assert len(tasks) == 2  # one task per candidate
    by_user = {t.assignee_user_id: t for t in tasks}

    r1 = await _decide(by_user[a].id, a, "approve")
    assert r1["stage_state"] == "PENDING"  # need 2 distinct
    r2 = await _decide(by_user[b].id, b, "approve")
    assert r2["stage_state"] == "MET"
    assert (await _instance(iid)).current_state == "COMPLETED"


async def test_distinct_approver_guard_blocks_one_actor_satisfying_quorum(
    app_under_test: object,
) -> None:
    org = await _org_id()
    a, b = await _user("dg-a"), await _user("dg-b")
    role = await _role_with([a, b])
    key = await _seed_definition(
        org, [_approve_stage("gate", role, quorum={"type": "N_OF_M", "n": 2, "m": 2})], entry="gate"
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    tasks = await _stage_tasks(iid, "gate")
    await _decide(tasks[0].id, a, "approve")
    # actor A tries to also decide the sibling task → 409 (one actor cannot satisfy a 2-of-2 alone)
    with pytest.raises(ProblemException) as exc:
        await _decide(tasks[1].id, a, "approve")
    assert exc.value.status == 409
    assert (await _instance(iid)).current_state == "gate"  # still pending


async def test_n_of_m_early_fail_on_reject(app_under_test: object) -> None:
    org = await _org_id()
    a, b = await _user("ef-a"), await _user("ef-b")
    role = await _role_with([a, b])
    key = await _seed_definition(
        org, [_approve_stage("gate", role, quorum={"type": "N_OF_M", "n": 2, "m": 2})], entry="gate"
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    tasks = await _stage_tasks(iid, "gate")
    by_user = {t.assignee_user_id: t for t in tasks}
    await _decide(by_user[a].id, a, "approve")
    r = await _decide(by_user[b].id, b, "reject")  # 2-of-2 now unreachable → early FAILED
    assert r["stage_state"] == "FAILED"
    assert (await _instance(iid)).current_state == "REJECTED"
    assert await _audit_count(iid, EventType.STAGE_FAILED) == 1


async def test_under_quorum_fails_closed_at_instantiate(app_under_test: object) -> None:
    org = await _org_id()
    a = await _user("uq-a")
    role = await _role_with([a])  # only 1 member, but 2-of-2 required
    key = await _seed_definition(
        org, [_approve_stage("gate", role, quorum={"type": "N_OF_M", "n": 2, "m": 2})], entry="gate"
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    assert (await _instance(iid)).current_state == "NEEDS_ATTENTION"  # never silently passes
    assert await _stage_tasks(iid, "gate") == []


async def test_conditional_quorum_resolution_and_fail_closed(app_under_test: object) -> None:
    org = await _org_id()
    a, b = await _user("cq-a"), await _user("cq-b")
    role = await _role_with([a, b])
    cond = {
        "type": "conditional",
        "rule": [
            {"when": "severity == 'Critical'", "quorum": {"type": "N_OF_M", "n": 2, "m": 2}},
            {"default": {"type": "ANY"}},
        ],
    }
    key = await _seed_definition(org, [_approve_stage("gate", role, quorum=cond)], entry="gate")

    # Minor → default ANY: one approval completes.
    iid_minor = await _instantiate(key, uuid.uuid4(), {"severity": "Minor"}, a)
    tasks = await _stage_tasks(iid_minor, "gate")
    r = await _decide(tasks[0].id, a, "approve")
    assert r["stage_state"] == "MET"

    # Missing discriminator → fail closed (NOT the weakest default).
    iid_missing = await _instantiate(key, uuid.uuid4(), {"other": "x"}, a)
    assert (await _instance(iid_missing)).current_state == "NEEDS_ATTENTION"
    assert await _stage_tasks(iid_missing, "gate") == []


async def test_router_branch_and_cycle_guard(app_under_test: object) -> None:
    org = await _org_id()
    a = await _user("rt-a")
    role = await _role_with([a])
    router = {
        "key": "route",
        "mode": "ROUTER",
        "transitions": [
            {"when": "severity == 'Critical'", "to": "crit"},
            {"default": "minor"},
        ],
    }
    crit = _approve_stage("crit", role, quorum={"type": "ANY"})
    minor = _approve_stage("minor", role, quorum={"type": "ANY"})
    key = await _seed_definition(org, [router, crit, minor], entry="route")

    iid = await _instantiate(key, uuid.uuid4(), {"severity": "Critical"}, a)
    assert (await _instance(iid)).current_state == "crit"  # routed past the task-less ROUTER
    assert len(await _stage_tasks(iid, "crit")) == 1

    # A self-cycling router fails closed (no hang).
    loop_router = {
        "key": "loop",
        "mode": "ROUTER",
        "transitions": [{"default": "loop"}],
    }
    key2 = await _seed_definition(org, [loop_router], entry="loop")
    iid2 = await _instantiate(key2, uuid.uuid4(), None, a)
    assert (await _instance(iid2)).current_state == "NEEDS_ATTENTION"


async def test_signature_spec_threaded_no_row_written(app_under_test: object) -> None:
    org = await _org_id()
    a = await _user("sig-a")
    role = await _role_with([a])
    stage = _approve_stage("gate", role, quorum={"type": "ANY"})
    stage["signature"] = {"meaning": "approval", "method": "SESSION"}
    key = await _seed_definition(org, [stage], entry="gate")
    subject = uuid.uuid4()
    iid = await _instantiate(key, subject, None, a)
    t = (await _stage_tasks(iid, "gate"))[0]
    r = await _decide(t.id, a, "approve")
    assert r["signature_spec"] == {"meaning": "approval", "method": "SESSION"}  # threaded
    # NO signature_event row written this slice (no legal signed object for a synthetic subject).
    async with get_sessionmaker()() as s:
        n = (
            await s.execute(
                select(func.count())
                .select_from(SignatureEvent)
                .where(SignatureEvent.signed_object_id == subject)
            )
        ).scalar_one()
    assert n == 0


async def test_idempotent_replay_and_conflict(app_under_test: object) -> None:
    org = await _org_id()
    a = await _user("idem-a")
    role = await _role_with([a])
    key = await _seed_definition(
        org, [_approve_stage("gate", role, quorum={"type": "ANY"})], entry="gate"
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    t = (await _stage_tasks(iid, "gate"))[0]
    await _decide(t.id, a, "approve", key="K1")
    # same key → replay (no second outcome, no error)
    replay = await _decide(t.id, a, "approve", key="K1")
    assert replay["replayed"] is True
    async with get_sessionmaker()() as s:
        from easysynq_api.db.models.workflow import TaskOutcome

        n = (
            await s.execute(
                select(func.count()).select_from(TaskOutcome).where(TaskOutcome.task_id == t.id)
            )
        ).scalar_one()
    assert n == 1
    # a no-key (or different-key) decision on a DONE task → 409
    with pytest.raises(ProblemException) as exc:
        await _decide(t.id, a, "approve", key="K2")
    assert exc.value.status == 409


async def test_auto_skip_replay_is_not_an_error(app_under_test: object) -> None:
    org = await _org_id()
    a, b = await _user("sk-a"), await _user("sk-b")
    role = await _role_with([a, b])
    key = await _seed_definition(
        org, [_approve_stage("gate", role, quorum={"type": "ANY"})], entry="gate"
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    tasks = await _stage_tasks(iid, "gate")
    by_user = {t.assignee_user_id: t for t in tasks}
    await _decide(by_user[a].id, a, "approve")  # ANY → MET, b's task auto-SKIPPED
    skipped = next(t for t in await _stage_tasks(iid, "gate") if t.assignee_user_id == b)
    assert skipped.state is TaskState.SKIPPED
    assert skipped.client_token == engine._QUORUM_SKIP  # the quorum-skip marker (not a real key)
    # b retries their auto-skipped task → a defined no-op, NOT a 409.
    res = await _decide(skipped.id, b, "approve")
    assert res["stage_state"] == "ALREADY_SATISFIED"


async def test_concurrent_quorum_advances_exactly_once(app_under_test: object) -> None:
    """Two sibling N_OF_M approvers race on separate sessions → the instance advances exactly once
    (the instance-row FOR UPDATE serialization point), mirroring test_lifecycle's release race."""
    org = await _org_id()
    a, b = await _user("cc-a"), await _user("cc-b")
    role = await _role_with([a, b])
    key = await _seed_definition(
        org, [_approve_stage("gate", role, quorum={"type": "N_OF_M", "n": 2, "m": 2})], entry="gate"
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    tasks = await _stage_tasks(iid, "gate")
    by_user = {t.assignee_user_id: t for t in tasks}
    results = await asyncio.gather(
        _decide(by_user[a].id, a, "approve"),
        _decide(by_user[b].id, b, "approve"),
        return_exceptions=True,
    )
    # Neither racer errors; exactly one STAGE_ADVANCED is recorded and the instance completes once.
    assert all(not isinstance(r, Exception) for r in results), results
    assert (await _instance(iid)).current_state == "COMPLETED"
    assert (
        await _audit_count(iid, EventType.STAGE_ADVANCED) == 2
    )  # instantiate + the single advance


async def test_all_quorum_reject_fails_stage(app_under_test: object) -> None:
    org = await _org_id()
    a, b = await _user("all-a"), await _user("all-b")
    role = await _role_with([a, b])
    key = await _seed_definition(
        org, [_approve_stage("gate", role, quorum={"type": "ALL"})], entry="gate"
    )
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    tasks = await _stage_tasks(iid, "gate")
    by_user = {t.assignee_user_id: t for t in tasks}
    await _decide(by_user[a].id, a, "approve")  # ALL needs both → still PENDING
    assert (await _instance(iid)).current_state == "gate"
    r = await _decide(by_user[b].id, b, "reject")  # any reject under ALL → FAILED
    assert r["stage_state"] == "FAILED"
    assert (await _instance(iid)).current_state == "REJECTED"
    assert await _audit_count(iid, EventType.STAGE_FAILED) == 1


async def test_null_quorum_fails_closed_at_instantiate(app_under_test: object) -> None:
    org = await _org_id()
    a = await _user("nq-a")
    role = await _role_with([a])
    # a stage with NO quorum key → resolve_conditional(None) → fail closed
    stage = {
        "key": "gate",
        "mode": "PARALLEL",
        "assignees": {"roles": [role], "task_type": "APPROVE"},
        "transitions": [],
    }
    key = await _seed_definition(org, [stage], entry="gate")
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    assert (await _instance(iid)).current_state == "NEEDS_ATTENTION"
    assert await _stage_tasks(iid, "gate") == []


async def test_invalid_signature_spec_fails_closed_at_instantiate(app_under_test: object) -> None:
    org = await _org_id()
    a = await _user("badsig-a")
    role = await _role_with([a])
    stage = _approve_stage("gate", role, quorum={"type": "ANY"})
    stage["signature"] = {"meaning": "not_a_real_meaning"}  # invalid SignatureMeaning
    key = await _seed_definition(org, [stage], entry="gate")
    iid = await _instantiate(key, uuid.uuid4(), None, a)
    assert (await _instance(iid)).current_state == "NEEDS_ATTENTION"  # never threads a garbage spec
    assert await _stage_tasks(iid, "gate") == []
