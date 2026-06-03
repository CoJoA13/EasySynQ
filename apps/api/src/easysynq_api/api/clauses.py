"""The ISO clause spine — read-only reference data (slice S9, doc 15 §8.4).

``GET /clauses`` returns the seeded clause catalog (4 → 4.4 → 4.4.1) as a flat list ordered by
clause number, each row carrying its ``parent_id`` so the client rebuilds the tree. Gated on
``clauseMap.read`` (the seeded catalog key; doc 15 §8.4's shorthand ``clause.read`` is not in the
closed 96-key catalog, so we wire the real key per CLAUDE.md §1 "doc-07 keys verbatim"). There is no
write surface: clauses are INSERT-by-seed only (no ``clause.edit`` key, doc 07 §3.6).

The optional ``framework`` query param is the framework *code* (doc 15 writes ``framework_id=
iso9001:2015`` but the value is the code, not a UUID); it defaults to the org's ``iso9001:2015``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.app_user import AppUser
from ..db.models.clause import Clause
from ..db.session import get_session
from ..problems import ProblemException
from ..services.authz import require
from ..services.vault import repository as vault_repo

router = APIRouter(prefix="/api/v1", tags=["clauses"])

# clauseMap.read is SYSTEM-scoped (the whole catalog) → the default SYSTEM scope, no resolver.
_clauses_read = require("clauseMap.read")


def _clause(c: Clause) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "framework_id": str(c.framework_id),
        "number": c.number,
        "parent_id": str(c.parent_id) if c.parent_id else None,
        "title": c.title,
        "intent_text": c.intent_text,
        "is_mandatory_star": c.is_mandatory_star,
        "pdca_phase": c.pdca_phase.value,
        "requirement_node": c.requirement_node,
    }


@router.get("/clauses")
async def list_clauses_endpoint(
    framework: str | None = None,
    caller: AppUser = Depends(_clauses_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    fw = await vault_repo.get_framework(session, caller.org_id, code=framework or "iso9001:2015")
    if fw is None:
        raise ProblemException(status=404, code="not_found", title="Framework not found")
    clauses = await vault_repo.list_clauses(session, fw.id)
    return [_clause(c) for c in clauses]
