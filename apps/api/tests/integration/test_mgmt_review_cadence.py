"""S-mr-1 Phase 7 (the cadence Beat sweep) integration: ``sweep_mgmt_reviews`` mints the next
Scheduled Management Review (a Draft MR document + one ``MR_INPUT`` task on a fresh ``MGMT_REVIEW``
instance) when the org's cadence horizon is reached, and degrades honestly.

Service-level (no HTTP) — still needs ``app_under_test`` (it repoints ``get_sessionmaker()`` to the
testcontainer DB). The shared session DB means delta-based / run-scoped assertions only (the
S-ing-4 lesson): capture the MR-document count before, assert the delta after.

The three pins:
  * the FIRST run (no prior released MR, owner set) MINTS one Draft MR + one MR_INPUT task;
  * a SECOND run is a no-op (the org-scoped ``open_review_exists`` idempotency guard — the MR is
    still open);
  * a NULL ``mgmt_review_owner_user_id`` is the honest degrade — NOTHING is minted."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from easysynq_api.db.models._vault_enums import DocumentCurrentState
from easysynq_api.db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.management_review import ManagementReview
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.models.workflow import Task, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.mgmt_review.cadence import sweep_mgmt_reviews

from .test_quality_objectives import _grant

pytestmark = pytest.mark.integration


async def _set_owner(owner_id: uuid.UUID | None) -> uuid.UUID:
    """Set ``system_config.mgmt_review_owner_user_id`` for the single org; return the org id."""
    async with get_sessionmaker()() as s:
        org_id = (await s.execute(select(SystemConfig.org_id))).scalars().first()
        assert org_id is not None
        await s.execute(
            update(SystemConfig)
            .where(SystemConfig.org_id == org_id)
            .values(mgmt_review_owner_user_id=owner_id)
        )
        await s.commit()
        return org_id


async def _mr_doc_count(org_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(ManagementReview)
                .where(ManagementReview.org_id == org_id)
            )
        ).scalar_one()


async def _open_mr_doc_count(org_id: uuid.UUID) -> int:
    """Count of non-terminal (Draft/InReview/Approved/UnderRevision) MR documents for the org."""
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(ManagementReview)
                .join(DocumentedInformation, ManagementReview.id == DocumentedInformation.id)
                .where(
                    ManagementReview.org_id == org_id,
                    DocumentedInformation.current_state.in_(
                        (
                            DocumentCurrentState.Draft,
                            DocumentCurrentState.InReview,
                            DocumentCurrentState.Approved,
                            DocumentCurrentState.UnderRevision,
                        )
                    ),
                )
            )
        ).scalar_one()


async def _run_sweep() -> dict[str, int]:
    async with get_sessionmaker()() as session:
        return await sweep_mgmt_reviews(session)


async def test_sweep_mints_first_review_and_is_idempotent(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """With an owner set and no MR currently open, the FIRST sweep mints one Draft MR + one MR_INPUT
    task; a SECOND sweep is a no-op (the org-scoped open_review_exists guard)."""
    # JIT a real app_user to be the cadence owner (no permission keys needed to BE the owner).
    owner_subject = f"mr-cad-own-{uuid.uuid4()}"
    owner_id = await _grant(owner_subject, ())
    org_id = await _set_owner(owner_id)

    # Self-provide the precondition: the shared DB may already hold an open MR from a neighbour test
    # (the inverse-of-clean-DB trap). Only assert the mint delta when no MR is currently open.
    if await _open_mr_doc_count(org_id) > 0:
        pytest.skip("an MR is already open in the shared DB — the mint path is covered elsewhere")

    before = await _mr_doc_count(org_id)

    summary = await _run_sweep()
    assert summary["mgmt_reviews_opened"] == 1, summary
    assert summary.get("skipped_lock_held", 0) == 0, summary

    after = await _mr_doc_count(org_id)
    assert after == before + 1, (before, after)

    # The minted MR is a Draft document with a period label, owned by the configured owner; a single
    # MR_INPUT task on a fresh MGMT_REVIEW instance is assigned to the owner.
    async with get_sessionmaker()() as s:
        mr_doc = (
            await s.execute(
                select(DocumentedInformation)
                .join(ManagementReview, ManagementReview.id == DocumentedInformation.id)
                .where(ManagementReview.org_id == org_id)
                .order_by(DocumentedInformation.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert mr_doc.current_state is DocumentCurrentState.Draft
        assert mr_doc.owner_user_id == owner_id
        assert mr_doc.identifier.startswith("MR-")

        mr = (
            await s.execute(select(ManagementReview).where(ManagementReview.id == mr_doc.id))
        ).scalar_one()
        assert mr.period_label is not None  # the auto-minted "<year> Annual" label

        instance = (
            await s.execute(
                select(WorkflowInstance).where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.MGMT_REVIEW,
                    WorkflowInstance.subject_id == mr_doc.id,
                )
            )
        ).scalar_one()
        assert instance.current_state == "OPEN"

        tasks = (
            (await s.execute(select(Task).where(Task.instance_id == instance.id))).scalars().all()
        )
        assert len(tasks) == 1, tasks
        task = tasks[0]
        assert task.type is TaskType.MR_INPUT
        assert task.state is TaskState.PENDING
        assert task.assignee_user_id == owner_id
        assert task.candidate_pool == [str(owner_id)]
        assert task.stage_key == "prepare"
        assert task.action_expected == "prepare"

    # Idempotency: the MR is still open (Draft) → a second sweep mints nothing.
    count_before_second = await _mr_doc_count(org_id)
    second = await _run_sweep()
    assert second["mgmt_reviews_opened"] == 0, second
    assert second.get("skipped_open", 0) == 1, second
    assert await _mr_doc_count(org_id) == count_before_second


async def test_sweep_with_null_owner_is_a_no_op(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The honest degrade: with ``mgmt_review_owner_user_id`` NULL the sweep mints NOTHING (you
    cannot create an ownerless document)."""
    org_id = await _set_owner(None)
    before = await _mr_doc_count(org_id)

    summary = await _run_sweep()
    assert summary["mgmt_reviews_opened"] == 0, summary

    after = await _mr_doc_count(org_id)
    assert after == before, (before, after)
