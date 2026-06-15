"""Management Review read queries (S-mr-1, clause 9.3). Returns the satellite + the joined base
identity (the ``list_objectives`` shape); ``get_review_doc`` loads the base doc + satellite with
``populate_existing=True`` for the freeze caller (the S-drift-1 stale-identity-map trap)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._mgmt_review_enums import ReviewOutputType
from ...db.models._signature_enums import SignatureMeaning, SignatureMethod, SignedObjectType
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models._workflow_enums import TaskState
from ...db.models.app_user import AppUser
from ...db.models.documented_information import DocumentedInformation
from ...db.models.management_review import ManagementReview
from ...db.models.review_input import ReviewInput
from ...db.models.review_output import ReviewOutput
from ...db.models.signature_event import SignatureEvent
from ...db.models.workflow import Task

# (mr, identifier, title, current_state)
ReviewRow = tuple[ManagementReview, str, str, DocumentCurrentState]

# A close-gate row: (output_type, spawned MR_ACTION task state | None). The pure
# domain.mgmt_review.output_blocks_close predicate is applied to each.
ReviewCloseGateRow = tuple[ReviewOutputType, TaskState | None]

# The pre-release "open" cycle: a Management Review document that has not yet reached a terminal /
# filed state (Effective/Superseded/Obsolete). The cadence sweep skips minting a new one while any
# such MR exists (the org-scoped idempotency guard, s6).
_OPEN_STATES = (
    DocumentCurrentState.Draft,
    DocumentCurrentState.InReview,
    DocumentCurrentState.Approved,
    DocumentCurrentState.UnderRevision,
)


def _row_select() -> Select[Any]:
    return select(
        ManagementReview,
        DocumentedInformation.identifier,
        DocumentedInformation.title,
        DocumentedInformation.current_state,
    ).join(DocumentedInformation, ManagementReview.id == DocumentedInformation.id)


async def get_review(session: AsyncSession, review_id: uuid.UUID) -> ManagementReview | None:
    return await session.get(ManagementReview, review_id)


async def get_review_doc(
    session: AsyncSession, review_id: uuid.UUID, *, for_update: bool = False
) -> tuple[ManagementReview, DocumentedInformation] | None:
    """Load the base document + the satellite with ``populate_existing=True`` — the freeze caller
    locks them and the authz resolver may already have identity-mapped the satellite (the S-drift-1
    trap; a stale satellite would freeze yesterday's minutes). ``for_update`` takes a ``FOR UPDATE``
    lock on the DOC row so a Draft edit serializes against a concurrent submit-freeze (Codex #3)."""
    doc_q = (
        select(DocumentedInformation)
        .where(DocumentedInformation.id == review_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        doc_q = doc_q.with_for_update()
    doc = (await session.execute(doc_q)).scalar_one_or_none()
    mr = (
        await session.execute(
            select(ManagementReview)
            .where(ManagementReview.id == review_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if doc is None or mr is None:
        return None
    return mr, doc


async def list_reviews(session: AsyncSession, org_id: uuid.UUID) -> list[ReviewRow]:
    rows = await session.execute(
        _row_select()
        .where(ManagementReview.org_id == org_id)
        .order_by(DocumentedInformation.identifier)
    )
    return [tuple(r) for r in rows.all()]


async def get_review_row(session: AsyncSession, review_id: uuid.UUID) -> ReviewRow | None:
    row = (await session.execute(_row_select().where(ManagementReview.id == review_id))).first()
    return tuple(row) if row is not None else None


async def list_inputs(session: AsyncSession, review_id: uuid.UUID) -> Sequence[ReviewInput]:
    rows = await session.execute(
        select(ReviewInput)
        .where(ReviewInput.management_review_id == review_id)
        .order_by(ReviewInput.position, ReviewInput.created_at)
    )
    return list(rows.scalars())


async def list_outputs(session: AsyncSession, review_id: uuid.UUID) -> Sequence[ReviewOutput]:
    rows = await session.execute(
        select(ReviewOutput)
        .where(ReviewOutput.management_review_id == review_id)
        .order_by(ReviewOutput.created_at)
    )
    return list(rows.scalars())


async def open_review_exists(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """True iff a non-terminal (Draft/InReview/Approved/UnderRevision) Management Review document
    exists — the cadence sweep's org-scoped idempotency guard (s6)."""
    exists = (
        await session.execute(
            select(ManagementReview.id)
            .join(DocumentedInformation, ManagementReview.id == DocumentedInformation.id)
            .where(
                ManagementReview.org_id == org_id,
                DocumentedInformation.current_state.in_(_OPEN_STATES),
            )
            .limit(1)
        )
    ).first()
    return exists is not None


async def outputs_for_close_gate(
    session: AsyncSession, review_id: uuid.UUID
) -> Sequence[ReviewCloseGateRow]:
    """Every output of the review with the facts the close gate needs: its type and its spawned
    ``MR_ACTION`` task's state (LEFT JOIN on ``spawned_task_id``; ``None`` when unlinked/unspawned).

    The **OUTERJOIN is load-bearing**: an ``ACTION`` output with no spawned task must still appear
    in the row set with ``state == None`` so ``output_blocks_close``'s fail-closed leg fires. An
    INNER join would silently DROP unlinked actions from the blocker set — a fail-OPEN bug."""
    rows = await session.execute(
        select(ReviewOutput.output_type, Task.state)
        .outerjoin(Task, Task.id == ReviewOutput.spawned_task_id)
        .where(ReviewOutput.management_review_id == review_id)
    )
    return [(ot, ts) for ot, ts in rows.all()]


# (display_name | None, signer_user_id | None, meaning, created_at, method).
# signer_user_id distinguishes a true system signature (null) from a human whose display_name is
# null (still a human — the pack must not render them as "system"); no email (PII) is selected.
SignoffRow = tuple[
    str | None, uuid.UUID | None, SignatureMeaning, datetime.datetime, SignatureMethod
]


async def list_signoffs_for_version(
    session: AsyncSession, version_id: uuid.UUID
) -> list[SignoffRow]:
    """The approval + release signatures on an MR's released version, oldest first (OUTER JOIN to
    app_user — a null signer is a Beat-activated future-dated release). Returns the signer's
    display_name, email, and signer_user_id so the caller can preserve human identity (a human with
    no display_name falls back to email/id; only a null signer_user_id is a system signature). The
    MR rides document.approve/release, so the signatures carry signed_object_type=document_version
    + signed_object_id = the version id."""
    rows = await session.execute(
        select(
            AppUser.display_name,
            SignatureEvent.signer_user_id,
            SignatureEvent.meaning,
            SignatureEvent.created_at,
            SignatureEvent.method,
        )
        .outerjoin(AppUser, AppUser.id == SignatureEvent.signer_user_id)
        .where(
            SignatureEvent.voided_by.is_(None),
            SignatureEvent.signed_object_type == SignedObjectType.document_version,
            SignatureEvent.signed_object_id == version_id,
            SignatureEvent.meaning.in_([SignatureMeaning.approval, SignatureMeaning.release]),
        )
        .order_by(SignatureEvent.created_at)
    )
    return [tuple(r) for r in rows.all()]
