"""On-demand spawns from a released Management Review's ACTION outputs (S-mr-3, clause 9.3 → §10/
§7.5). A CAPA spawn is a one-shot latch on ``review_output.spawned_capa_id``; a DCR spawn is 1:N
(the link lives one-way on the DCR), retry-safe via an Idempotency-Key. Both are *recording* acts:
they mint an audit event but NO signature (R43). Each reuses the canonical create core
(``build_capa`` / ``raise_dcr``) with ``_commit=False`` so the link + audit commit in ONE txn — the
``_auto_capa_for_finding`` / ``raise_dcr_from_capa`` atomic precedents."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._capa_enums import CapaSource, NcSeverity
from ...db.models._dcr_enums import DcrChangeType, DcrReasonClass, DcrSourceLinkType
from ...db.models._improvement_enums import ImprovementSource
from ...db.models._mgmt_review_enums import ManagementReviewCloseState, ReviewOutputType
from ...db.models._vault_enums import ChangeSignificance
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.dcr import Dcr
from ...db.models.improvement_initiative import ImprovementInitiative
from ...db.models.review_output import ReviewOutput
from ...problems import ProblemException
from ..capa.service import build_capa
from ..dcr.service import raise_dcr
from ..improvement.repository import get_spawned_initiative
from ..improvement.service import create_initiative
from .repository import get_review_doc
from .service import _conflict, _not_found  # package-private helpers (same package)


async def spawn_capa_for_output(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    severity: NcSeverity,
) -> ReviewOutput:
    """Spawn a CAPA from an ACTION output of a released review (F2 on-demand). One-shot latch on
    ``spawned_capa_id``. The output row is locked FOR UPDATE so two concurrent spawns serialize (the
    loser sees the latch set → 409, never an orphaned second CAPA). ``build_capa(_commit=False)`` →
    set the link → audit ``MGMT_REVIEW_CAPA_SPAWNED`` (no signature) → one commit."""
    pair = await get_review_doc(session, review_id)
    if pair is None:
        raise _not_found("Management Review")
    review, doc = pair
    # Org check (Codex #1; moot under D1 single-org but consistent with _require_draft's read-path
    # guard) — 404-collapse a cross-org id, never leak its existence.
    if doc.org_id != actor.org_id:
        raise _not_found("Management Review")
    output = (
        await session.execute(
            select(ReviewOutput)
            .where(ReviewOutput.id == output_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if output is None or output.management_review_id != review_id:
        raise _not_found("Review output")
    if output.output_type is not ReviewOutputType.ACTION:
        raise _conflict("output_not_actionable", "Only an ACTION output can spawn a CAPA")
    # Best-effort tracking-window guard: close_state is read unlocked, so a concurrent close_review
    # (which locks the MR satellite row, not this one) is not serialized against. Benign by design —
    # the close gate reads only the spawned MR_ACTION task state, never the spawned CAPA/DCR, so a
    # spawn in the tiny just-after-close window is still coherent.
    if review.close_state is not ManagementReviewCloseState.ActionsTracked:
        raise _conflict(
            "review_not_tracking",
            "A CAPA can only be spawned while the review's actions are being tracked "
            "(it must be released, and not already closed).",
        )
    if output.spawned_capa_id is not None:
        raise _conflict("capa_already_spawned", "This action has already spawned a CAPA")
    capa = await build_capa(
        session,
        actor,
        title=f"CAPA (from management review {doc.identifier})",
        severity=severity,
        source=CapaSource.review_output,
        process_id=None,
        origin_finding_id=None,
        raised_block={
            "source": CapaSource.review_output.value,
            "review_id": str(review_id),
            "output_id": str(output_id),
            "severity": severity.value,
        },
        _commit=False,
    )
    output.spawned_capa_id = capa.id
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.MGMT_REVIEW_CAPA_SPAWNED,
            object_type=AuditObjectType.document,
            object_id=doc.id,
            scope_ref=doc.identifier,
            after={"output_id": str(output_id), "capa_id": str(capa.id)},
        )
    )
    await session.commit()
    await session.refresh(output)
    return output


async def _find_spawned_dcr_for_output(
    session: AsyncSession, org_id: uuid.UUID, output_id: uuid.UUID, idempotency_key: str | None
) -> Dcr | None:
    """The DCR this output already spawned for ``idempotency_key`` (None when no key). Scoped to
    (org, this output, key) — the ``(org_id, source_link_id, spawn_idempotency_key)`` partial-UNIQUE
    (S-dcr-5)."""
    if idempotency_key is None:
        return None
    return (
        await session.execute(
            select(Dcr).where(
                Dcr.org_id == org_id,
                Dcr.source_link_type == DcrSourceLinkType.mgmt_review,
                Dcr.source_link_id == output_id,
                Dcr.spawn_idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def spawn_dcr_for_output(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    change_type: DcrChangeType,
    change_significance: ChangeSignificance,
    reason_text: str,
    target_document_id: uuid.UUID | None = None,
    proposed_effective_from: datetime.datetime | None = None,
    idempotency_key: str | None = None,
) -> tuple[Dcr, bool]:
    """Spawn a DCR from an ACTION output of a released review (F3, backend-only). 1:N — the link
    lives one-way on the DCR (``source_link_type=mgmt_review``, ``source_link_id=output.id``); an
    output may drive multiple changes. ``reason_class`` is fixed to ``mgmt_review``. An
    Idempotency-Key makes a retry return the same DCR (created=False). NO signature (raise_dcr emits
    only ``DCR_RAISED``). Mirrors ``raise_dcr_from_capa``."""
    pair = await get_review_doc(session, review_id)
    if pair is None:
        raise _not_found("Management Review")
    review, doc = pair
    # Org check (Codex #1; moot under D1 single-org but consistent with _require_draft's read-path
    # guard) — 404-collapse a cross-org id, never leak its existence.
    if doc.org_id != actor.org_id:
        raise _not_found("Management Review")
    output = await session.get(ReviewOutput, output_id)
    if output is None or output.management_review_id != review_id:
        raise _not_found("Review output")
    if output.output_type is not ReviewOutputType.ACTION:
        raise _conflict("output_not_actionable", "Only an ACTION output can spawn a DCR")
    # Idempotent replay FIRST — BEFORE the mutable close-state gate (the raise_dcr_from_capa
    # precedent; Codex). If the original raise succeeded but the response was lost and the review
    # was then closed, a retry with the same Idempotency-Key must still replay the already-created
    # DCR, not 409 ``review_not_tracking``.
    existing = await _find_spawned_dcr_for_output(session, actor.org_id, output_id, idempotency_key)
    if existing is not None:
        return existing, False
    # Best-effort tracking-window guard: close_state is read unlocked, so a concurrent close_review
    # (which locks the MR satellite row, not this one) is not serialized against. Benign by design —
    # the close gate reads only the spawned MR_ACTION task state, never the spawned CAPA/DCR, so a
    # spawn in the tiny just-after-close window is still coherent.
    if review.close_state is not ManagementReviewCloseState.ActionsTracked:
        raise _conflict(
            "review_not_tracking",
            "A DCR can only be spawned while the review's actions are being tracked "
            "(it must be released, and not already closed).",
        )
    try:
        dcr = await raise_dcr(
            session,
            actor,
            change_type=change_type,
            change_significance=change_significance,
            reason_class=DcrReasonClass.mgmt_review,
            reason_text=reason_text,
            target_document_id=target_document_id,
            source_link_type=DcrSourceLinkType.mgmt_review,
            source_link_id=output_id,
            proposed_effective_from=proposed_effective_from,
            spawn_idempotency_key=idempotency_key,
            _commit=False,
        )
        session.add(
            AuditEvent(
                org_id=actor.org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=actor.id,
                actor_type=ActorType.user,
                event_type=EventType.MGMT_REVIEW_DCR_SPAWNED,
                object_type=AuditObjectType.document,
                object_id=doc.id,
                scope_ref=doc.identifier,
                after={"output_id": str(output_id), "dcr_id": str(dcr.id)},
            )
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _find_spawned_dcr_for_output(
            session, actor.org_id, output_id, idempotency_key
        )
        if existing is not None:
            return existing, False
        raise
    await session.refresh(dcr)
    return dcr, True


async def spawn_initiative_for_output(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    title: str,
    description: str | None = None,
    target_outcome: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    process_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> tuple[ImprovementInitiative, bool]:
    """Raise an Improvement Initiative from an ACTION/IMPROVEMENT output of a released review
    (S-improvement-2, clause 9.3 → 10.3). 1:N — the link lives one-way on the initiative
    (``source=review``, ``source_link_id=output.id``); ``review_output.spawned_initiative_id`` stays
    reserved-null (R46 — un-reserving the reciprocal latch is a future owner-flipped migration, NOT
    this slice). An Idempotency-Key makes a retry return the same initiative (created=False). Emits
    ``MGMT_REVIEW_INITIATIVE_SPAWNED`` on the MR doc + ``INITIATIVE_RAISED`` (from
    create_initiative); NO signature (R43). Mirrors ``spawn_dcr_for_output``."""
    pair = await get_review_doc(session, review_id)
    if pair is None:
        raise _not_found("Management Review")
    review, doc = pair
    # Org check (Codex #1; moot under D1 single-org but consistent) — 404-collapse a cross-org id.
    if doc.org_id != actor.org_id:
        raise _not_found("Management Review")
    output = await session.get(ReviewOutput, output_id)
    if output is None or output.management_review_id != review_id:
        raise _not_found("Review output")
    # Eligibility (immutable): an ACTION or an IMPROVEMENT output may seed an initiative (the
    # IMPROVEMENT output type is reserved for exactly this — owner-confirmed eligibility set); a
    # DECISION is not improvable. 422 (the target is the wrong kind), kept symmetric with
    # /findings/{id}/raise-initiative's owner-locked ``finding_not_improvable`` 422 — a deliberate
    # divergence from the raise-capa/raise-dcr siblings' 409 ``output_not_actionable``.
    if output.output_type not in (ReviewOutputType.ACTION, ReviewOutputType.IMPROVEMENT):
        raise ProblemException(
            status=422,
            code="output_not_improvable",
            title="Only an ACTION or IMPROVEMENT output can raise an improvement initiative",
        )
    # Idempotent replay FIRST — BEFORE the mutable close-state gate (the spawn_dcr_for_output
    # precedent; a retry whose original succeeded but whose response was lost must replay even after
    # the review was closed, never 409 ``review_not_tracking``).
    existing = await get_spawned_initiative(session, actor.org_id, output_id, idempotency_key)
    if existing is not None:
        return existing, False
    # Best-effort tracking-window guard: close_state is read unlocked, so a concurrent close_review
    # (which locks the MR satellite row, not this one) is not serialized against. Benign by design,
    # exactly as for spawn_capa_for_output / spawn_dcr_for_output — the close gate reads only the
    # spawned MR_ACTION task state, never spawned initiatives, so an initiative spawned in the tiny
    # just-after-close window is still coherent (Codex P2; matches the documented siblings).
    if review.close_state is not ManagementReviewCloseState.ActionsTracked:
        raise _conflict(
            "review_not_tracking",
            "An improvement initiative can only be spawned while the review's actions are being "
            "tracked (it must be released, and not already closed).",
        )
    try:
        initiative = await create_initiative(
            session,
            actor,
            title=title,
            description=description,
            target_outcome=target_outcome,
            source=ImprovementSource.review,
            source_link_id=output_id,
            spawn_idempotency_key=idempotency_key,
            process_id=process_id,
            owner_user_id=owner_user_id,
            _commit=False,
        )
        session.add(
            AuditEvent(
                org_id=actor.org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=actor.id,
                actor_type=ActorType.user,
                event_type=EventType.MGMT_REVIEW_INITIATIVE_SPAWNED,
                object_type=AuditObjectType.document,
                object_id=doc.id,
                scope_ref=doc.identifier,
                after={"output_id": str(output_id), "initiative_id": str(initiative.id)},
            )
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await get_spawned_initiative(session, actor.org_id, output_id, idempotency_key)
        if existing is not None:
            return existing, False
        raise
    await session.refresh(initiative)
    return initiative, True
