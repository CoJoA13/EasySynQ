"""Unified search surface (slice S10, doc 13 §2, doc 15 §8.14).

Postgres-FTS over the document metadata plane behind the ``Indexer`` seam (R34 — OpenSearch is a v1
drop-in). The indexer returns *candidate* hits **over Effective documents only** (doc 13's
"Effective only" default — non-Effective states need the distinct read_draft/read_obsolete keys, a
v1 facet); this tier **re-validates ``document.read`` per hit** against PostgreSQL (deny-by-default)
so a stale/over-broad index can never over-disclose (doc 13 §2.7). Search is a **list surface: it
filters, never 403s** (doc 18 §5.2) — a caller who may read nothing gets ``200`` with empty results
and ``hidden_by_scope`` counting what their access scope hid ("N hidden by your access scope").
Records/other types are not built → documents only.
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models.app_user import AppUser
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..services.authz import gather_grants
from ..services.search import SearchHit, get_indexer
from ..services.vault import repository as vault_repo

router = APIRouter(prefix="/api/v1", tags=["search"])

_CANDIDATE_CAP = (
    200  # how many FTS candidates to score before the post-authz cut (a pre-filter cap)
)


async def _readable_hits(
    session: AsyncSession, caller: AppUser, hits: list[SearchHit]
) -> tuple[list[SearchHit], int]:
    """Drop hits the caller may not ``document.read`` (the GET /documents row-filter pattern).
    Returns (visible, hidden_count)."""
    grants = await gather_grants(session, caller.id, caller.org_id, "document.read")
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC))
    visible: list[SearchHit] = []
    for h in hits:
        resource = ResourceContext(
            artifact_id=str(h.doc_id),
            folder_path=h.folder_path,
            document_level=h.document_level,
        )
        if authorize(grants, "document.read", resource, ctx).allow:
            visible.append(h)
    return visible, len(hits) - len(visible)


@router.get("/search")
async def search_endpoint(
    q: str = Query(..., min_length=1, description="free-text query (metadata plane)"),
    limit: int = Query(25, ge=1, le=100),
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    candidates = await get_indexer().search(session, caller.org_id, q, limit=_CANDIDATE_CAP)
    visible, hidden = await _readable_hits(session, caller, candidates)
    visible = visible[:limit]
    refs = await vault_repo.clause_numbers_for_docs(session, [h.doc_id for h in visible])
    return {
        "query": q,
        "results": [
            {
                "type": "document",
                "id": str(h.doc_id),
                "identifier": h.identifier,
                "title": h.title,
                "current_state": h.current_state,
                "clause_refs": refs.get(h.doc_id, []),
                "snippet": h.snippet,
                "rank": h.rank,
            }
            for h in visible
        ],
        "hidden_by_scope": hidden,
    }


@router.get("/search/suggest")
async def suggest_endpoint(
    q: str = Query(..., min_length=1, description="prefix for type-ahead (identifier/title)"),
    limit: int = Query(10, ge=1, le=25),
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # Over-fetch suggestions then post-filter by document.read (filter-not-403), keeping ``limit``.
    raw = await get_indexer().suggest(session, caller.org_id, q, limit=_CANDIDATE_CAP)
    grants = await gather_grants(session, caller.id, caller.org_id, "document.read")
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC))
    out: list[dict[str, str]] = []
    for s in raw:
        resource = ResourceContext(
            artifact_id=str(s.doc_id), folder_path=s.folder_path, document_level=s.document_level
        )
        if authorize(grants, "document.read", resource, ctx).allow:
            out.append({"id": str(s.doc_id), "identifier": s.identifier, "title": s.title})
        if len(out) >= limit:
            break
    return {"suggestions": out}
