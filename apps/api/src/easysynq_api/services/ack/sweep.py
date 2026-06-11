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
from ...db.models._vault_enums import DocumentCurrentState, DocumentKind
from ...db.models._workflow_enums import TaskState, WorkflowSubjectType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.workflow import Task, WorkflowInstance
from ...domain.ack.rules import plan_obligations
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


async def _cancel_instance(session: AsyncSession, instance_id: uuid.UUID) -> bool:
    """Force-terminate one obligation instance (the S-dcr-4 inline precedent): PENDING tasks →
    SKIPPED under FOR UPDATE, instance → CANCELLED. Returns False if already terminal."""
    instance = await wf_repo.lock_instance_for_update(session, instance_id)
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
    session: AsyncSession, *, document_id: uuid.UUID | None = None
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
                        DocumentedInformation.current_state == DocumentCurrentState.Effective,
                        DocumentedInformation.acknowledgement_required.is_(True),
                        *doc_filter,
                    )
                )
            )
            .scalars()
            .all()
        )
        # Resolve the definition ONCE; a mis-seeded org degrades to a logged no-op (the
        # sweep_reviews posture), never a 500-shaped Beat failure.
        if eligible and (
            await wf_repo.effective_definition(
                session, eligible[0].org_id, _DEF_KEY, WorkflowSubjectType.DOC_ACK
            )
            is None
        ):
            logger.error("ack_sweep: no effective doc_acknowledgement definition — seed missing")
            eligible = []

        eligible_ids = {d.id for d in eligible}
        due_at = _now() + datetime.timedelta(days=get_settings().ack_due_days)
        reason = (
            AckCreatedReason.release if document_id is not None else AckCreatedReason.target_entry
        )

        for doc in eligible:
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
            for user_id in to_cancel:
                iid = instance_by_user.get(user_id)
                if iid is not None and await _cancel_instance(session, iid):
                    cancelled += 1
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
                    update(Task).where(Task.instance_id == instance.id).values(due_at=due_at)
                )
                created += 1

        # Pass B: open DOC_ACK obligations on docs that are no longer eligible.
        stale_q = (
            select(Task.instance_id, WorkflowInstance.subject_id)
            .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
            .where(
                WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                Task.state == TaskState.PENDING,
            )
        )
        if document_id is not None:
            stale_q = stale_q.where(WorkflowInstance.subject_id == document_id)
        for instance_id, subject_id in (await session.execute(stale_q)).all():
            if subject_id not in eligible_ids and await _cancel_instance(session, instance_id):
                cancelled += 1

        await session.commit()
        return {"tasks_created": created, "tasks_cancelled": cancelled, "skipped_lock_held": 0}
