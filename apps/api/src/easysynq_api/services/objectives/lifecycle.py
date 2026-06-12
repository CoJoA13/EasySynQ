"""Quality-Objective lifecycle (S-obj-3, clause 6.2). ``submit_objective_for_review`` folds a
commitment-freeze + the T2 transition + the approval-workflow instantiation into ONE transaction,
then the OBJ rides the generic DOCUMENT decide leg (approve) + ``release`` cutover, unchanged."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.documented_information import DocumentedInformation
from ...db.models.quality_objective import QualityObjective
from ...domain.objectives.commitment import build_commitment
from ...problems import ProblemException
from ..vault import VaultAuditSink, audit_transition, submit_review
from ..vault import repository as vault_repo
from ..vault.service import checkin_objective_commitment
from ..workflow import instantiate_approval


async def submit_objective_for_review(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    qo: QualityObjective,
) -> DocumentedInformation:
    """Freeze the commitment (first submit only) → T2 (Draft→InReview) → instantiate the approval
    workflow → audit, all in one transaction. ``doc`` MUST be loaded ``with_for_update`` +
    ``populate_existing`` (the authz resolver already identity-mapped it — the S-drift-1 trap)."""
    if doc.current_state is not DocumentCurrentState.Draft:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Objective is not in Draft",
            detail=f"current_state is {doc.current_state.value}",
        )
    # Freeze a new version IFF the latest version doesn't already carry the frozen commitment —
    # keyed on the SNAPSHOT FIELD, never bare version existence (Codex P2): the generic
    # /documents checkout/checkin byte-path accepts an OBJ id, and a commitment-less byte-version
    # must not suppress the freeze (submit would then advance an unfrozen version to release).
    # A re-submit after request_changes still skips (the latest version IS the frozen one) — there
    # is no commitment-edit path in v1, so re-freezing identical bytes would be a duplicate.
    latest = await vault_repo.latest_version(session, doc.id)
    if latest is None or (latest.metadata_snapshot or {}).get("objective_commitment") is None:
        commitment = build_commitment(
            target_value=qo.target_value,
            unit=qo.unit,
            direction=qo.direction,
            due_date=qo.due_date,
            at_risk_threshold=qo.at_risk_threshold,
            baseline_value=qo.baseline_value,
            policy_id=qo.policy_id,
        )
        await checkin_objective_commitment(
            session,
            vault_sink,
            actor,
            doc,
            commitment=commitment,
            change_reason="Objective commitment submitted for review",
            change_significance="MAJOR",
        )
    result = await submit_review(session, actor, doc)
    await instantiate_approval(session, result.doc, actor)
    audit_transition(session, vault_sink, result, actor)
    await session.commit()
    return result.doc
