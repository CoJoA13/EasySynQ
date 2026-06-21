"""Interested Parties register read queries (S-interested-parties-2, clause 4.2).

``governing_register`` returns the GOVERNING frozen register snapshot —
``metadata_snapshot.interested_party_register`` (rows) of the head's current Effective version, or
``None`` pre-first-release. The CONTROLLED read-of-record for downstream consumers (the MR 9.3.2(b)
input's 4.2 half + the ``GET /interested-parties/summary`` seam). During UnderRevision the Effective
version keeps governing, so the read resolves the PRIOR snapshot (the pointer moves at the next
cutover — R43; the ``context/queries.governing_register`` precedent)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation


async def governing_register(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any] | None:
    """The org IPR head's current Effective version's frozen ``interested_party_register`` snapshot
    (rows), or ``None`` pre-first-release. The join on ``current_effective_version_id`` excludes an
    Obsolete head (its pointer is NULL) and resolves the PRIOR Effective version while the head is
    UnderRevision (the pointer only moves at the next cutover — R43)."""
    snapshot = (
        await session.execute(
            select(DocumentVersion.metadata_snapshot["interested_party_register"])
            .join(
                DocumentedInformation,
                DocumentVersion.id == DocumentedInformation.current_effective_version_id,
            )
            .join(DocumentType, DocumentedInformation.document_type_id == DocumentType.id)
            .where(
                DocumentedInformation.org_id == org_id,
                DocumentType.code == "IPR",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if isinstance(snapshot, dict):
        return snapshot
    return None
