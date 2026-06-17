"""Engine-routed, signed Top-Management authorization of an Improvement Initiative (slice
S-improvement-4; doc 02 Cl 10.3, doc 10 §2, decisions-register R46/R2).

The OPT-IN, additive alternative to the unsigned ``Completed→Closed`` /transition close. A Completed
initiative's owner/manager *requests* a management authorization; it routes through the generic
multi-stage workflow engine to the reserved **"Top Management"** role. When a Top-Management member
signs (``meaning='verify'`` — leadership verifies the realized benefit), the sign-off CLOSES the
initiative, binding the ``signature_event`` to the new ``Closed`` stage event via the
pre-generated-UUID seam (two mutually-referencing INSERTs, never an UPDATE — the table is
REVOKE-immutable). The CAPA ``decide_capa_action_plan`` template, ported to an own-table subject.

Authority is the role-resolved candidate pool (no permission key gates the SIGN — the self-scoped-
task doctrine, doc 07). The engine fails CLOSED to ``NEEDS_ATTENTION`` when no Top-Management member
is assigned, so the request never silently completes. The existing unsigned close is untouched (R46:
clause 10.3 mandates no per-initiative sign-off; this is opt-in).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._improvement_enums import ImprovementStage
from ...db.models._workflow_enums import WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.improvement_initiative import ImprovementInitiative
from ...db.models.improvement_initiative_stage_event import ImprovementInitiativeStageEvent
from ...db.models.workflow import Task, WorkflowInstance
from ...domain.improvement import transition_allowed
from ...logging import request_id_var
from ...problems import ProblemException
from ..vault.signature import SignatureEvent, SignatureEventSink
from ..workflow import engine
from ..workflow import repository as wf_repo
from . import repository as repo

# The seeded (mig 0053) effective definition: a single Top-Management ANY stage that signs
# ``meaning=verify`` and advances to COMPLETED.
_AUTH_DEF_KEY = "improvement_initiative_authorization"
# The engine's terminal/sentinel instance states. Anything else means the flow is still running, so
# the request guard treats only these as "no active authorization" (NEEDS_ATTENTION is an abandoned
# fail-closed instance → a re-request is allowed once a Top-Management approver is assigned).
_TERMINAL_INSTANCE_STATES = (engine.COMPLETED, engine.REJECTED, engine.NEEDS_ATTENTION)


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


def _content_digest(content_block: dict[str, Any]) -> str:
    """A deterministic ``sha256:`` digest of the sealed authorization block — binds the ``verify``
    signature to the exact bytes it signed (the ``_content_digest`` in capa/service.py)."""
    payload = json.dumps(content_block, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _not_found(what: str) -> ProblemException:
    return ProblemException(status=404, code="not_found", title=f"{what} not found")


def _conflict(code: str, title: str) -> ProblemException:
    return ProblemException(status=409, code=code, title=title)


def _emit_authorized(
    session: AsyncSession,
    actor: AppUser,
    initiative: ImprovementInitiative,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    """The first-class audit of the leadership sign-off (object_type=improvement_initiative,
    scope_ref=identifier) — distinct from the unsigned INITIATIVE_TRANSITIONED close (the ``_emit``
    in service.py pattern)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.INITIATIVE_AUTHORIZED,
            object_type=AuditObjectType.improvement_initiative,
            object_id=initiative.id,
            scope_ref=initiative.identifier,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


async def request_authorization(
    session: AsyncSession,
    actor: AppUser,
    initiative_id: uuid.UUID,
    *,
    comment: str | None = None,
) -> WorkflowInstance:
    """Open a Top-Management authorization workflow for a Completed initiative (POST
    /improvement-initiatives/{id}/request-authorization). FOR UPDATE + populate_existing → 409
    unless ``Completed`` → 409 if an authorization is already in flight → instantiate the engine
    definition (materializing the Top-Management task, or NEEDS_ATTENTION when the pool is empty),
    commit. The initiative stays ``Completed`` until a Top-Management member signs."""
    initiative = await repo.get_initiative(session, initiative_id, for_update=True)
    if initiative is None or initiative.org_id != actor.org_id:
        raise _not_found("Improvement initiative")
    if initiative.stage is not ImprovementStage.Completed:
        raise _conflict(
            "initiative_not_authorizable",
            "Management authorization can only be requested for a Completed initiative",
        )
    existing = await wf_repo.find_nonterminal_instance(
        session,
        actor.org_id,
        WorkflowSubjectType.IMPROVEMENT_INITIATIVE,
        initiative.id,
        _TERMINAL_INSTANCE_STATES,
    )
    if existing is not None:
        raise _conflict(
            "authorization_in_progress",
            "A management authorization is already in progress for this initiative",
        )
    instance = await engine.instantiate(
        session,
        org_id=actor.org_id,
        definition_key=_AUTH_DEF_KEY,
        subject_type=WorkflowSubjectType.IMPROVEMENT_INITIATIVE,
        subject_id=initiative.id,
        context={
            "initiative_id": str(initiative.id),
            "identifier": initiative.identifier,
            "title": initiative.title,
            "process_id": str(initiative.process_id) if initiative.process_id else None,
            "requested_by": str(actor.id),
            "request_comment": comment,
        },
        actor=actor,
    )
    await session.commit()
    await session.refresh(instance)
    return instance


async def _assert_initiative_authorizer(
    session: AsyncSession, actor: AppUser, task: Task, instance: WorkflowInstance
) -> None:
    """The SOLE authorization gate for an initiative-authorization decision (no catalog key — the
    role-resolved candidate pool IS the authority; the ``_assert_capa_approver`` shape). Both
    collapse legs return 404 uniformly. MUST run under the instance lock the caller holds."""
    pool_frozen = task.candidate_pool or []
    if task.assignee_user_id != actor.id and str(actor.id) not in pool_frozen:
        raise _not_found("Task")
    stages = await wf_repo.all_stages(session, instance.definition_id)
    stage = stages.get(task.stage_key)
    if stage is None:
        raise _not_found("Task")
    roles = list((stage.assignees or {}).get("roles", []))
    pool = await wf_repo.users_with_roles(session, actor.org_id, roles)
    if actor.id not in pool:
        raise _not_found("Task")
    # Single-stage flow → the cross-stage guard is a no-op today, but kept for symmetry with the
    # CAPA precedent (so a future two-tier definition stays sound).
    if await wf_repo.actor_decided_in_instance(
        session, instance.id, actor.id, exclude_task_id=task.id
    ):
        raise ProblemException(
            status=409,
            code="conflict",
            title="Already decided this authorization",
            detail="an approver may not decide more than one stage of one authorization",
        )


async def _enrich_completed_replay(
    session: AsyncSession, result: dict[str, Any], initiative_id: uuid.UUID
) -> None:
    """Re-derive the initiative fields (``initiative_stage`` + ``signature_event_id``) for an
    idempotent replay whose original decision COMPLETED — so a retry's body matches. An initiative
    closes exactly once (terminal, no effectiveness loop), so the single signed ``Closed`` stage
    event is unambiguous (unlike CAPA's per-cycle ActionPlan stages)."""
    initiative = await repo.get_initiative(session, initiative_id)
    if initiative is None:
        return
    result["initiative_stage"] = initiative.stage.value
    events = await repo.list_stage_events(session, initiative_id)
    signed = [e for e in events if e.signed_event_id is not None]
    result["signature_event_id"] = str(signed[-1].signed_event_id) if signed else None


async def decide_initiative_authorization(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
    sig_sink: SignatureEventSink,
) -> dict[str, Any]:
    """Decide a Top-Management authorization task (the ``POST /tasks/{id}/decision`` IMPROVEMENT_
    INITIATIVE dispatch). Runs the generic engine decision WITHOUT committing, then — on the
    COMPLETING ``verify`` sign — writes ``signature_event(meaning=verify,
    signed_object=improvement_initiative_stage_event)``, appends the SIGNED ``Closed`` stage event,
    and flips the initiative to ``Closed`` (+ ``closed_at``), all in ONE transaction. The
    append-only stage-event table never gets an UPDATE: the stage-event id is pre-generated so the
    signature (``signed_object_id`` = that id) and the stage event (``signed_event_id`` = the
    flushed signature id) are two mutually-referencing INSERTs. A reject leaves the initiative at
    ``Completed`` (re-requestable); no signature."""
    # Lock the instance FIRST (the serialization point); engine.decide re-locks re-entrantly.
    instance = await wf_repo.lock_instance_for_update(session, task.instance_id)
    if instance is None or instance.org_id != actor.org_id:
        raise _not_found("Workflow instance")
    await _assert_initiative_authorizer(session, actor, task, instance)

    result = await engine.decide(
        session,
        task,
        actor,
        outcome=outcome,
        comment=comment,
        idempotency_key=idempotency_key,
        _commit=False,
    )
    if result.get("replayed"):
        if result.get("current_state") == engine.COMPLETED:
            await _enrich_completed_replay(session, result, instance.subject_id)
        await session.commit()
        return result

    if result["current_state"] == engine.COMPLETED:
        initiative = await repo.get_initiative(session, instance.subject_id, for_update=True)
        if initiative is None or initiative.org_id != actor.org_id:
            raise _not_found("Improvement initiative")
        if not transition_allowed(initiative.stage, ImprovementStage.Closed):
            raise _conflict(
                "improvement_transition_invalid",
                f"An improvement initiative in {initiative.stage.value} cannot be authorized "
                "to Closed",
            )
        before = initiative.stage
        # Pre-gen so the signature ↔ stage-event cross-reference needs no UPDATE.
        stage_event_id = uuid.uuid4()
        sealed: dict[str, Any] = {
            "initiative_id": str(initiative.id),
            "identifier": initiative.identifier,
            "verified_by": str(actor.id),
            "requested_by": (instance.context or {}).get("requested_by"),
            "outcome": comment,
            "workflow_instance_id": str(instance.id),
        }
        sig = sig_sink.record(
            session,
            SignatureEvent(
                org_id=actor.org_id,
                signed_object_id=stage_event_id,
                meaning="verify",
                signer_user_id=actor.id,
                signed_object_type="improvement_initiative_stage_event",
                content_digest=_content_digest(sealed),
                auth_context={"acr": "SESSION"},
            ),
        )
        await session.flush()  # the sink adds but does NOT flush — populate sig.id for the FK
        session.add(
            ImprovementInitiativeStageEvent(
                id=stage_event_id,
                org_id=actor.org_id,
                initiative_id=initiative.id,
                from_state=before,
                to_state=ImprovementStage.Closed,
                actor_id=actor.id,
                comment=comment,
                payload={"outcome": comment} if comment else None,
                signed_event_id=sig.id if sig is not None else None,
            )
        )
        initiative.stage = ImprovementStage.Closed
        initiative.closed_at = _now()
        _emit_authorized(
            session,
            actor,
            initiative,
            before={"stage": before.value},
            after={
                "stage": ImprovementStage.Closed.value,
                "signed_event_id": str(sig.id) if sig is not None else None,
            },
        )
        result["initiative_stage"] = ImprovementStage.Closed.value
        result["signature_event_id"] = str(sig.id) if sig is not None else None

    await session.commit()
    return result
