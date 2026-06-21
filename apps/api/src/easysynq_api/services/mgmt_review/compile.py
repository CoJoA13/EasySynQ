"""compile_inputs (S-mr-1, clause 9.3.2) — the owner-grant-gated input compiler.

``compile_inputs(session, review, owner)`` (Draft only) attempts each of the seven sourced 9.3.2
reads **gated on the review OWNER's grants** (the MR document's ``owner_user_id``), not the
caller's — so the frozen 9.3 evidence is deterministic regardless of which authorized preparer
triggered the compile. A source the owner can't read becomes a named ``available=False`` gap row
(fail-closed per source, F3) — NEVER a 403 of the whole compile. The remaining five 9.3.2 inputs
have no backend source yet and ship as fixed-reason gap rows. (9.3.2(e) RISK_OPPORTUNITY_ACTIONS
joined the sourced set in S-risk-2 — the clause-6.1 register's governing snapshot, R49.)

⚠ F3 traps (load-bearing):
1. Gate on the OWNER's grants via the NON-auditing PDP path (``gather_grants`` + ``authorize`` at
   ``ResourceContext.system()`` — NOT ``pep.evaluate``/``enforce``/``require``, which emit an authz
   audit row per probe + raise 403). A typo'd key string returns empty grants → deny → a silent
   gap row, so the key strings are copied EXACTLY from the live endpoint ``require(...)`` calls.
2. The summaries are AS-OF snapshots (counts/RAG), not live queries retained for read-time.
3. Re-compile REPLACES the working ``review_input`` set (delete-then-insert) in one txn; Draft-only.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._mgmt_review_enums import ReviewInputType
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.documented_information import DocumentedInformation
from ...db.models.kpi_measurement import KpiMeasurement
from ...db.models.management_review import ManagementReview
from ...db.models.review_input import ReviewInput
from ...domain.authz import RequestContext, ResourceContext, authorize
from ...domain.context.summary import summarize_register as summarize_context
from ...domain.interested_parties.summary import summarize_register as summarize_parties
from ...domain.mgmt_review.inputs import (
    summarize_audits,
    summarize_capas_ncrs,
    summarize_kpis,
    summarize_process_perf,
    summarize_scorecard,
)
from ...domain.risk.summary import summarize_register
from ...problems import ProblemException
from ..audits import repository as audits_repo
from ..authz import gather_grants
from ..capa import repository as capa_repo
from ..context import governing_register as context_governing_register
from ..interested_parties import governing_register as interested_parties_governing_register
from ..objectives import compute_scorecard
from ..reports import compute_checklist
from ..risk import governing_register
from ..vault import drift_report

# The sourced reads' permission keys — copied VERBATIM from the live endpoint require(...)
# calls (a typo silently gap-rows everything; F3 trap 2). Complaints ride record.read (they are
# ad-hoc records — capa.py:294 _complaint_read = require("record.read"); there is no complaint.read
# key in the catalog).
_KEY_OBJECTIVES = "objective.read"
_KEY_AUDITS = "audit.read"
_KEY_CAPA = "capa.read"
_KEY_NCR = "ncr.read"
_KEY_COMPLAINT = "record.read"
_KEY_KPI = "kpi.read"
_KEY_CHECKLIST = "report.compliance_checklist.read"
_KEY_DRIFT = "drift.read"
# 9.3.2(e) reads the clause-6.1 Risk & Opportunity register's CONTROLLED (governing) snapshot — the
# register rows ride the seeded register.read key (S-risk, R49); no risk.* key exists.
_KEY_REGISTER = "register.read"

_REASON_NO_ACCESS = "not available (insufficient access)"
_REASON_NO_SOURCE = "not available (no structured source)"
_REASON_NO_PRIOR = "not available (no prior released review)"
_REASON_NO_REGISTER = "not available (no published register)"

# The four sourceless 9.3.2 inputs (c1/c7/d/f) — honest fixed-reason gap rows (s3/F4). 9.3.2(e)
# RISK_OPPORTUNITY_ACTIONS left this set in S-risk-2; 9.3.2(b) CONTEXT_CHANGES left it in
# S-interested-parties-2 — both are now sourced reads of a register's governing snapshot (a gap row
# only when the owner lacks register.read or no register has been published yet).
_SOURCELESS_GAPS = (
    ReviewInputType.CUSTOMER_SATISFACTION,
    ReviewInputType.SUPPLIER_PERFORMANCE,
    ReviewInputType.RESOURCE_ADEQUACY,
    ReviewInputType.IMPROVEMENT_OPPORTUNITIES,
)


async def _owner_holds(session: AsyncSession, owner: AppUser, key: str) -> bool:
    """PDP-check the OWNER's grant for ``key`` at SYSTEM scope via the NON-auditing path
    (``gather_grants`` + ``authorize`` — the compute_effective_permissions recipe). NOT the PEP's
    ``evaluate`` (an authz audit row per probe + a 403)."""
    grants = await gather_grants(session, owner.id, owner.org_id, key)
    decision = authorize(
        grants,
        key,
        ResourceContext.system(),
        RequestContext(now=datetime.datetime.now(datetime.UTC)),
    )
    return decision.allow


def _source_ref(
    *, available: bool, summary: dict[str, Any] | None, reason: str | None, now: str
) -> dict[str, Any]:
    """The ``review_input.source_ref`` envelope: ``{available, summary?, reason?, generated_at}``
    (F3 trap 4). An available row carries ``summary``; a gap row carries ``reason``."""
    ref: dict[str, Any] = {"available": available, "generated_at": now}
    if summary is not None:
        ref["summary"] = summary
    if reason is not None:
        ref["reason"] = reason
    return ref


async def _kpi_counts(session: AsyncSession, org_id: uuid.UUID) -> tuple[int, int]:
    """(readings, objectives_measured) — org-wide KPI-measurement count + distinct measured
    objectives (there is no org-wide KPI count helper; a fresh COUNT per s4/the brief)."""
    readings = (
        await session.execute(
            select(func.count()).select_from(KpiMeasurement).where(KpiMeasurement.org_id == org_id)
        )
    ).scalar_one()
    measured = (
        await session.execute(
            select(func.count(distinct(KpiMeasurement.objective_id))).where(
                KpiMeasurement.org_id == org_id,
                KpiMeasurement.objective_id.is_not(None),
            )
        )
    ).scalar_one()
    return int(readings), int(measured)


async def _build_row(
    session: AsyncSession, owner: AppUser, input_type: ReviewInputType, now: str
) -> dict[str, Any]:
    """Compute one sourced input row's ``source_ref`` — owner-grant-gated, fail-closed to a gap
    row. PRIOR_ACTIONS is a gap row until a second released review exists (v1)."""
    org_id = owner.org_id

    if input_type is ReviewInputType.OBJECTIVES_STATUS:
        if not await _owner_holds(session, owner, _KEY_OBJECTIVES):
            return _source_ref(available=False, summary=None, reason=_REASON_NO_ACCESS, now=now)
        summary = summarize_scorecard(await compute_scorecard(session, org_id))
        return _source_ref(available=True, summary=summary, reason=None, now=now)

    if input_type is ReviewInputType.AUDIT_RESULTS:
        if not await _owner_holds(session, owner, _KEY_AUDITS):
            return _source_ref(available=False, summary=None, reason=_REASON_NO_ACCESS, now=now)
        summary = summarize_audits(await audits_repo.list_audits(session, org_id))
        return _source_ref(available=True, summary=summary, reason=None, now=now)

    if input_type is ReviewInputType.NONCONFORMITIES_CAPA:
        # Needs the union (capa.read + ncr.read + record.read) — any missing key fails the source
        # closed (a partial summary would be misleading evidence).
        for key in (_KEY_CAPA, _KEY_NCR, _KEY_COMPLAINT):
            if not await _owner_holds(session, owner, key):
                return _source_ref(available=False, summary=None, reason=_REASON_NO_ACCESS, now=now)
        summary = summarize_capas_ncrs(
            await capa_repo.list_capas(session, org_id),
            await capa_repo.list_ncrs(session, org_id),
            await capa_repo.list_complaints(session, org_id),
        )
        return _source_ref(available=True, summary=summary, reason=None, now=now)

    if input_type is ReviewInputType.MONITORING_RESULTS:
        if not await _owner_holds(session, owner, _KEY_KPI):
            return _source_ref(available=False, summary=None, reason=_REASON_NO_ACCESS, now=now)
        readings, measured = await _kpi_counts(session, org_id)
        summary = summarize_kpis(readings=readings, objectives_measured=measured)
        return _source_ref(available=True, summary=summary, reason=None, now=now)

    if input_type is ReviewInputType.PROCESS_PERFORMANCE:
        if not await _owner_holds(session, owner, _KEY_CHECKLIST):
            return _source_ref(available=False, summary=None, reason=_REASON_NO_ACCESS, now=now)
        checklist = await compute_checklist(session, org_id)
        # drift.read is a SEPARATE key — its absence drops only the integrity block (drift_status
        # takes NO org_id; single-org vault). The checklist itself is the source's gate.
        drift = (
            await drift_report.drift_status(session)
            if await _owner_holds(session, owner, _KEY_DRIFT)
            else None
        )
        summary = summarize_process_perf(checklist, drift)
        return _source_ref(available=True, summary=summary, reason=None, now=now)

    if input_type is ReviewInputType.RISK_OPPORTUNITY_ACTIONS:
        # 9.3.2(e) — the clause-6.1 register's CONTROLLED (governing) read-of-record: the frozen
        # snapshot of the head's current Effective version, NEVER the live working satellite (which
        # during UnderRevision carries the steward's unpublished edits). The summary is captured
        # as-of and frozen into the WORM minutes, so it must be the point-in-time governing view
        # (spec §3/§8; R49 L2 — the band grades against the governing frozen criteria). A Draft-only
        # register (no Effective version yet) is a gap row, the PRIOR_ACTIONS-until-a-release shape.
        if not await _owner_holds(session, owner, _KEY_REGISTER):
            return _source_ref(available=False, summary=None, reason=_REASON_NO_ACCESS, now=now)
        register = await governing_register(session, org_id)
        if register is None:
            return _source_ref(available=False, summary=None, reason=_REASON_NO_REGISTER, now=now)
        summary = summarize_register(register)
        return _source_ref(available=True, summary=summary, reason=None, now=now)

    if input_type is ReviewInputType.CONTEXT_CHANGES:
        # 9.3.2(b) — "changes in external/internal issues AND interested parties": the CONTROLLED
        # (governing) read-of-record of BOTH clause-4 register heads' current Effective frozen
        # snapshots, NEVER the live working satellites (which during UnderRevision carry the
        # steward's unpublished edits — the WORM minutes must freeze the point-in-time governing
        # view). One register.read gate covers both (org-level, SYSTEM). The nested
        # {context, interested_parties} envelope keeps each pure projection's JSON leaves; a half is
        # null when its register is unpublished. Available if EITHER is published; a gap only when
        # BOTH are (the no-published-register shape).
        if not await _owner_holds(session, owner, _KEY_REGISTER):
            return _source_ref(available=False, summary=None, reason=_REASON_NO_ACCESS, now=now)
        context_reg = await context_governing_register(session, org_id)
        parties_reg = await interested_parties_governing_register(session, org_id)
        if context_reg is None and parties_reg is None:
            return _source_ref(available=False, summary=None, reason=_REASON_NO_REGISTER, now=now)
        context_changes: dict[str, Any] = {
            "context": summarize_context(context_reg) if context_reg is not None else None,
            "interested_parties": (
                summarize_parties(parties_reg) if parties_reg is not None else None
            ),
        }
        return _source_ref(available=True, summary=context_changes, reason=None, now=now)

    if input_type is ReviewInputType.PRIOR_ACTIONS:
        # Gap row until a second review exists (v1 — the prior-MR outputs read lands with the 2nd
        # review; the spec accepts this for the first cycle).
        # Future wiring target: inputs.summarize_prior_actions (the 2nd-review slice).
        return _source_ref(available=False, summary=None, reason=_REASON_NO_PRIOR, now=now)

    # Defensive — every sourced type is handled above; an unrecognised one fails closed.
    return _source_ref(available=False, summary=None, reason=_REASON_NO_SOURCE, now=now)


# The ordered, positioned 9.3.2 set (the canonical 9.3.2(a)…(f) order — the enum's declaration
# order). Sourced types resolve through _build_row; the four sourceless ones are fixed gap rows.
_INPUT_ORDER: tuple[ReviewInputType, ...] = tuple(ReviewInputType)


async def compile_inputs(
    session: AsyncSession,
    review: ManagementReview,
    owner: AppUser,
    caller: AppUser,
) -> list[ReviewInput]:
    """Re-compile the working ``review_input`` set (Draft-only) under the OWNER's grants. Replaces
    the existing rows (delete-then-insert) in one txn, then emits MGMT_REVIEW_INPUTS_COMPILED. The
    caller (the route) owns loading ``review``/``owner`` + the trigger-gate enforce.

    ``owner`` gates the sourced READS (F3 determinism); ``caller`` is the person who TRIGGERED the
    compile and is the audit actor — they differ when a delegate preparer recompiles."""
    doc = await session.get(DocumentedInformation, review.id)
    if doc is None:  # pragma: no cover — the satellite exists, so the base must too
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    if doc.current_state is not DocumentCurrentState.Draft:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Management Review inputs are only compilable in Draft",
            detail=f"current_state is {doc.current_state.value}",
        )

    now = datetime.datetime.now(datetime.UTC).isoformat()

    # Replace the working set (delete-then-insert; a re-compile is the as-of refresh, s2/s3).
    await session.execute(delete(ReviewInput).where(ReviewInput.management_review_id == review.id))

    created: list[ReviewInput] = []
    for position, input_type in enumerate(_INPUT_ORDER):
        if input_type in _SOURCELESS_GAPS:
            source_ref = _source_ref(
                available=False, summary=None, reason=_REASON_NO_SOURCE, now=now
            )
            available = False
        else:
            source_ref = await _build_row(session, owner, input_type, now)
            available = bool(source_ref["available"])
        ri = ReviewInput(
            org_id=review.org_id,
            management_review_id=review.id,
            input_type=input_type,
            available=available,
            source_ref=source_ref,
            position=position,
        )
        session.add(ri)
        created.append(ri)

    await session.flush()
    session.add(
        AuditEvent(
            org_id=owner.org_id,
            # Intentionally a fresh read — the audit wall-clock, NOT the frozen `now`/generated_at.
            occurred_at=datetime.datetime.now(datetime.UTC),
            # The audit actor is the CALLER who triggered the compile, not the owner whose grants
            # gated the reads (a delegate preparer ≠ the MR owner).
            actor_id=caller.id,
            actor_type=ActorType.user,
            event_type=EventType.MGMT_REVIEW_INPUTS_COMPILED,
            object_type=AuditObjectType.document,
            object_id=review.id,
            scope_ref=doc.identifier,
            after={
                "inputs": len(created),
                "available": sum(1 for ri in created if ri.available),
            },
        )
    )
    await session.commit()
    return created
