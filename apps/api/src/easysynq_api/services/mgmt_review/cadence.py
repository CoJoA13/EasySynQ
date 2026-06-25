"""The cadence Beat sweep (S-mr-1, clause 9.3 §s6) — mints the next *Scheduled* Management Review
when the org's cadence horizon is reached (mirrors the S-drift-1 ``sweep_reviews`` posture).

``next_mr_due`` is the pure cadence rule (``add_months`` from the last released review's
``effective_from``). ``sweep_mgmt_reviews`` is the daily pass:

  1. Acquire ``LOCK_MGMT_REVIEW_SWEEP`` (skip-and-return if another holder has it — acks-late
     re-delivery makes concurrent Beat fires real).
  2. Resolve the single org (D1 single-org).
  3. Read ``system_config`` cadence + owner. A NULL owner degrades to a logged no-op (you cannot
     create an ownerless document — the honest degrade, not a daily 500).
  4. Resolve the ``management_review`` workflow definition once; a mis-seed degrades to a logged
     no-op (the instance FK needs it; never a daily 500).
  5. Org-scoped idempotency: if a non-terminal MR document already exists, no-op (the subject of
     the MR we'd mint is the Draft doc itself — it does not exist yet, so a per-SUBJECT
     ``find_nonterminal_instance`` is impossible; org-scoped ``open_review_exists`` is the guard).
  6. Anchor on the last RELEASED MR's ``effective_from`` → ``next_mr_due``. No prior released MR →
     mint the first one now. Else mint iff ``next_due <= today_org()``.
  7. Mint: ``create_review`` under the configured owner (it commits the base doc internally — the
     sweep is a TWO-commit shape; the ``open_review_exists`` check before create is the idempotency
     guard), then a fresh ``MGMT_REVIEW`` instance + ONE ``MR_INPUT`` task on the prepare stage (the
     Phase-5 ``spawn.py`` direct-insert shape, ``type=MR_INPUT``). Commit.

A sweep MINTS + AUDITS but never ESCALATES (``decide()`` accepts only PENDING) — it only creates."""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.management_review import ManagementReview
from ...db.models.organization import Organization
from ...db.models.system_config import SystemConfig
from ...db.models.workflow import Task, WorkflowInstance
from ..common.pg_locks import LOCK_MGMT_REVIEW_SWEEP, pg_advisory_lock
from ..notifications.duedate import resolve_calendar, snap_to_working_day
from ..vault import get_vault_audit_sink

# Re-use the calendar-clamp + org-tz helpers from the S-drift-1 review module (do NOT re-derive the
# month-add / org-tz logic — the engineering-patterns rule).
from ..vault.review import _org_tz, add_months, today_org
from ..workflow import repository as wf_repo
from . import repository as repo
from . import service

logger = logging.getLogger("easysynq.mgmt_review")

_DEF_KEY = "management_review"
_PREPARE_STAGE_KEY = "prepare"
# All three keys on every exit path (a consumer/assertion must never KeyError on a missing key).
_ZERO_SUMMARY = {"mgmt_reviews_opened": 0, "skipped_open": 0, "skipped_lock_held": 0}


def next_mr_due(
    last_effective_from_date: datetime.date | None, cadence_months: int
) -> datetime.date | None:
    """When the next Management Review is due: the last released review's ``effective_from`` DATE +
    ``cadence_months`` (calendar month-add, the ``add_months`` clamp). ``None`` history → ``None``
    (the sweep reads None as "no anchor — mint the first review now")."""
    if last_effective_from_date is None:
        return None
    return add_months(last_effective_from_date, cadence_months)


MR_REVIEW_LEAD_DAYS = 30  # the MR-specific due_soon window; org-config is a v1.x deferral.
# NOT review.REVIEW_LEAD_DAYS — an annual cadence is independently tuned.


@dataclasses.dataclass(frozen=True)
class CadenceStatus:
    cadence_months: int
    owner_user_id: uuid.UUID | None
    last_review_effective_from: datetime.date | None
    next_review_due: datetime.date | None


async def read_cadence(session: AsyncSession, org_id: uuid.UUID) -> CadenceStatus | None:
    """The one cadence rule shared by the daily sweep AND the GET /management-reviews/next-due read
    (so the widget and the sweep can't desync). None when no system_config row exists for the org
    (seeded at setup → unreachable operationally; the caller degrades, never 500s)."""
    config = (
        await session.execute(select(SystemConfig).where(SystemConfig.org_id == org_id))
    ).scalar_one_or_none()
    if config is None:
        return None
    anchor = await _last_released_effective_from(session, org_id)
    return CadenceStatus(
        cadence_months=config.mgmt_review_cadence_months,
        owner_user_id=config.mgmt_review_owner_user_id,
        last_review_effective_from=anchor,
        next_review_due=next_mr_due(anchor, config.mgmt_review_cadence_months),
    )


def mr_review_state(next_due: datetime.date | None, today: datetime.date) -> str | None:
    """current | due_soon | overdue (None = not scheduled). Mirrors vault.review.review_state with
    an MR-specific lead window (MR_REVIEW_LEAD_DAYS)."""
    if next_due is None:
        return None
    if today >= next_due:
        return "overdue"
    if today >= next_due - datetime.timedelta(days=MR_REVIEW_LEAD_DAYS):
        return "due_soon"
    return "current"


async def _resolve_org_id(session: AsyncSession) -> uuid.UUID | None:
    """The single org (D1). Prefer the seed short_code, else the only organization."""
    org_id = (
        await session.execute(select(Organization.id).where(Organization.short_code == "DEFAULT"))
    ).scalar_one_or_none()
    if org_id is None:
        org_id = (await session.execute(select(Organization.id).limit(2))).scalars().first()
    return org_id


async def _last_released_effective_from(
    session: AsyncSession, org_id: uuid.UUID
) -> datetime.date | None:
    """The ``effective_from`` DATE (org-tz) of the most recently released Management Review — the
    cadence anchor. Reads the governing Effective version of the newest Effective MR document.

    ``None`` when no MR has ever been released (the first-review-mint-now path). The version is
    reached via ``current_effective_version_id`` (set atomically at the release cutover); a NULL
    ``effective_from`` on that version is unreachable (release stamps it), but is guarded for
    mypy."""
    row = (
        await session.execute(
            select(DocumentVersion.effective_from)
            .join(
                DocumentedInformation,
                DocumentedInformation.current_effective_version_id == DocumentVersion.id,
            )
            .join(ManagementReview, ManagementReview.id == DocumentedInformation.id)
            .where(
                ManagementReview.org_id == org_id,
                DocumentedInformation.current_state == DocumentCurrentState.Effective,
            )
            .order_by(DocumentVersion.effective_from.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    # effective_from is a tz-aware datetime; date it in the org timezone (R8: org-tz dates).
    return row.astimezone(_org_tz()).date()


def _period_label(due: datetime.date) -> str:
    """A human period label for the auto-minted review (the year of the due date)."""
    return f"{due.year} Annual"


async def sweep_mgmt_reviews(session: AsyncSession) -> dict[str, int]:
    """The daily cadence sweep. Returns ``{mgmt_reviews_opened, skipped_open, skipped_lock_held}``
    (int-valued counts). Idempotent: the org-scoped ``open_review_exists`` guard makes a re-run a
    no-op while any MR is open, so the two-commit ``create_review`` shape is safe."""
    async with pg_advisory_lock(session, LOCK_MGMT_REVIEW_SWEEP) as held:
        if not held:
            logger.info("mgmt_review_sweep: another sweep holds the lock; skipping this tick")
            return {**_ZERO_SUMMARY, "skipped_lock_held": 1}

        org_id = await _resolve_org_id(session)
        if org_id is None:  # pragma: no cover — an operational install always has its org
            logger.error("mgmt_review_sweep: no organization row — cannot sweep")
            return {**_ZERO_SUMMARY, "skipped_lock_held": 0}

        cad = await read_cadence(session, org_id)
        if cad is None:  # pragma: no cover — system_config is seeded at setup
            logger.error("mgmt_review_sweep: no system_config row — cannot sweep")
            return {**_ZERO_SUMMARY, "skipped_lock_held": 0}

        owner_id = cad.owner_user_id
        if owner_id is None:
            # The honest degrade: you cannot create an ownerless document. Logged, not a daily 500.
            logger.info(
                "mgmt_review_sweep: mgmt_review_owner_user_id is unset — no review minted "
                "(set system_config.mgmt_review_owner_user_id to enable the cadence)"
            )
            return {**_ZERO_SUMMARY, "skipped_lock_held": 0}

        definition = await wf_repo.effective_definition(
            session, org_id, _DEF_KEY, WorkflowSubjectType.MGMT_REVIEW
        )
        if definition is None:
            # A mis-seeded org (no management_review workflow_definition): the MR_INPUT task's
            # instance FK requires it. Degrade to a logged no-op (the 0050 seed guarantees it).
            logger.error(
                "mgmt_review_sweep: no effective management_review workflow definition — "
                "seed missing"
            )
            return {**_ZERO_SUMMARY, "skipped_lock_held": 0}

        # Org-scoped idempotency (NOT subject-scoped): an MR's subject is the Draft doc we'd mint —
        # it does not exist yet, so there is no per-subject instance to find. A non-terminal MR
        # document existing means the cycle is already open → skip.
        if await repo.open_review_exists(session, org_id):
            logger.info("mgmt_review_sweep: a Management Review is already open — skipping")
            return {**_ZERO_SUMMARY, "skipped_open": 1}

        due = cad.next_review_due
        # No prior released MR (anchor None → due None) → mint the first one NOW. Else mint iff the
        # cadence horizon has arrived (<= today, org-tz; a small lead window is optional — v1 keeps
        # it simple).
        today = today_org()
        if due is not None and due > today:
            logger.info(
                "mgmt_review_sweep: next review not yet due (due %s > today %s) — skipping",
                due.isoformat(),
                today.isoformat(),
            )
            return {**_ZERO_SUMMARY, "skipped_open": 0}

        owner = await session.get(AppUser, owner_id)
        if owner is None:  # pragma: no cover — the RESTRICT FK guarantees a live app_user row
            logger.error("mgmt_review_sweep: configured owner %s not found — cannot mint", owner_id)
            return {**_ZERO_SUMMARY, "skipped_lock_held": 0}

        # The MR document_type must be seeded (0050) for create_review; a missing type is a 422 from
        # create_review's _mr_document_type_id — guard it to a logged no-op rather than a daily 500.
        mr_type = (
            await session.execute(
                select(DocumentType).where(DocumentType.org_id == org_id, DocumentType.code == "MR")
            )
        ).scalar_one_or_none()
        if mr_type is None:  # pragma: no cover — the 0050 seed guarantees it
            logger.error("mgmt_review_sweep: MR document_type unseeded — cannot mint")
            return {**_ZERO_SUMMARY, "skipped_lock_held": 0}

        label = _period_label(due or today)
        sink = get_vault_audit_sink()
        # create_review commits the base doc internally (the OBJ/form_template two-step). The
        # org-scoped open_review_exists check above is the idempotency guard for the 2-commit shape.
        mr = await service.create_review(
            session,
            sink,
            owner,
            title=f"Management Review {label}",
            period_label=label,
        )

        # Mint the MR_INPUT task on a fresh MGMT_REVIEW container instance (the Phase-5 spawn.py
        # direct-insert shape; type=MR_INPUT, action_expected="prepare"). NO standalone task is
        # possible — task.instance_id is NOT-NULL with a RESTRICT FK — so the instance is added +
        # flushed FIRST, then the task.
        instance = WorkflowInstance(
            org_id=org_id,
            definition_id=definition.id,
            definition_version=definition.version,
            subject_type=WorkflowSubjectType.MGMT_REVIEW,
            subject_id=mr.id,
            current_state="OPEN",
            revision=0,
        )
        session.add(instance)
        await session.flush()  # populate instance.id for the task FK

        # R55/D-5: build at midnight in the working_calendar's tz and snap FORWARD to a working day.
        cal = await resolve_calendar(session, org_id)
        due_at = (
            snap_to_working_day(
                datetime.datetime.combine(due, datetime.time(0, 0), tzinfo=cal.tz), cal
            )
            if due is not None
            else None
        )
        task = Task(
            org_id=org_id,
            instance_id=instance.id,
            stage_key=_PREPARE_STAGE_KEY,
            type=TaskType.MR_INPUT,
            assignee_user_id=owner.id,
            candidate_pool=[str(owner.id)],
            action_expected="prepare",
            state=TaskState.PENDING,
            due_at=due_at,
        )
        session.add(task)
        await session.flush()
        from ..notifications.dispatch import enqueue_task_notifications

        await enqueue_task_notifications(session, instance, [task])
        await session.commit()
        logger.info(
            "mgmt_review_sweep: minted Management Review %s (%s) for owner %s",
            mr.id,
            label,
            owner.id,
        )
        return {"mgmt_reviews_opened": 1, "skipped_open": 0, "skipped_lock_held": 0}
