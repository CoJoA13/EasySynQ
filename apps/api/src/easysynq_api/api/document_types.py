"""The document-type catalog — read-only reference data (slice S-web-2, doc 14 §6).

``GET /document-types`` returns the org's seeded ``document_type`` rows so a client can render a
friendly **Type** column / **Type** facet for the document library (the list/detail endpoints carry
only ``document_type_id`` as a bare UUID). Reference data, INSERT-by-seed only — no write surface.

Gating mirrors ``GET /documents``: **authentication only** (any org member), NOT a ``require(...)``
PEP. ``document.read`` is a CONTENT key resolved per-resource at ARTIFACT/FOLDER/DOC_CLASS scope, so
``require("document.read")`` at the default SYSTEM scope would lock out an ordinary reader whose
grant is narrower. The type catalog is innocuous org reference data needed to render whatever rows
the authenticated, row-filtered ``GET /documents`` returns, so authentication is the boundary.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models.app_user import AppUser
from ..db.models.document_type import DocumentType
from ..db.session import get_session

router = APIRouter(prefix="/api/v1", tags=["document-types"])


def _document_type(dt: DocumentType) -> dict[str, Any]:
    return {
        "id": str(dt.id),
        "code": dt.code,
        "name": dt.name,
        "document_level": dt.document_level.value,
        "is_singleton": dt.is_singleton,
    }


@router.get("/document-types")
async def list_document_types(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The org's document-type catalog, ordered by name."""
    types = (
        (
            await session.execute(
                select(DocumentType)
                .where(DocumentType.org_id == caller.org_id)
                .order_by(DocumentType.name)
            )
        )
        .scalars()
        .all()
    )
    return [_document_type(dt) for dt in types]
