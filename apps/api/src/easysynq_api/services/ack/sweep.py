"""The acknowledgement sweep — the ONE universal obligation mint (slice S-ack-1; doc 04 §8.2/§8.3,
R15/R43, spec §4).

One idempotent pass covers EVERY trigger family: release (the cutover enqueues a doc-scoped run
post-commit), R15 target entry, flag flips, entry adds/removes, imported docs later gaining
distribution — the daily Beat run is the self-heal. CANCEL-BEFORE-MINT: a stale open task (left
audience / superseded-by-MAJOR pin / lapsed flag) is terminated FIRST so the open-task mint guard
never shadows the fresh mint. Cancel = instance termination + skip PENDING tasks (the S-dcr-4
inline force-terminate; NEVER a task-state flip on decide's path — decide() accepts only PENDING).
One session, one commit, under LOCK_ACK_SWEEP (the sweep_reviews posture: acks-late re-delivery
makes concurrent runs real)."""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._ack_enums import AckCreatedReason
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._vault_enums import DocumentKind
from ...db.models._workflow_enums import TaskState, WorkflowSubjectType
from ...db.models.audit_event import AuditEvent
from ...db.models.documented_information import DocumentedInformation
from ...db.models.workflow import Task, WorkflowInstance
from ...domain.ack.rules import plan_obligations
from ..common.org_clock import resolve_org_tz, using_org_tz
from ..common.pg_locks import LOCK_ACK_SWEEP, pg_advisory_lock
from ..workflow import engine as wf_engine
from ..workflow import repository as wf_repo
from . import queries

logger = logging.getLogger("easysynq.ack")

CANCELLED = "CANCELLED"  # the sweep's terminal sentinel for a lapsed obligation
_TERMINAL_INSTANCE_STATES = (
    wf_engine.COMPLETED,
    wf_engine.REJECTED,
    wf_engine.NEEDS_ATTENTION,
    CANCELLED,
)
_DEF_KEY = "doc_acknowledgement"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit_cancelled(
    session: AsyncSession,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    instance_id: uuid.UUID,
    identifier: str | None,
    why: str,
) -> None:
    """The obligation's disappearance must leave a trace (spec §7): the engine audits mint
    (STAGE_ADVANCED at instantiate) but a sweep-cancel bypasses the engine. STAGE_FAILED is the
    engine's flow-terminated-without-completing event (early-fail/reject AND the fail-closed
    unresolvable-stage path), payload-discriminated like instantiate's ``{"event": …}``; keyed
    object_type=document + scope_ref=identifier (the REVIEW_OVERDUE shape) so
    GET /documents/{id}/audit-events surfaces it. A Beat sweep has no request → request_id None."""
    session.add(
        AuditEvent(
            org_id=org_id,
            occurred_at=_now(),
            actor_id=None,
            actor_type=ActorType.system,
            event_type=EventType.STAGE_FAILED,
            object_type=AuditObjectType.document,
            object_id=document_id,
            scope_ref=identifier,
            after={
                "event": "ack_obligation_cancelled",
                "instance_id": str(instance_id),
                "why": why,
            },
            request_id=None,
        )
    )


async def _cancel_instance(session: AsyncSession, instance_id: uuid.UUID) -> bool:
    """Force-terminate one obligation instance (the S-dcr-4 inline precedent): PENDING tasks →
    SKIPPED under FOR UPDATE, instance → CANCELLED. Returns False if already terminal.

    The locked load carries ``populate_existing`` — the sweep's own ``open_ack_tasks`` read has
    already identity-mapped these instances, and without it the lock returns the PRE-LOCK
    attribute snapshot (the S-drift-1 trap): a decide that committed COMPLETED while we waited
    would be invisible and this helper would clobber it to CANCELLED."""
    instance = (
        await session.execute(
            select(WorkflowInstance)
            .where(WorkflowInstance.id == instance_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if instance is None or instance.current_state in _TERMINAL_INSTANCE_STATES:
        return False
    pending = (
        (
            await session.execute(
                select(Task)
                .where(Task.instance_id == instance.id, Task.state == TaskState.PENDING)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for sibling in pending:
        sibling.state = TaskState.SKIPPED
    instance.current_state = CANCELLED
    return True


async def sweep_acks(
    session: AsyncSession,
    *,
    document_id: uuid.UUID | None = None,
    trigger: str | None = None,
) -> dict[str, int]:
    """Reconcile obligations for every ack-eligible Effective document (or ONE doc when scoped).

    Pass A (eligible docs): cancel lapsed open tasks, then mint one instance+task per unsatisfied
    audience member with no surviving open task, pinned to the current Effective version, due_at =
    now + ACK_DUE_DAYS. Pass B: cancel ALL open DOC_ACK tasks on docs that are no longer eligible
    (flag off / not Effective). Idempotent: re-runs no-op."""
    async with pg_advisory_lock(session, LOCK_ACK_SWEEP) as held:
        if not held:
            logger.info("ack_sweep: another sweep holds the lock; skipping this tick")
            return {"tasks_created": 0, "tasks_cancelled": 0, "skipped_lock_held": 1}

        created = cancelled = 0
        doc_filter = [DocumentedInformation.id == document_id] if document_id else []

        eligible = (
            (
                await session.execute(
                    select(DocumentedInformation).where(
                        DocumentedInformation.kind == DocumentKind.DOCUMENT,
                        # "In force" = a governing Effective version exists (the pointer is
                        # cleared on obsolete). current_state is deliberately NOT used: an
                        # UnderRevision/InReview/Approved doc still governs (R1/T7 — the prior
                        # Effective keeps governing), and keying on state would mass-cancel
                        # obligations the moment a revision opens.
                        DocumentedInformation.current_effective_version_id.is_not(None),
                        DocumentedInformation.acknowledgement_required.is_(True),
                        *doc_filter,
                    )
                )
            )
            .scalars()
            .all()
        )
        # Resolve the definition PER ORG (cached for the run; audit U4 — the old first-org-only
        # guard let one mis-seeded org stall or abort the whole cohort). A broken org degrades to
        # a logged no-op for THAT org only — INCLUDING its Pass B rows: with the definition
        # missing we cannot distinguish "lapsed" from "config-broken", and cancelling on broken
        # config would be fail-open (the blast radius is every open obligation). Fail closed:
        # touch nothing of that org this tick; every other org proceeds.
        def_ok: dict[uuid.UUID, bool] = {}

        async def _org_seeded(org_id: uuid.UUID) -> bool:
            if org_id not in def_ok:
                ok = (
                    await wf_repo.effective_definition(
                        session, org_id, _DEF_KEY, WorkflowSubjectType.DOC_ACK
                    )
                    is not None
                )
                if not ok:
                    logger.error(
                        "ack_sweep: no effective doc_acknowledgement definition for org %s — "
                        "seed missing; skipping its documents this tick",
                        org_id,
                    )
                def_ok[org_id] = ok
            return def_ok[org_id]

        eligible_ids = {d.id for d in eligible}
        due_at = _now() + datetime.timedelta(days=get_settings().ack_due_days)
        # R43/doc 17: 'release' marks version-triggered re-arms; everything else (entry adds,
        # flag flips, the daily catch-up) is a target_entry obligation.
        reason = (
            AckCreatedReason.release
            if trigger in ("release", "release_due")
            else AckCreatedReason.target_entry
        )

        for doc in eligible:
            if not await _org_seeded(doc.org_id):
                continue
            boundary = await queries.boundary_seq(session, doc)
            if boundary is None:
                continue  # Effective state without a version row — FK-guaranteed unreachable
            entries = await queries.list_entries(session, doc.id)
            audience = await queries.resolve_audience(session, doc.org_id, entries)
            satisfied = await queries.satisfied_users(session, doc.id, boundary)
            open_pairs = await queries.open_ack_tasks(session, doc.id)
            seqs = await queries.version_seqs(
                session,
                {str((i.context or {}).get("document_version_id", "")) for _, i in open_pairs},
            )
            open_map = queries.pinned_seq_map(open_pairs, seqs)
            to_mint, to_cancel = plan_obligations(
                audience=audience, satisfied=satisfied, open_tasks=open_map, last_major=boundary
            )
            instance_by_user: dict[uuid.UUID, uuid.UUID] = {
                t.assignee_user_id: i.id for t, i in open_pairs if t.assignee_user_id
            }
            # Per-doc SAVEPOINT: cancel-before-mint is a PAIRED reconciliation, and instantiate
            # can fail mid-pair on a present-but-malformed definition — some malformed-spec
            # shapes even raise AFTER the engine added the instance row. If the mint half dies,
            # the cancel half (and any partial engine rows) must not survive to the outer
            # commit: cancel-without-remint on broken config is fail-open (the Pass B
            # rationale). A stale-pinned user sits in BOTH to_cancel and to_mint, so their
            # obligation would otherwise be cancelled "lapsed" with the owed remint never
            # minted.
            doc_created = doc_cancelled = 0
            savepoint = await session.begin_nested()
            try:
                for user_id in to_cancel:
                    iid = instance_by_user.get(user_id)
                    if iid is not None and await _cancel_instance(session, iid):
                        _emit_cancelled(session, doc.org_id, doc.id, iid, doc.identifier, "lapsed")
                        doc_cancelled += 1
                # R55 (S-duedate-snap): snap the (loop-invariant) raw due_at forward to a
                # working day in THIS doc's org calendar — a NEW local (never reassign
                # `due_at`, which spans all orgs).
                from ..notifications.duedate import snap_due_at

                snapped_due_at = await snap_due_at(session, doc.org_id, due_at)
                org_tz = await resolve_org_tz(session, doc.org_id)
                for user_id in to_mint:
                    instance = await wf_engine.instantiate(
                        session,
                        org_id=doc.org_id,
                        definition_key=_DEF_KEY,
                        subject_type=WorkflowSubjectType.DOC_ACK,
                        subject_id=doc.id,
                        context={
                            "user_id": str(user_id),
                            "document_id": str(doc.id),
                            "document_version_id": str(doc.current_effective_version_id),
                            "created_reason": reason.value,
                            "identifier": doc.identifier,
                        },
                        actor=None,
                    )
                    await session.flush()
                    await session.execute(
                        update(Task)
                        .where(Task.instance_id == instance.id)
                        .values(due_at=snapped_due_at)
                    )
                    ack_tasks = (
                        (await session.execute(select(Task).where(Task.instance_id == instance.id)))
                        .scalars()
                        .all()
                    )
                    from ..notifications.dispatch import enqueue_task_notifications

                    with using_org_tz(org_tz):
                        await enqueue_task_notifications(
                            session, instance, list(ack_tasks), due_at_override=snapped_due_at
                        )
                    doc_created += 1
            except Exception:
                # EVERY malformed-definition shape lands here: the pre-add ProblemException
                # raises (missing/entry-less definition) AND the post-add
                # AttributeError/TypeError/ValueError shapes a malformed stage spec (scalar
                # assignees/roles/sla, non-numeric quorum) throws from _enter_stage. Roll the
                # doc's half-done pair back, mark the org broken for the rest of this run (its
                # later docs and Pass B rows skip), keep the cohort alive.
                await savepoint.rollback()
                logger.exception(
                    "ack_sweep: reconcile failed for org %s — malformed doc_acknowledgement "
                    "definition; skipping the org this tick",
                    doc.org_id,
                )
                def_ok[doc.org_id] = False
                continue
            await savepoint.commit()
            created += doc_created
            cancelled += doc_cancelled

        # Pass B: open DOC_ACK obligations on docs that are no longer eligible.
        stale_q = (
            select(
                Task.instance_id,
                WorkflowInstance.subject_id,
                WorkflowInstance.org_id,
                WorkflowInstance.context,
            )
            .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
            .where(
                WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                Task.state == TaskState.PENDING,
            )
        )
        if document_id is not None:
            stale_q = stale_q.where(WorkflowInstance.subject_id == document_id)
        for instance_id, subject_id, org_id, context in (await session.execute(stale_q)).all():
            # The same per-org fail-closed posture: a config-broken org's obligations are left
            # untouched (cancel-without-remint on broken config would be fail-open).
            if not await _org_seeded(org_id):
                continue
            if subject_id not in eligible_ids and await _cancel_instance(session, instance_id):
                _emit_cancelled(
                    session,
                    org_id,
                    subject_id,
                    instance_id,
                    (context or {}).get("identifier"),
                    "ineligible",
                )
                cancelled += 1

        await session.commit()
        return {"tasks_created": created, "tasks_cancelled": cancelled, "skipped_lock_held": 0}
