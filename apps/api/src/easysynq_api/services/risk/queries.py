"""Risk & Opportunity register read queries (S-risk-1b, clause 6.1). ``governing_register`` returns
the GOVERNING frozen register snapshot — the head's current Effective version's
``metadata_snapshot.risk_register`` (rows + per-method criteria), or ``None`` pre-first-release. The
serializer grades each live satellite row's BAND against ``resolve_criteria(governing, method)`` so
a code-level band-threshold edit can never re-grade the live register (the R49 L2 freeze; the
``objectives/queries`` governing-commitment precedent). During UnderRevision the Effective version
keeps governing, so the live band resolves against the PRIOR Effective criteria — the working edits
show their re-derived risk_rating but are graded on the governing basis."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation


async def governing_register(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any] | None:
    """The org RSK head's current Effective version's frozen ``risk_register`` snapshot (rows +
    criteria), or ``None`` pre-first-release. The join on ``current_effective_version_id`` excludes
    an Obsolete head (its pointer is NULL) and resolves the PRIOR Effective version while the head
    is UnderRevision (the pointer only moves at the next cutover — R43)."""
    snapshot = (
        await session.execute(
            select(DocumentVersion.metadata_snapshot["risk_register"])
            .join(
                DocumentedInformation,
                DocumentVersion.id == DocumentedInformation.current_effective_version_id,
            )
            .join(DocumentType, DocumentedInformation.document_type_id == DocumentType.id)
            .where(
                DocumentedInformation.org_id == org_id,
                DocumentType.code == "RSK",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if isinstance(snapshot, dict):
        return snapshot
    return None
