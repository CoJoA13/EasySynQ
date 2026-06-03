"""Vault DB access: atomic identifier-sequence allocation + the document/version lookups.

``allocate_seq`` is a single ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` so concurrent
``POST /documents`` for the same (type, area) never collide on ``{SEQ}``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, asc, desc, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.blob import Blob
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.framework import Framework
from ...db.models.numbering_counter import NumberingCounter
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
