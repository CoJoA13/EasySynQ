"""The declarative multi-stage workflow engine (slice S-wf-engine; doc 10 §2).

A GENERIC engine over the existing workflow_* tables — SEPARATE from the S5 single-stage DOCUMENT
approval (``service.decide`` / ``instantiate_approval`` stay byte-identical; the S5 tests pin them).
Its first consumer is S-capa-2's severity-routed CAPA ActionPlan approval. This slice ships the
engine + its synthetic-definition tests; HTTP wiring (the per-subject permission/scope map) + the
real ``signature_event`` row write (a CAPA stage's signed object) land in S-capa-2.

Load-bearing invariants (each a confirmed design-critic finding):
- **Serialization point = the instance row.** ``decide`` locks ``workflow_instance`` FOR UPDATE
  first (then the task), so all sibling quorum approvers serialize → quorum-count + advance +
  sibling-skip + next-stage materialization is one atomic transaction.
- **Quorum counts DISTINCT approvers** (``{outcome.decided_by}``), never raw task rows; one actor
  cannot satisfy a multi-task quorum (a service-level distinct-approver guard backstops it until the
  S-capa-2 endpoint adds per-subject SoD).
- **Tri-state quorum with early-fail** (``domain.workflow.quorum_state``): MET advances, FAILED
  fails fast (remaining pending can no longer reach the threshold), PENDING waits.
- **Fail-closed totality** (doc 10 §2.3): a missing conditional discriminator, an empty/under-quorum
  candidate pool, or a ROUTER cycle sets ``current_state="NEEDS_ATTENTION"`` and materializes no
  advancing tasks — never a silent downgrade, never an exception under the lock.
- **Cross-role conjunction is sequential stages**, NOT a single merged-pool N_OF_M (the engine's
  N_OF_M is single-role-pool; S-capa-2 composes Critical as ``qm→top_mgmt`` chained stages).
- **The signature SPEC is threaded into the result; NO ``signature_event`` row is written** this
  slice (no legal signed object for a synthetic/CAPA subject yet).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._signature_enums import SignatureMeaning
from ...db.models._workflow_enums import TaskOutcomeKind, TaskState, TaskType, WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.workflow import Task, TaskOutcome, WorkflowInstance, WorkflowStage
from ...domain.workflow import (
    evaluate_condition,
    quorum_state,
    referenced_keys,
    required_approvals,
    resolve_conditional,
)
from ...logging import request_id_var
from ...problems import ProblemException
from . import repository as wf_repo

# Terminal / sentinel instance states (not stage keys).
NEEDS_ATTENTION = "NEEDS_ATTENTION"
COMPLETED = "COMPLETED"
REJECTED = "REJECTED"

# Decision outcomes that advance a quorum vs that fail it.
_POSITIVE = {
    TaskOutcomeKind.approve,
    TaskOutcomeKind.complete,
    TaskOutcomeKind.verify,
    TaskOutcomeKind.acknowledge,
}
_NEGATIVE = {TaskOutcomeKind.reject, TaskOutcomeKind.changes_requested}
_SUCCESS_ON = {"satisfied", "stage_satisfied", "approve"}
_REJECT_ON = {"reject", "fail"}
_QUORUM_SKIP = "quorum_skip"  # marker stamped on a task auto-skipped when its stage closed
_SIG_MEANINGS = {m.value for m in SignatureMeaning}


def _valid_signature_spec(spec: Any) -> bool:
    """True if a stage's signature spec is absent or carries a known ``SignatureMeaning``. The full
    method / signed-object validation is S-capa-2's job (where the row is written)."""
    if spec is None:
        return True
    return isinstance(spec, dict) and spec.get("meaning") in _SIG_MEANINGS


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _emit(
    session: AsyncSession,
    instance: WorkflowInstance,
    actor: AppUser | None,
    event_type: EventType,
    *,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a per-transition workflow audit row BEFORE commit (object_type=workflow_instance), via
    direct session.add — the engine deliberately bypasses VaultAuditSink (its object-type map has no
    workflow key), mirroring the records/audits emit pattern."""
    session.add(
        AuditEvent(
            org_id=instance.org_id,
            occurred_at=_now(),
            actor_id=actor.id if actor else None,
            actor_type=ActorType.user if actor else ActorType.system,
            event_type=event_type,
            object_type=AuditObjectType.workflow_instance,
            object_id=instance.id,
            after=after,
            request_id=_rid(),
        )
    )


def _stage_spec(stage: WorkflowStage) -> dict[str, Any]:
    return stage.assignees or {}


def _due_at(stage: WorkflowStage, default_sla: dict[str, Any] | None) -> datetime.datetime | None:
    """Wall-clock SLA: now + ``hours`` from the stage's sla (or the definition default). No working
    calendar (deferred, R39)."""
    sla = stage.sla or default_sla or {}
    hours = sla.get("hours")
    if not isinstance(hours, (int, float)):
        return None
    return _now() + datetime.timedelta(hours=float(hours))


def _context_user_ids(context: dict[str, Any] | None, ref: str) -> list[uuid.UUID]:
    """Parse user ids named by a stage's ``context_users`` spec key out of the instance context
    (one id or a list). Malformed/missing values resolve to [] — the caller's empty-pool check
    then fails closed to NEEDS_ATTENTION (the engine's standard posture)."""
    raw = (context or {}).get(ref)
    values = raw if isinstance(raw, list) else [raw]
    out: list[uuid.UUID] = []
    for v in values:
        try:
            out.append(uuid.UUID(str(v)))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


async def _resolve_pool(
    session: AsyncSession, instance: WorkflowInstance, stage: WorkflowStage
) -> list[uuid.UUID]:
    """Resolve the candidate pool for a stage.

    Combines two additive sources (S-drift-1 owner-assignment seam):
    - ``roles``: users in any of the stage's named roles (the pre-S-drift-1 path, byte-identical
      for any stage without ``context_users``).
    - ``context_users``: a key naming one user-id or a list of user-ids in the instance context
      (e.g. ``"owner_user_id"`` for the periodic-review stage seeded in 0045).  Duplicates are
      suppressed; the role-pool members come first."""
    spec = _stage_spec(stage)
    pool = await wf_repo.users_with_roles(session, instance.org_id, list(spec.get("roles", [])))
    ref = spec.get("context_users")
    if isinstance(ref, str):
        for uid in _context_user_ids(instance.context, ref):
            if uid not in pool:
                pool.append(uid)
    return pool


async def _materialize_stage(
    session: AsyncSession,
    instance: WorkflowInstance,
    stage: WorkflowStage,
    default_sla: dict[str, Any] | None,
) -> bool:
    """Materialize one task per resolved candidate (PARALLEL). Returns False (FAIL CLOSED) when the
    resolved quorum is unresolvable (missing discriminator) or the pool is empty / smaller than the
    quorum needs — the caller sets NEEDS_ATTENTION.

    A SEQUENTIAL-mode stage materializes identically (all candidates): within-stage one-at-a-time
    ordering is deferred (no v1 consumer; the quorum OUTCOME is the same, and the only
    ordering-visible layer — notifications — is deferred too). Sequential APPROVAL is modeled as
    sequential STAGES, which the engine supports. ROUTER stages never reach here (task-less)."""
    quorum = resolve_conditional(stage.quorum, instance.context)
    if quorum is None:
        return False
    pool = await _resolve_pool(session, instance, stage)
    if not pool or len(pool) < required_approvals(quorum, len(pool)):
        return False
    spec = _stage_spec(stage)
    try:
        task_type = TaskType(spec.get("task_type", "APPROVE"))
    except ValueError:
        task_type = TaskType.APPROVE
    due = _due_at(stage, default_sla)
    for candidate in pool:
        session.add(
            Task(
                org_id=instance.org_id,
                instance_id=instance.id,
                stage_key=stage.key,
                assignee_user_id=candidate,
                candidate_pool=[str(candidate)],
                type=task_type,
                action_expected=spec.get("action_expected"),
                state=TaskState.PENDING,
                due_at=due,
            )
        )
    await session.flush()
    return True


def _route(stage: WorkflowStage, context: dict[str, Any] | None) -> str | None:
    """A ROUTER stage's next target: walk transitions ({when,to} then {default}); fail closed (None)
    when the discriminator key is absent or no branch matches without a default."""
    # transitions is JSONB (ORM-typed dict|None but holds a list of edge dicts) — read as Any.
    raw: Any = stage.transitions
    transitions = raw if isinstance(raw, list) else []
    default: str | None = None
    refs: set[str] = set()
    for t in transitions:
        if not isinstance(t, dict):
            continue
        if "default" in t:
            default = t["default"] if isinstance(t["default"], str) else None
            continue
        when, to = t.get("when"), t.get("to")
        if not isinstance(when, str) or not isinstance(to, str):
            continue
        refs |= referenced_keys(when)
        if evaluate_condition(when, context):
            return to
    # Fail closed if ANY referenced discriminator key is absent (mirrors resolve_conditional).
    if not (refs <= set(context or {})):
        return None
    return default


def _transition_target(stage: WorkflowStage, on_values: set[str]) -> str | None:
    raw: Any = stage.transitions
    for t in raw if isinstance(raw, list) else []:
        if isinstance(t, dict) and t.get("on") in on_values:
            to = t.get("to")
            if isinstance(to, str):
                return to
    return None


async def _enter_stage(
    session: AsyncSession,
    instance: WorkflowInstance,
    stages: dict[str, WorkflowStage],
    stage_key: str,
    default_sla: dict[str, Any] | None,
    visited: set[str],
) -> str:
    """Enter ``stage_key`` and return the resulting instance state: the stage key (tasks
    materialized),
    a terminal sentinel, or NEEDS_ATTENTION (fail-closed). ROUTER stages are traversed (task-less)
    until a materializing stage or terminal; a revisited stage (cycle) fails closed."""
    if stage_key not in stages:
        return stage_key  # a terminal sentinel (COMPLETED / REJECTED / a custom end state)
    if stage_key in visited:
        return NEEDS_ATTENTION  # cycle — fail fast, release the lock (doc 10 §2.1 no recursion)
    visited.add(stage_key)
    stage = stages[stage_key]
    if stage.mode.value == "ROUTER":
        nxt = _route(stage, instance.context)
        if nxt is None:
            return NEEDS_ATTENTION
        return await _enter_stage(session, instance, stages, nxt, default_sla, visited)
    ok = await _materialize_stage(session, instance, stage, default_sla)
    return stage_key if ok else NEEDS_ATTENTION


def _entry_key(definition_stages: Any, stages: dict[str, WorkflowStage]) -> str | None:
    if isinstance(definition_stages, dict):
        entry = definition_stages.get("entry")
        if isinstance(entry, str):
            return entry
    return sorted(stages)[0] if stages else None


async def instantiate(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    definition_key: str,
    subject_type: WorkflowSubjectType,
    subject_id: uuid.UUID,
    context: dict[str, Any] | None,
    actor: AppUser | None,
) -> WorkflowInstance:
    """Open a multi-stage ``workflow_instance`` for ``subject_id`` from the effective definition and
    materialize the entry stage's tasks. Adds rows WITHOUT committing (the caller commits). A
    fail-closed entry (malformed stage, under-quorum pool, missing discriminator) →
    ``current_state="NEEDS_ATTENTION"``."""
    definition = await wf_repo.effective_definition(session, org_id, definition_key, subject_type)
    if definition is None:
        raise ProblemException(
            status=500,
            code="internal_error",
            title=f"No effective {definition_key} workflow definition",
        )
    stages = await wf_repo.all_stages(session, definition.id)
    entry = _entry_key(definition.stages, stages)
    if entry is None:
        raise ProblemException(
            status=500, code="internal_error", title="Workflow definition has no stages"
        )
    instance = WorkflowInstance(
        org_id=org_id,
        definition_id=definition.id,
        definition_version=definition.version,
        subject_type=subject_type,
        subject_id=subject_id,
        current_state=entry,
        context=context,
        revision=0,
    )
    session.add(instance)
    await session.flush()
    # Validate stage signature specs up front (a malformed definition fails closed, never threads a
    # garbage spec downstream). Only ``meaning`` is checked here; method/signed-object validation
    # happens in S-capa-2 where the signature_event row is actually written.
    if any(not _valid_signature_spec(s.signature) for s in stages.values()):
        instance.current_state = NEEDS_ATTENTION
    else:
        instance.current_state = await _enter_stage(
            session, instance, stages, entry, definition.default_sla, set()
        )
    _emit(
        session,
        instance,
        actor,
        EventType.STAGE_ADVANCED,
        after={"event": "instantiated", "current_state": instance.current_state},
    )
    return instance


def _response(
    task: Task,
    outcome: TaskOutcome,
    instance: WorkflowInstance,
    *,
    stage_state: str,
    signature_spec: dict[str, Any] | None,
    replayed: bool = False,
) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "instance_id": str(task.instance_id),
        "stage_key": task.stage_key,
        "outcome": outcome.outcome.value,
        "decided_at": outcome.decided_at.isoformat() if outcome.decided_at else None,
        "decided_by": str(outcome.decided_by),
        "stage_state": stage_state,
        "current_state": instance.current_state,
        "signature_spec": signature_spec,
        "comment": outcome.comment,
        "replayed": replayed,
    }


async def decide(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
    _commit: bool = True,
) -> dict[str, Any]:
    """The generic multi-stage decision (one atomic transaction). Authorization/SoD are the caller's
    job (the S-capa-2 endpoint); the distinct-approver guard here is the service-level backstop.

    ``_commit=False`` (the ``build_capa`` / ``capture_record`` precedent) hands the open transaction
    back to the caller so a CAPA-subject wrapper can append the signed ``capa_stage`` + write the
    ``signature_event`` atomically with this decision (S-capa-2). The ``_response`` is built from
    in-memory state, so it is valid before the caller's commit. The replay early-returns add no rows
    and never committed, so they are unaffected by the flag."""
    instance = await wf_repo.lock_instance_for_update(session, task.instance_id)
    if instance is None:
        raise ProblemException(status=404, code="not_found", title="Workflow instance not found")
    # populate_existing: the route's get_task pre-loaded this row into the identity map — without
    # it this locked SELECT returns the PRE-LOCK snapshot (the S-drift-1 trap).
    locked = (
        await session.execute(
            select(Task)
            .where(Task.id == task.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    # --- replay / decidability guard --------------------------------------------------------
    if locked.state is TaskState.DONE:
        existing = await wf_repo.get_outcome(session, locked.id)
        if (
            existing is not None
            and idempotency_key is not None
            and locked.client_token == idempotency_key
        ):
            return _response(
                locked, existing, instance, stage_state="REPLAY", signature_spec=None, replayed=True
            )
        raise ProblemException(status=409, code="conflict", title="Task already decided")
    if locked.state is TaskState.SKIPPED:
        if locked.client_token == _QUORUM_SKIP:
            existing = await wf_repo.get_outcome(session, locked.id)
            # The stage already closed via quorum — a benign retry is a no-op, not a 409.
            return {
                "task_id": str(locked.id),
                "instance_id": str(locked.instance_id),
                "stage_key": locked.stage_key,
                "outcome": None,
                "stage_state": "ALREADY_SATISFIED",
                "current_state": instance.current_state,
                "signature_spec": None,
                "replayed": True,
            }
        raise ProblemException(status=409, code="conflict", title="Task not decidable")
    if locked.state is not TaskState.PENDING:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Task not decidable",
            detail=f"task state is {locked.state.value}",
        )

    try:
        kind = TaskOutcomeKind(outcome)
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title=f"Unsupported outcome: {outcome}"
        ) from exc

    # --- distinct-approver guard (one actor cannot satisfy a multi-task quorum alone) --------
    if kind in _POSITIVE:
        prior = await wf_repo.stage_outcomes(session, instance.id, locked.stage_key)
        if any(o.outcome in _POSITIVE and o.decided_by == actor.id for o in prior):
            raise ProblemException(
                status=409,
                code="conflict",
                title="Already decided this stage",
                detail="an actor may not approve more than one task in the same quorum stage",
            )

    decision = TaskOutcome(
        task_id=locked.id,
        outcome=kind,
        comment=comment,
        decided_at=_now(),
        decided_by=actor.id,
    )
    session.add(decision)
    locked.state = TaskState.DONE
    locked.assignee_user_id = actor.id
    locked.client_token = idempotency_key
    await session.flush()
    _emit(
        session,
        instance,
        actor,
        EventType.TASK_DECIDED,
        after={"stage_key": locked.stage_key, "outcome": kind.value},
    )

    # --- quorum over the current stage ------------------------------------------------------
    stages = await wf_repo.all_stages(session, instance.definition_id)
    stage = stages.get(locked.stage_key)
    spec = resolve_conditional(stage.quorum, instance.context) if stage else None
    tasks = await wf_repo.stage_tasks(session, instance.id, locked.stage_key)
    outcomes = await wf_repo.stage_outcomes(session, instance.id, locked.stage_key)
    approvers = {o.decided_by for o in outcomes if o.outcome in _POSITIVE}
    rejects = sum(1 for o in outcomes if o.outcome in _NEGATIVE)
    resolved_count = len(tasks)

    signature_spec: dict[str, Any] | None = None
    if spec is None:  # unresolvable conditional at decision time → fail closed
        state = "FAILED"
        instance.current_state = NEEDS_ATTENTION
    else:
        state = quorum_state(spec, len(approvers), rejects, resolved_count)

    if state == "MET" and stage is not None:
        for t in tasks:
            if t.state is TaskState.PENDING:
                t.state = TaskState.SKIPPED
                t.client_token = _QUORUM_SKIP
        signature_spec = stage.signature  # threaded only; no signature_event row this slice
        target = _transition_target(stage, _SUCCESS_ON) or COMPLETED
        instance.current_state = await _enter_stage(session, instance, stages, target, None, set())
        _emit(
            session,
            instance,
            actor,
            EventType.STAGE_ADVANCED,
            after={"from": locked.stage_key, "current_state": instance.current_state},
        )
    elif state == "FAILED" and stage is not None:
        for t in tasks:
            if t.state is TaskState.PENDING:
                t.state = TaskState.SKIPPED
                t.client_token = _QUORUM_SKIP
        instance.current_state = _transition_target(stage, _REJECT_ON) or REJECTED
        _emit(
            session,
            instance,
            actor,
            EventType.STAGE_FAILED,
            after={"from": locked.stage_key, "current_state": instance.current_state},
        )
    elif state == "FAILED":  # stage missing or unresolvable conditional
        _emit(
            session,
            instance,
            actor,
            EventType.STAGE_FAILED,
            after={"from": locked.stage_key, "current_state": instance.current_state},
        )

    if _commit:
        await session.commit()
    return _response(locked, decision, instance, stage_state=state, signature_spec=signature_spec)
