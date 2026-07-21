"""Vault DB access: atomic identifier-sequence allocation + the document/version lookups.

``allocate_seq`` is a single ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` so concurrent
``POST /documents`` for the same (type, area) never collide on ``{SEQ}``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, asc, desc, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import VersionState
from ...db.models.blob import Blob
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.form_template import FormTemplate
from ...db.models.framework import Framework
from ...db.models.numbering_counter import NumberingCounter
from ...db.models.org_role import OrgRole
from ...db.models.process import Process
from ...db.models.process_edge import ProcessEdge
from ...db.models.process_link import ProcessLink
from ...db.models.quality_objective import QualityObjective
from ...db.models.supplier import Supplier
from ...db.models.system_config import SystemConfig
from ...db.models.working_draft import WorkingDraft


async def allocate_seq(
    session: AsyncSession, org_id: uuid.UUID, type_code: str, area_code: str
) -> int:
    """Atomically allocate the next ``{SEQ}`` for an (org, type, area) — returns 1, 2, 3, …."""
    stmt = (
        pg_insert(NumberingCounter)
        .values(org_id=org_id, type_code=type_code, area_code=area_code, next_value=1)
        .on_conflict_do_update(
            index_elements=["org_id", "type_code", "area_code"],
            set_={"next_value": NumberingCounter.next_value + 1},
        )
        .returning(NumberingCounter.next_value)
    )
    return (await session.execute(stmt)).scalar_one()


async def get_document(session: AsyncSession, doc_id: uuid.UUID) -> DocumentedInformation | None:
    return await session.get(DocumentedInformation, doc_id)


async def get_document_type(session: AsyncSession, dt_id: uuid.UUID) -> DocumentType | None:
    return await session.get(DocumentType, dt_id)


async def get_form_template(session: AsyncSession, doc_id: uuid.UUID) -> FormTemplate | None:
    """The ``form_template`` subtype row for a document id (S-rec-3), if it is a Form/Template."""
    return await session.get(FormTemplate, doc_id)


async def effective_version(session: AsyncSession, doc_id: uuid.UUID) -> DocumentVersion | None:
    """The single Effective version of a document (INV-1 guarantees at most one), or None."""
    return (
        await session.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.version_state == VersionState.Effective,
            )
        )
    ).scalar_one_or_none()


async def latest_non_obsolete_version(
    session: AsyncSession, doc_id: uuid.UUID
) -> DocumentVersion | None:
    """The highest-``version_seq`` version that is not Obsolete (the S-rec-3 pre-release-capture
    resolution — a deterministic pick, never a bare ``latest_version`` that ignores state)."""
    return (
        await session.execute(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.version_state != VersionState.Obsolete,
            )
            .order_by(desc(DocumentVersion.version_seq))
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_framework(
    session: AsyncSession, org_id: uuid.UUID, code: str = "iso9001:2015"
) -> Framework | None:
    return (
        await session.execute(
            select(Framework).where(Framework.org_id == org_id, Framework.code == code)
        )
    ).scalar_one_or_none()


async def get_working_draft(session: AsyncSession, doc_id: uuid.UUID) -> WorkingDraft | None:
    return (
        await session.execute(select(WorkingDraft).where(WorkingDraft.document_id == doc_id))
    ).scalar_one_or_none()


async def latest_version(session: AsyncSession, doc_id: uuid.UUID) -> DocumentVersion | None:
    return (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc_id)
            .order_by(desc(DocumentVersion.version_seq))
            .limit(1)
        )
    ).scalar_one_or_none()


async def next_version_seq(session: AsyncSession, doc_id: uuid.UUID) -> int:
    current = (
        await session.execute(
            select(func.max(DocumentVersion.version_seq)).where(
                DocumentVersion.document_id == doc_id
            )
        )
    ).scalar_one_or_none()
    return (current or 0) + 1


async def get_blob(session: AsyncSession, sha256: str) -> Blob | None:
    return await session.get(Blob, sha256)


async def capture_pre_release_enabled(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """The org's S-rec-3 opt-in to capture a Mode-B record against a non-Effective form template
    (default OFF). Drives the pre-release version resolution in capture + effective-form-schema."""
    return bool(
        await session.scalar(
            select(SystemConfig.capture_pre_release_templates).where(SystemConfig.org_id == org_id)
        )
    )


# --- clause IA / clause_mapping (S9) -----------------------------------------------------


async def list_clauses(session: AsyncSession, framework_id: uuid.UUID) -> list[Clause]:
    """The read-only clause spine for a framework, ordered by the natural clause-number key so the
    client can rebuild the 4 → 4.4 → 4.4.1 tree from the flat list + ``parent_id``."""
    return list(
        (
            await session.execute(
                select(Clause)
                .where(Clause.framework_id == framework_id)
                .order_by(asc(_clause_sort_key()))
            )
        )
        .scalars()
        .all()
    )


def _clause_sort_key() -> ColumnElement[Any]:
    """Sort clause numbers numerically per dotted segment ('8.5' before '8.10', '10' after '9').
    ``string_to_array(number,'.')::int[]`` orders the segments as integers, not lexically."""
    return func.cast(func.string_to_array(Clause.number, "."), postgresql.ARRAY(postgresql.INTEGER))


async def get_clause(session: AsyncSession, clause_id: uuid.UUID) -> Clause | None:
    return await session.get(Clause, clause_id)


async def count_clause_mappings(session: AsyncSession, doc_id: uuid.UUID) -> int:
    """How many clauses a document maps to (the submit-review >=1 gate reads this)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(ClauseMapping)
            .where(ClauseMapping.documented_information_id == doc_id)
        )
    ).scalar_one()


async def get_clause_mapping(
    session: AsyncSession, doc_id: uuid.UUID, clause_id: uuid.UUID
) -> ClauseMapping | None:
    return (
        await session.execute(
            select(ClauseMapping).where(
                ClauseMapping.documented_information_id == doc_id,
                ClauseMapping.clause_id == clause_id,
            )
        )
    ).scalar_one_or_none()


async def list_clause_mappings(
    session: AsyncSession, doc_id: uuid.UUID
) -> list[tuple[ClauseMapping, Clause]]:
    """A document's clause mappings joined to the clause detail (for the per-document read)."""
    rows = (
        await session.execute(
            select(ClauseMapping, Clause)
            .join(Clause, ClauseMapping.clause_id == Clause.id)
            .where(ClauseMapping.documented_information_id == doc_id)
            .order_by(asc(_clause_sort_key()))
        )
    ).all()
    return [(m, c) for m, c in rows]


async def clause_numbers_for_docs(
    session: AsyncSession, doc_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Batch-load each document's mapped clause **numbers** (the ``clause_refs`` serializer field on
    ``GET /documents``, S10). One ``clause_mapping ⨝ clause`` query over the page — index-backed by
    ``ix_clause_mapping_documented_information_id``, no N+1 (the ``mirror.fetch_clause_refs`` idea).
    Numbers are numeric-sorted per doc ('8.5' before '8.10'); docs with no mapping are absent."""
    if not doc_ids:
        return {}
    rows = (
        await session.execute(
            select(ClauseMapping.documented_information_id, Clause.number)
            .join(Clause, ClauseMapping.clause_id == Clause.id)
            .where(ClauseMapping.documented_information_id.in_(doc_ids))
            .order_by(asc(_clause_sort_key()))
        )
    ).all()
    out: dict[uuid.UUID, list[str]] = {}
    for doc_id, number in rows:
        out.setdefault(doc_id, []).append(number)
    return out


# --- process IA (S9c, doc 02 §3.3, doc 14 §4) --------------------------------------------
# By-id lookups fetch then let the handler org-validate (the get_clause/get_clause_mapping
# precedent) — every S9c handler checks ``org_id == caller.org_id`` before use; list/by-name
# helpers filter by org_id directly.


async def get_process(session: AsyncSession, process_id: uuid.UUID) -> Process | None:
    return await session.get(Process, process_id)


async def get_process_by_name(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> Process | None:
    return (
        await session.execute(select(Process).where(Process.org_id == org_id, Process.name == name))
    ).scalar_one_or_none()


async def list_processes(session: AsyncSession, org_id: uuid.UUID) -> list[Process]:
    """All processes in the org, name-ordered (the process map + list reads, org-wide per S9c)."""
    return list(
        (
            await session.execute(
                select(Process).where(Process.org_id == org_id).order_by(asc(Process.name))
            )
        )
        .scalars()
        .all()
    )


async def list_process_edges(session: AsyncSession, org_id: uuid.UUID) -> list[ProcessEdge]:
    return list(
        (await session.execute(select(ProcessEdge).where(ProcessEdge.org_id == org_id)))
        .scalars()
        .all()
    )


async def get_process_edge(session: AsyncSession, edge_id: uuid.UUID) -> ProcessEdge | None:
    return await session.get(ProcessEdge, edge_id)


async def get_process_edge_pair(
    session: AsyncSession, from_id: uuid.UUID, to_id: uuid.UUID
) -> ProcessEdge | None:
    return (
        await session.execute(
            select(ProcessEdge).where(
                ProcessEdge.from_process_id == from_id, ProcessEdge.to_process_id == to_id
            )
        )
    ).scalar_one_or_none()


async def get_org_role(session: AsyncSession, role_id: uuid.UUID) -> OrgRole | None:
    return await session.get(OrgRole, role_id)


async def get_supplier(session: AsyncSession, supplier_id: uuid.UUID) -> Supplier | None:
    return await session.get(Supplier, supplier_id)


async def get_process_link(
    session: AsyncSession, process_id: uuid.UUID, doc_id: uuid.UUID
) -> ProcessLink | None:
    return (
        await session.execute(
            select(ProcessLink).where(
                ProcessLink.process_id == process_id,
                ProcessLink.documented_information_id == doc_id,
            )
        )
    ).scalar_one_or_none()


async def list_process_links(
    session: AsyncSession, doc_id: uuid.UUID
) -> list[tuple[ProcessLink, Process]]:
    """A document's process links joined to the process detail (for the per-document read)."""
    rows = (
        await session.execute(
            select(ProcessLink, Process)
            .join(Process, ProcessLink.process_id == Process.id)
            .where(ProcessLink.documented_information_id == doc_id)
            .order_by(asc(Process.name))
        )
    ).all()
    return [(link, proc) for link, proc in rows]


async def process_ids_for_docs(
    session: AsyncSession, doc_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, frozenset[str]]:
    """Map each ``documented_information`` id → the frozenset of its process ids (as strings).
    Ids with no process are absent (callers default to ``frozenset()``). The single shared
    loader behind every document-scope ``ResourceContext.process_ids`` — so the
    S-owner-assignment-1 R28 enrichment (a bound Process Owner's PROCESS grant authorizing
    across the document gates) stays consistent everywhere a doc's authz scope resolves
    (detail/list/search/workflow-read).

    A doc's process scope = its ``ProcessLink`` rows UNION any satellite-bound process. A
    quality objective (6.2) stores its bound process on ``quality_objective.process_id`` —
    each objective IS its own ``documented_information`` (``QualityObjective.id == doc.id``),
    NOT a ``ProcessLink`` (only ``vault/service.py`` creates those). Without this union a
    PROCESS-scoped ``document.read``/``approve``/``release`` DENY on the objective's bound
    process is dropped once #333 populates the FRAMEWORK selector: the framework ALLOW then
    matches while the PROCESS DENY can't (deny-always-wins violated, #346). Risk/opportunity
    is a register-HEAD model — its satellite ``process_id`` sits on rows keyed by their own
    id, never a document id — so no union is needed there."""
    if not doc_ids:
        return {}
    grouped: dict[uuid.UUID, set[str]] = {}
    for di_id, p_id in (
        await session.execute(
            select(ProcessLink.documented_information_id, ProcessLink.process_id).where(
                ProcessLink.documented_information_id.in_(doc_ids)
            )
        )
    ).all():
        grouped.setdefault(di_id, set()).add(str(p_id))
    # Objective satellite: QualityObjective.id == the OBJ document's id; its process_id (absent from
    # ProcessLink) is the objective's bound process — union it so a PROCESS-scoped DENY still fires.
    for oid, p_id in (
        await session.execute(
            select(QualityObjective.id, QualityObjective.process_id).where(
                QualityObjective.id.in_(doc_ids),
                QualityObjective.process_id.is_not(None),
            )
        )
    ).all():
        grouped.setdefault(oid, set()).add(str(p_id))
    return {k: frozenset(v) for k, v in grouped.items()}


async def process_ids_for_doc(session: AsyncSession, doc_id: uuid.UUID) -> frozenset[str]:
    """The single document's linked process ids (str) — the per-doc convenience over
    ``process_ids_for_docs`` (empty when the doc has no links)."""
    return (await process_ids_for_docs(session, [doc_id])).get(doc_id, frozenset())
