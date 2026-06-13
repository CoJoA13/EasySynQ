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
from ...db.models._mgmt_review_enums import ManagementReviewCloseState, ReviewOutputType
from ...db.models._vault_enums import ChangeSignificance
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.dcr import Dcr
from ...db.models.review_output import ReviewOutput
from ..capa.service import build_capa
from ..dcr.service import raise_dcr
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
    existing = await _find_spawned_dcr_for_output(session, actor.org_id, output_id, idempotency_key)
    if existing is not None:
        return existing, False
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
