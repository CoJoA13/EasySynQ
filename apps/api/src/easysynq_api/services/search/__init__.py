"""Search services (slice S10): the engine-agnostic ``Indexer`` seam (Postgres-FTS now; OpenSearch
the v1 drop-in per R34)."""

from __future__ import annotations

from .indexer import Indexer, PostgresFtsIndexer, SearchHit, Suggestion, get_indexer

__all__ = ["Indexer", "PostgresFtsIndexer", "SearchHit", "Suggestion", "get_indexer"]
