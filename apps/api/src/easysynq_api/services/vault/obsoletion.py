"""The §7.3 obsoletion-safety gate — the SINGLE source of truth shared by the direct
``document.obsolete`` path and the DCR RETIRE-implement path (slice S-dcr-5; doc 05 §7.3).

doc 05 §7.3 blocks retiring a document that would leave a coverage/dependency gap — when it (1)
``governs``-links an **active** process, (2) is ``references``-linked by an **Effective** document,
or (3) is the **sole** Effective coverer of a ★ mandatory clause (no replacement) — overridable only
by an explicit ``force_retire`` + a recorded justification (audited). S-dcr-2 shipped the pure
predicate (``domain/dcr/obsoletion.py``) + a read-only advisory; S-dcr-5 promotes it to a 409 gate
inside ``lifecycle.obsolete()`` so BOTH ``POST /documents/{id}/obsolete`` and a DCR RETIRE
``POST /dcrs/{id}/implement`` are guarded by ONE check (owner decision, decisions-register R40
S-dcr-5 addendum — amending the S-dcr-2 addendum that deferred the call site).

This module lives in the **vault** layer (not ``services/dcr``) so ``lifecycle.obsolete()`` can call
it without a vault→dcr import cycle; ``services/dcr/where_used.build_where_used`` consumes
``assemble_obsoletion_safety`` for its advisory too, so the gate and the advisory can never disagree
(the three input queries + the pure rule are computed in exactly ONE place).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._process_enums import ProcessState
from ...db.models._vault_enums import DocumentCurrentState, DocumentLinkType
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_link import DocumentLink
from ...db.models.documented_information import DocumentedInformation
from ...domain.dcr import ObsoletionSafety, evaluate_obsoletion
from ...problems import ProblemException
from . import repository as vault_repo


async def _governing_active_processes(
    session: AsyncSession, doc_id: uuid.UUID
) -> list[tuple[str, str]]:
    """ACTIVE processes this document ``governs`` (the §7.3 leg-1 input)."""
    links = await vault_repo.list_process_links(session, doc_id)
    return [(str(proc.id), proc.name) for _link, proc in links if proc.state is ProcessState.ACTIVE]


async def _referencing_effective_documents(
    session: AsyncSession, doc_id: uuid.UUID
) -> list[tuple[str, str]]:
    """Effective documents that ``references``-link TO ``doc_id`` (inbound ``references`` = the
    where-used ``referenced_by`` bucket; the §7.3 'referenced by an Effective document' leg)."""
    rows = await session.execute(
        select(DocumentedInformation.id, DocumentedInformation.identifier)
        .join(DocumentLink, DocumentLink.from_document_id == DocumentedInformation.id)
        .where(
            DocumentLink.to_document_id == doc_id,
            DocumentLink.link_type == DocumentLinkType.references,
            DocumentedInformation.current_state == DocumentCurrentState.Effective,
        )
        .order_by(DocumentedInformation.identifier.asc())
    )
    return [(str(did), ident) for did, ident in rows.all()]


async def _sole_star_clauses(
    session: AsyncSession, org_id: uuid.UUID, doc_id: uuid.UUID
) -> list[tuple[str, str]]:
    """★ clauses for which ``doc_id`` is the SOLE Effective coverer (the §7.3 'no replacement'
    leg) — any OTHER document with an Effective version mapped to the same ★ clause clears the leg.
    Moved here from ``services/dcr/repository.py`` so the gate + the advisory share ONE query (no
    vault→dcr cycle); Effective = ``current_effective_version_id IS NOT NULL`` (the checklist
    coverage semantics)."""
    doc_star = (
        select(ClauseMapping.clause_id)
        .join(Clause, Clause.id == ClauseMapping.clause_id)
        .where(
            ClauseMapping.documented_information_id == doc_id, Clause.is_mandatory_star.is_(True)
        )
        .subquery()
    )
    other_effective = (
        select(
            ClauseMapping.clause_id, func.count(func.distinct(DocumentedInformation.id)).label("n")
        )
        .join(
            DocumentedInformation,
            DocumentedInformation.id == ClauseMapping.documented_information_id,
        )
        .where(
            ClauseMapping.clause_id.in_(select(doc_star.c.clause_id)),
            ClauseMapping.documented_information_id != doc_id,
            DocumentedInformation.org_id == org_id,
            DocumentedInformation.current_effective_version_id.isnot(None),
        )
        .group_by(ClauseMapping.clause_id)
        .subquery()
    )
    rows = await session.execute(
        select(Clause.number, Clause.title)
        .join(doc_star, doc_star.c.clause_id == Clause.id)
        .outerjoin(other_effective, other_effective.c.clause_id == Clause.id)
        .where(func.coalesce(other_effective.c.n, 0) == 0)
        .order_by(Clause.number.asc())
    )
    return [(num, f"{num} {title}") for num, title in rows.all()]


async def assemble_obsoletion_safety(
    session: AsyncSession, org_id: uuid.UUID, doc_id: uuid.UUID
) -> ObsoletionSafety:
    """Resolve the three doc 05 §7.3 legs + apply the pure ``evaluate_obsoletion`` rule — the ONE
    place the obsoletion decision is computed (the gate + the advisory both call this)."""
    return evaluate_obsoletion(
        governing_active_processes=await _governing_active_processes(session, doc_id),
        referencing_effective_documents=await _referencing_effective_documents(session, doc_id),
        sole_star_clauses=await _sole_star_clauses(session, org_id, doc_id),
    )


async def assert_obsoletion_allowed(
    session: AsyncSession,
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    *,
    force_retire: bool,
    override_justification: str | None,
) -> ObsoletionSafety:
    """The §7.3 enforcement gate. Returns the computed safety (for audit) or raises:
    - **409 ``obsoletion_blocked``** when the safety is ``blocked`` and ``force_retire`` is unset —
      the structured reasons are surfaced so the caller can remediate (re-map ★ coverage / drop the
      reference) or force-retire.
    - **422** when ``force_retire`` is set without a non-empty ``override_justification`` (§7.3
      requires a recorded justification for the override).
    """
    safety = await assemble_obsoletion_safety(session, org_id, doc_id)
    if force_retire:
        if not override_justification or not override_justification.strip():
            raise ProblemException(
                status=422,
                code="validation_error",
                title="force_retire requires a justification",
                errors=[
                    {
                        "field": "override_justification",
                        "code": "required",
                        "message": "a justification is required to force-retire (doc 05 §7.3)",
                    }
                ],
            )
        return safety
    if safety.blocked:
        raise ProblemException(
            status=409,
            code="obsoletion_blocked",
            title="Obsoletion would create a coverage gap",
            detail="; ".join(r.detail for r in safety.reasons),
            errors=[
                {"field": "force_retire", "code": r.code, "message": r.detail}
                for r in safety.reasons
            ],
        )
    return safety
