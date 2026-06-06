"""The where-used / impact orchestration (slice S-dcr-2; doc 05 §7.2, §7.3, §5.3).

``build_where_used`` composes the doc 05 §7.2 categories for a target document (processes ·
child/ parent documents · referenced-by · forms/templates · records-produced-under · clauses ·
related CAPAs/findings) + the §7.3 ``obsoletion_safety`` advisory. ``build_impact_rows`` projects
that into the seven doc 05 §5.3 :class:`ImpactDimension` auto-populated facts the assess step
persists. Pure bucketing lives in ``domain/dcr/where_used.py``; the §7.3 rule in
``domain/dcr/obsoletion.py``. The service does the I/O (reusing ``vault_repo`` process/clause
helpers + the S-dcr-2 ``dcr_repo`` where-used reads).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._dcr_enums import ImpactDimension
from ...db.models._process_enums import ProcessState
from ...db.models._vault_enums import ChangeSignificance
from ...db.models.dcr import Dcr
from ...domain.dcr import bucket_links, evaluate_obsoletion
from ..vault import repository as vault_repo
from . import repository as repo

_EFFECTIVE = "Effective"


async def build_where_used(
    session: AsyncSession, org_id: uuid.UUID, doc_id: uuid.UUID
) -> dict[str, Any]:
    """The doc 05 §7.2 where-used panel for ``doc_id`` + the §7.3 obsoletion_safety advisory. The
    caller validates the document is in-org (404) before calling."""
    did = doc_id
    oid = org_id

    process_links = await vault_repo.list_process_links(session, did)
    processes = [
        {
            "id": str(proc.id),
            "name": proc.name,
            "state": proc.state.value,
            "is_active": proc.state is ProcessState.ACTIVE,
        }
        for _link, proc in process_links
    ]

    buckets = bucket_links(await repo.list_document_links(session, did))

    rec_count, rec_sample = await repo.records_produced_under(session, did)

    clause_rows = await vault_repo.list_clause_mappings(session, did)
    clauses = [
        {"number": c.number, "title": c.title, "is_mandatory_star": c.is_mandatory_star}
        for _m, c in clause_rows
    ]

    related = await repo.caused_by_links(session, did)

    # §7.3 obsoletion safety (advisory in S-dcr-2; the blocking gate is S-dcr-5). Build the
    # (id, label) pairs from the typed ORM rows (not the str|bool dicts) so the predicate's
    # list[tuple[str, str]] contract holds.
    governing_active = [
        (str(proc.id), proc.name)
        for _link, proc in process_links
        if proc.state is ProcessState.ACTIVE
    ]
    referencing_effective = [
        (link["document_id"], link["identifier"])
        for link in buckets["referenced_by"]
        if link["current_state"] == _EFFECTIVE
    ]
    sole_star = await repo.sole_star_coverage(session, oid, did)
    sole_star_pairs = [(c["number"], f"{c['number']} {c['title']}") for c in sole_star]
    safety = evaluate_obsoletion(
        governing_active_processes=governing_active,
        referencing_effective_documents=referencing_effective,
        sole_star_clauses=sole_star_pairs,
    )

    return {
        "document_id": str(doc_id),
        "processes": processes,
        "child_documents": buckets["child_documents"],
        "parent_documents": buckets["parent_documents"],
        "referenced_by": buckets["referenced_by"],
        "references_out": buckets["references_out"],
        "forms_templates": buckets["forms_templates"],
        "supersedes": buckets["supersedes"],
        "superseded_by": buckets["superseded_by"],
        "records_produced_under": {"count": rec_count, "sample": rec_sample},
        "clauses": clauses,
        "related_capas_findings": related,
        "obsoletion_safety": {
            "blocked": safety.blocked,
            "reasons": [{"code": r.code, "detail": r.detail} for r in safety.reasons],
        },
    }


def build_impact_rows(
    where_used: dict[str, Any], dcr: Dcr
) -> dict[ImpactDimension, dict[str, Any]]:
    """Project a where-used result into the seven doc 05 §5.3 impact dimensions' ``auto_populated``
    facts. A CREATE DCR (no target) → every dimension ``{"applicable": false}`` (the pack
    gap_summary
    N/A precedent). ``risk`` is always N/A in v1 (the Clause-6 risk register is unbuilt)."""
    if dcr.target_document_id is None:
        na = {"applicable": False, "reason": "a CREATE DCR has no target document"}
        return {dim: dict(na) for dim in ImpactDimension}

    star_clauses = [c for c in where_used["clauses"] if c["is_mandatory_star"]]
    is_major = dcr.change_significance is ChangeSignificance.MAJOR
    return {
        ImpactDimension.affected_processes: {
            "applicable": True,
            "processes": where_used["processes"],
        },
        ImpactDimension.dependent_documents: {
            "applicable": True,
            "child_documents": where_used["child_documents"],
            "parent_documents": where_used["parent_documents"],
            "referenced_by": where_used["referenced_by"],
            "forms_templates": where_used["forms_templates"],
        },
        ImpactDimension.records_produced_under: {
            "applicable": True,
            # Historical records stay pinned to their original version (immutable; INV-7) — no
            # retroactive change. Surfaced as count + sample for awareness.
            **where_used["records_produced_under"],
            "note": "historical records stay pinned to their version (no retroactive edit)",
        },
        ImpactDimension.training_awareness: {
            "applicable": True,
            # The read-acknowledge engine is a later family; v1 surfaces the MAJOR re-ack trigger
            # only.
            "reacknowledge_required": is_major,
            "note": "MAJOR revisions trigger read-acknowledge re-prompts (engine deferred)",
        },
        ImpactDimension.clause_coverage: {
            "applicable": True,
            "clauses": where_used["clauses"],
            "mandatory_star_clauses": star_clauses,
            "obsoletion_star_gap": where_used["obsoletion_safety"],
        },
        ImpactDimension.effectivity_transition: {
            "applicable": True,
            "proposed_effective_from": (
                dcr.proposed_effective_from.isoformat() if dcr.proposed_effective_from else None
            ),
            "scheduled": dcr.proposed_effective_from is not None,
        },
        ImpactDimension.risk: {
            "applicable": False,
            "reason": "the Clause 6 risk register is not built in v1",
        },
    }
