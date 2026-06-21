"""Context register read queries (S-context-1, clause 4.1). ``governing_register`` returns the
GOVERNING frozen register snapshot — the head's current Effective version's
``metadata_snapshot.context_register`` (rows), or ``None`` pre-first-release — the CONTROLLED
read-of-record for downstream consumers (the future MR 9.3.2(b) context-change input + the
``GET /context/summary`` seam, S-context-2). During UnderRevision the Effective version keeps
governing, so the read-of-record resolves the PRIOR Effective snapshot (the pointer only moves at
the
next cutover — R43; the ``risk/queries.governing_register`` precedent)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation


async def governing_register(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any] | None:
    """The org CTX head's current Effective version's frozen ``context_register`` snapshot (rows),
    or ``None`` pre-first-release. The join on ``current_effective_version_id`` excludes an Obsolete
    head (its pointer is NULL) and resolves the PRIOR Effective version while the head is
    UnderRevision (the pointer only moves at the next cutover — R43)."""
    snapshot = (
        await session.execute(
            select(DocumentVersion.metadata_snapshot["context_register"])
            .join(
                DocumentedInformation,
                DocumentVersion.id == DocumentedInformation.current_effective_version_id,
            )
            .join(DocumentType, DocumentedInformation.document_type_id == DocumentType.id)
            .where(
                DocumentedInformation.org_id == org_id,
                DocumentType.code == "CTX",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if isinstance(snapshot, dict):
        return snapshot
    return None
