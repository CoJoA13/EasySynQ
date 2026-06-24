"""Document → ResourceContext builder (extracted from api/documents for reuse).

The audience resolver (services/authz/audience.py) and the api document gate both need a document's
authz scope. This is the single builder; api/documents._document_scope_by_id is a thin delegate so
authority still flows api→services (services never imports api).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...domain.authz import ResourceContext
from ..vault import repository as vault_repo


async def build_document_resource_context(
    session: AsyncSession, doc_id: uuid.UUID
) -> ResourceContext:
    """Resolve a document's authz scope (ARTIFACT + folder + doc-class + process_ids + lifecycle).

    Returns a degraded ResourceContext(artifact_id=str(doc_id)) when the doc is missing — the api
    gate relies on this fallback, so it MUST be preserved byte-identically.
    """
    doc = await session.get(DocumentedInformation, doc_id)
    if doc is None:
        return ResourceContext(artifact_id=str(doc_id))
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    return ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        lifecycle_state=doc.current_state.value,
        process_ids=await vault_repo.process_ids_for_doc(session, doc.id),
    )
