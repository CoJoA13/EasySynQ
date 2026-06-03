"""search: a Postgres full-text GIN index over documented_information (slice S10, doc 13 §1.3)

Adds the metadata-plane full-text search backend the ``Indexer`` interface queries (R34 — OpenSearch
is omitted in the MVP, Postgres-FTS serves; the ``Indexer`` keeps OpenSearch a clean v1 drop-in). The
index is a **functional / expression GIN index** over a frozen ``to_tsvector('english', …)`` document
built from the identifier + title + legacy_identifier + area_code, created with ``op.execute`` rather
than a generated column on purpose:

  * No new column → no model change → no ``Computed`` server-default comparison for ``alembic check``
    to drift on (the load-bearing migration gate stays clean — Alembic *skips* reflection of
    expression-based indexes, so it never emits a spurious drop for one created here).
  * The exact same ``to_tsvector('english', …)`` expression lives once as ``_FTS_DOC_EXPR`` in
    ``services/search/indexer.py`` so the search query's ``@@`` filter matches this index verbatim and
    the planner uses it (a comment in both cross-references the other; a drift is a perf regression,
    never a correctness bug — the query works index-less).

Content-plane (extracted body text) FTS is deferred — it needs the extracted-text rendition pipeline
(doc 13 §2.2); this slice indexes the metadata plane only. Reversible: downgrade drops the index.

Revision ID: 0020_search_fts
Revises: 0019_process_ia
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020_search_fts"
down_revision: str | None = "0019_process_ia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_documented_information_search_tsv"

# The frozen FTS document expression — MUST stay byte-identical to ``_FTS_DOC_EXPR`` in
# apps/api/.../services/search/indexer.py so the search ``@@`` filter uses this GIN index. Unweighted:
# the ``@@`` match ignores weights (those only affect ts_rank, computed on the matching rows).
_FTS_DOC_EXPR = (
    "to_tsvector('english', "
    "coalesce(identifier, '') || ' ' || coalesce(title, '') || ' ' "
    "|| coalesce(legacy_identifier, '') || ' ' || coalesce(area_code, ''))"
)


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX {_INDEX_NAME} ON documented_information USING gin ({_FTS_DOC_EXPR})"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
