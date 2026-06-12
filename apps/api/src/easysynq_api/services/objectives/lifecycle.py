"""Quality-Objective lifecycle (S-obj-3/S-obj-4, clause 6.2). ``submit_objective_for_review``
folds a content-aware commitment-freeze + the T2/T9 transition + the approval-workflow
instantiation into ONE transaction, then the OBJ rides the generic DOCUMENT decide leg (approve)
+ ``release`` cutover, unchanged."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.documented_information import DocumentedInformation
from ...db.models.quality_objective import QualityObjective
from ...domain.objectives.commitment import build_commitment, commitment_needs_freeze
from ...problems import ProblemException
from ..vault import VaultAuditSink, audit_transition, locks, submit_review
from ..vault import repository as vault_repo
from ..vault.service import checkin_objective_commitment
from ..workflow import instantiate_approval

logger = logging.getLogger(__name__)

_EDITABLE = (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision)


async def submit_objective_for_review(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    qo: QualityObjective,
    *,
    change_reason: str | None = None,
) -> DocumentedInformation:
    """Freeze the commitment when it changed (``commitment_needs_freeze``) → T2/T9 → instantiate
    the approval workflow → audit, all in one transaction. ``doc`` MUST be loaded
    ``with_for_update`` + ``populate_existing`` (the authz resolver already identity-mapped both
    rows — the S-drift-1 trap; a stale satellite would freeze yesterday's commitment). From
    UnderRevision (T9), the start-revision WorkingDraft is deleted in the txn and its edit lock
    released post-commit (the generic-checkin pattern, O-4)."""
    if doc.current_state not in _EDITABLE:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Objective is not in Draft or UnderRevision",
            detail=f"current_state is {doc.current_state.value}",
        )
    working = build_commitment(
        target_value=qo.target_value,
        unit=qo.unit,
        direction=qo.direction,
        due_date=qo.due_date,
        at_risk_threshold=qo.at_risk_threshold,
        baseline_value=qo.baseline_value,
        policy_id=qo.policy_id,
    )
    # S-obj-4: freeze unless the latest version is a Draft already carrying the CURRENT working
    # commitment — covers the first submit (no version), a revision (the latest is the governing
    # Effective version — the S-obj-3 ``is None`` guard would have SKIPPED here and T9 would have
    # IllegalTransition'd), a PATCH since the last freeze, and a legacy commitment-less
    # byte-version (the generic path is guarded now; belt-and-braces).
    latest = await vault_repo.latest_version(session, doc.id)
    if commitment_needs_freeze(
        latest_version_state=latest.version_state if latest is not None else None,
        latest_commitment=(
            (latest.metadata_snapshot or {}).get("objective_commitment")
            if latest is not None
            else None
        ),
        working=working,
    ):
        default_reason = (
            "Objective commitment revised"
            if doc.current_state is DocumentCurrentState.UnderRevision
            else "Objective commitment submitted for review"
        )
        await checkin_objective_commitment(
            session,
            vault_sink,
            actor,
            doc,
            commitment=working,
            change_reason=(change_reason or "").strip() or default_reason,
            change_significance="MAJOR",
        )
    # O-4: leaving the editable window — drop the start-revision WorkingDraft (in-txn) and release
    # its edit lock post-commit (the generic-checkin pattern). Release regardless of holder: the
    # objective surface owns its lock (checkout is guarded on OBJ rows, so only start_revision
    # mints one). No WD exists on a plain Draft submit — both steps no-op.
    wd = await vault_repo.get_working_draft(session, doc.id)
    token = (wd.lock_token or "") if wd is not None else ""
    if wd is not None:
        await session.delete(wd)
    result = await submit_review(session, actor, doc)
    await instantiate_approval(session, result.doc, actor)
    audit_transition(session, vault_sink, result, actor)
    await session.commit()
    if token and not await locks.release(doc.id, token):
        logger.warning("objective submit: edit-lock token no longer matched (lock had lapsed)")
    return result.doc
