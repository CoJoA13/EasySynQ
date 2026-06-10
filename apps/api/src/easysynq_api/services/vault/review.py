"""Periodic re-review (D5 — doc 04 §9, doc 05 §9.1, spec S-drift-1).

The ONE recompute rule + the ``review_state`` read-time projection live here and nowhere else.
``next_review_due`` is STORED on ``documented_information`` (a confirm resets it from the review
date); ``review_state`` is NEVER stored (always derived — the owner's fork). Periods are integer
MONTHS (psycopg3 cannot load month-bearing PG intervals into timedelta)."""

from __future__ import annotations

import calendar
import datetime
import logging
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._vault_enums import DocumentCurrentState, DocumentKind
from ...db.models._workflow_enums import TaskState, WorkflowSubjectType
from ...db.models.audit_event import AuditEvent
from ...db.models.documented_information import DocumentedInformation
from ...db.models.workflow import Task, WorkflowInstance
from ...logging import request_id_var
from ..common.pg_locks import LOCK_REVIEW_SWEEP, pg_advisory_lock
from ..workflow import engine as wf_engine
from ..workflow import repository as wf_repo

REVIEW_PERIOD_DEFAULT_MONTHS = 24  # doc 04's "e.g. 12/24/36 months" middle value (owner fork)
REVIEW_LEAD_DAYS = 30  # doc 04 §9.1's lead window ("e.g. 30 days"); org-config later, additive


def add_months(day: datetime.date, months: int) -> datetime.date:
    """Calendar month-add, day clamped to the target month's length (Jan 31 + 1mo → Feb 28/29)."""
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _org_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().easysynq_org_timezone)


def today_org() -> datetime.date:
    """Today as a DATE in the org timezone (R8: dates display in org tz; UTC authoritative)."""
    return datetime.datetime.now(_org_tz()).date()


def compute_next_review_due(
    review_period_months: int | None,
    last_reviewed_at: datetime.datetime | None,
    effective_from: datetime.datetime | None,
) -> datetime.date | None:
    """anchor = the LATER of (last_reviewed_at, effective_from); + period months, org-tz dated.

    One rule, three triggers (release / review-confirm / PATCH): a re-release after a confirm
    anchors on the newer effective_from, a confirm after a release anchors on the newer review
    date. NULL period or no anchor → None (not scheduled)."""
    if review_period_months is None:
        return None
    anchors = [a for a in (last_reviewed_at, effective_from) if a is not None]
    if not anchors:
        return None
    return add_months(max(anchors).astimezone(_org_tz()).date(), review_period_months)


def review_state(next_review_due: datetime.date | None, today: datetime.date) -> str | None:
    """The derived currency projection: current | due_soon | overdue (None = not scheduled)."""
    if next_review_due is None:
        return None
    if today >= next_review_due:
        return "overdue"
    if today >= next_review_due - datetime.timedelta(days=REVIEW_LEAD_DAYS):
        return "due_soon"
    return "current"


logger = logging.getLogger("easysynq.documents.review")

_TERMINAL_INSTANCE_STATES = (wf_engine.COMPLETED, wf_engine.REJECTED, wf_engine.NEEDS_ATTENTION)
_DEF_KEY = "periodic_review"


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


async def sweep_reviews(session: AsyncSession) -> dict[str, int]:
    """The daily D5 sweep (doc 04 §9.1). Pass 1: open ONE periodic_review instance+task per
    Effective doc inside the lead window (idempotent — the open-instance check; NEEDS_ATTENTION
    counts as terminal so a failed-closed instance may be retried, the CAPA precedent — accepted:
    a persistent fail-closed mode would grow one NEEDS_ATTENTION row per run, unreachable today
    since owner_user_id is NOT NULL). Pass 2: once-per-cycle REVIEW_OVERDUE audit for past-due
    open tasks — NEVER flips task state (decide() accepts only PENDING; engine.py:390).
    Single-flight via the session-scoped advisory lock (no schema constraint stops two open
    instances per subject; acks-late re-delivery past the Redis visibility timeout makes
    concurrent runs real — the mirror.sync posture). One commit; accepted benign races: a stray
    REVIEW_OVERDUE for a just-decided task, and a task created from a snapshot the owner
    confirmed seconds earlier — both self-heal next cycle."""
    async with pg_advisory_lock(session, LOCK_REVIEW_SWEEP) as held:
        if not held:
            logger.info("review_sweep: another sweep holds the lock; skipping this tick")
            return {"tasks_created": 0, "escalated": 0, "skipped_lock_held": 1}

        today = today_org()
        horizon = today + datetime.timedelta(days=REVIEW_LEAD_DAYS)
        created = escalated = 0

        docs = (
            (
                await session.execute(
                    select(DocumentedInformation).where(
                        DocumentedInformation.kind == DocumentKind.DOCUMENT,
                        DocumentedInformation.current_state == DocumentCurrentState.Effective,
                        DocumentedInformation.next_review_due.is_not(None),
                        DocumentedInformation.next_review_due <= horizon,
                    )
                )
            )
            .scalars()
            .all()
        )
        # Resolve the definition ONCE; a mis-seeded org must degrade to a logged no-op, not a
        # 500-shaped Beat failure every day that also kills the escalation pass.
        if docs and (
            await wf_repo.effective_definition(
                session, docs[0].org_id, _DEF_KEY, WorkflowSubjectType.PERIODIC_REVIEW
            )
            is None
        ):
            logger.error("review_sweep: no effective periodic_review definition — seed missing")
            docs = []
        for doc in docs:
            if (
                await wf_repo.find_nonterminal_instance(
                    session,
                    doc.org_id,
                    WorkflowSubjectType.PERIODIC_REVIEW,
                    doc.id,
                    _TERMINAL_INSTANCE_STATES,
                )
                is not None
            ):
                continue
            instance = await wf_engine.instantiate(
                session,
                org_id=doc.org_id,
                definition_key=_DEF_KEY,
                subject_type=WorkflowSubjectType.PERIODIC_REVIEW,
                subject_id=doc.id,
                context={"owner_user_id": str(doc.owner_user_id), "identifier": doc.identifier},
                actor=None,
            )
            await session.flush()
            # Org-local midnight (NOT UTC): review_state flips overdue at org-tz midnight, so due_at
            # must anchor on the same instant or the two signals disagree by the UTC offset.
            # next_review_due is filtered is_not(None) above; guard for mypy.
            if doc.next_review_due is None:
                continue  # unreachable; the WHERE clause guarantees it
            due_at = datetime.datetime.combine(
                doc.next_review_due, datetime.time(0, 0), tzinfo=_org_tz()
            )
            await session.execute(
                update(Task).where(Task.instance_id == instance.id).values(due_at=due_at)
            )
            created += 1

        overdue_rows = (
            await session.execute(
                select(Task, WorkflowInstance)
                .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                    Task.state == TaskState.PENDING,
                    Task.due_at.is_not(None),
                    Task.due_at < _now(),
                )
            )
        ).all()
        for task, instance in overdue_rows:
            # Dedup anchored on the INSTANCE id stamped into `after` (one escalation per review
            # CYCLE — a fresh cycle's new instance id re-arms it), NOT on occurred_at vs
            # instance.started_at (Python clock vs PG clock skew could double-write).
            already = (
                await session.execute(
                    select(AuditEvent.id)
                    .where(
                        AuditEvent.object_type == AuditObjectType.document,
                        AuditEvent.object_id == instance.subject_id,
                        AuditEvent.event_type == EventType.REVIEW_OVERDUE,
                        AuditEvent.after["instance_id"].astext == str(instance.id),
                    )
                    .limit(1)
                )
            ).first()
            if already is not None:
                continue
            doc_row = await session.get(DocumentedInformation, instance.subject_id)
            session.add(
                AuditEvent(
                    org_id=instance.org_id,
                    occurred_at=_now(),
                    actor_id=None,
                    actor_type=ActorType.system,
                    event_type=EventType.REVIEW_OVERDUE,
                    object_type=AuditObjectType.document,
                    object_id=instance.subject_id,
                    scope_ref=doc_row.identifier if doc_row is not None else None,
                    after={
                        "instance_id": str(instance.id),
                        "due_at": task.due_at.isoformat() if task.due_at else None,
                    },
                    request_id=_rid(),
                )
            )
            escalated += 1

        await session.commit()
        return {"tasks_created": created, "escalated": escalated}
