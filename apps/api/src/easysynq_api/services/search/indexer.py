"""The ``Indexer`` seam (slice S10, doc 13 §1.3, doc 18 §13).

Search is **Postgres-FTS in the MVP** (R34 — OpenSearch is omitted from the S/M Compose profiles).
This module defines an engine-agnostic ``Indexer`` Protocol and a ``PostgresFtsIndexer`` over the
``documented_information`` metadata plane, so an ``OpenSearchIndexer`` is a clean **v1 drop-in**:
``get_indexer()`` is the single seam the api tier calls. Per doc 13 §1.3/§2.7 the index is *derived*
and never authoritative — it returns **candidate hits**; the api re-validates every hit's
``document.read`` permission against PostgreSQL at hydration so a stale/over-broad index can never
over-disclose (the caller filters in ``api/search.py``, never here).

Scope of the MVP index: the **metadata plane** only — identifier, title, legacy_identifier,
area_code (doc 13 §2.1 content plane / extracted body text is deferred; it needs the extracted-text
rendition pipeline, doc 13 §2.2). ``ts_rank`` weights identifier > title > legacy/area.

**Effective documents only** (doc 13 §1/§2.2: "Effective only" is the general searcher's default;
Draft/InReview/Superseded/Obsolete artifacts require the distinct ``document.read_draft`` /
``document.read_obsolete`` keys, which this metadata search does not consult). Restricting the
candidate set to ``current_state = 'Effective'`` keeps search from leaking non-Effective titles/
snippets to a plain ``document.read`` holder; surfacing other states by facet is a v1 refinement.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# A static SQL literal (no interpolation → no SQL-injection surface): the only user input is the
# bound ``:fts_q`` parameter, and ``websearch_to_tsquery`` never errors on arbitrary text. The
# ``to_tsvector('english', …)`` document expression in the WHERE clause MUST stay semantically
# identical to the functional GIN index in migrations/versions/0020_search_fts.py so the planner can
# use ``ix_documented_information_search_tsv`` (whitespace doesn't matter — PG matches the parsed
# tree). Ranking uses a weighted vector (identifier 'A' > title 'B' > legacy/area 'C', doc 13 §2.1)
# computed on the matching rows. Ambiguous columns are qualified; the FTS columns stay bare.
_SEARCH_SQL = sa.text(
    """
    SELECT documented_information.id AS id,
           identifier AS identifier,
           title AS title,
           documented_information.current_state AS current_state,
           documented_information.folder_path AS folder_path,
           documented_information.document_type_id AS document_type_id,
           dt.document_level AS document_level,
           ts_rank(
               setweight(to_tsvector('english', coalesce(identifier, '')), 'A')
               || setweight(to_tsvector('english', coalesce(title, '')), 'B')
               || setweight(to_tsvector('english',
                      coalesce(legacy_identifier, '') || ' ' || coalesce(area_code, '')), 'C'),
               websearch_to_tsquery('english', :fts_q)) AS rank,
           ts_headline('english',
                       coalesce(title, '') || ' ' || coalesce(identifier, ''),
                       websearch_to_tsquery('english', :fts_q)) AS snippet
    FROM documented_information
    LEFT JOIN document_type dt ON dt.id = documented_information.document_type_id
    WHERE documented_information.org_id = :org_id
      AND documented_information.current_state = 'Effective'::document_current_state
      AND to_tsvector('english',
              coalesce(identifier, '') || ' ' || coalesce(title, '') || ' '
              || coalesce(legacy_identifier, '') || ' ' || coalesce(area_code, ''))
          @@ websearch_to_tsquery('english', :fts_q)
    ORDER BY rank DESC, documented_information.id DESC
    LIMIT :limit
    """
)

# Type-ahead suggest = a simple case-insensitive prefix over identifier/title (doc 13 §2.1). FTS
# prefix (``to_tsquery(... :*)``) is a v1 refinement — ILIKE is robust and has no tsquery-syntax
# pitfalls on partial input. ``:prefix`` is bound (injection-safe). Carries the same
# folder_path/document_level as a SearchHit so the api's document.read post-filter is identical.
_SUGGEST_SQL = sa.text(
    """
    SELECT documented_information.id AS id,
           identifier AS identifier,
           title AS title,
           documented_information.folder_path AS folder_path,
           dt.document_level AS document_level
    FROM documented_information
    LEFT JOIN document_type dt ON dt.id = documented_information.document_type_id
    WHERE documented_information.org_id = :org_id
      AND documented_information.current_state = 'Effective'::document_current_state
      AND (identifier ILIKE :prefix OR title ILIKE :prefix)
    ORDER BY length(identifier), identifier
    LIMIT :limit
    """
)


@dataclasses.dataclass(frozen=True, slots=True)
class SearchHit:
    """One candidate document hit. ``folder_path``/``document_level`` carry the ABAC inputs the api
    needs to re-check ``document.read`` without an extra round-trip (an OpenSearch impl would
    denormalize the same fields)."""

    doc_id: uuid.UUID
    identifier: str
    title: str
    current_state: str
    folder_path: str | None
    document_level: str | None
    rank: float
    snippet: str


@dataclasses.dataclass(frozen=True, slots=True)
class Suggestion:
    doc_id: uuid.UUID
    identifier: str
    title: str
    folder_path: str | None
    document_level: str | None


class Indexer(Protocol):
    """Engine-agnostic search seam. The PG impl is the MVP; OpenSearch is the v1 drop-in (R34).
    Both return *candidate* hits — the api re-validates ``document.read`` per hit (doc 13 §2.7)."""

    async def search(
        self, session: AsyncSession, org_id: uuid.UUID, query: str, *, limit: int
    ) -> list[SearchHit]: ...

    async def suggest(
        self, session: AsyncSession, org_id: uuid.UUID, prefix: str, *, limit: int
    ) -> list[Suggestion]: ...


class PostgresFtsIndexer:
    """Postgres-FTS over the ``documented_information`` metadata plane (R34 degraded mode)."""

    async def search(
        self, session: AsyncSession, org_id: uuid.UUID, query: str, *, limit: int
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        rows = (
            await session.execute(_SEARCH_SQL, {"fts_q": query, "org_id": org_id, "limit": limit})
        ).mappings()
        return [
            SearchHit(
                doc_id=r["id"],
                identifier=r["identifier"],
                title=r["title"],
                current_state=r["current_state"],
                folder_path=r["folder_path"],
                document_level=r["document_level"],
                rank=float(r["rank"]),
                snippet=r["snippet"],
            )
            for r in rows
        ]

    async def suggest(
        self, session: AsyncSession, org_id: uuid.UUID, prefix: str, *, limit: int
    ) -> list[Suggestion]:
        term = prefix.strip()
        if not term:
            return []
        # Escape ILIKE wildcards in the user term so "%"/"_" can't broaden the prefix match.
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = (
            await session.execute(
                _SUGGEST_SQL, {"org_id": org_id, "prefix": f"{escaped}%", "limit": limit}
            )
        ).mappings()
        return [
            Suggestion(
                doc_id=r["id"],
                identifier=r["identifier"],
                title=r["title"],
                folder_path=r["folder_path"],
                document_level=r["document_level"],
            )
            for r in rows
        ]


_INDEXER: Indexer = PostgresFtsIndexer()


def get_indexer() -> Indexer:
    """The single seam the api tier calls. Returns the Postgres-FTS indexer in the MVP; an
    ``OpenSearchIndexer`` is the v1 drop-in (R34 — swap here, no api change)."""
    return _INDEXER
