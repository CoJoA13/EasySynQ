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


def resource_from_doc(
    doc: DocumentedInformation,
    *,
    document_type: DocumentType | None,
    process_ids: frozenset[str],
) -> ResourceContext:
    """Build a document's FULL authz scope tuple from an already-loaded row — the single
    completion source shared by every document-scope builder (issue #333).

    Every selector ``domain/authz/pdp.py::_matches_scope`` compares for a document MUST be set here,
    or a matching DENY at that scope is silently dropped (deny-always-wins violated — the bug this
    centralizes away):
      * ARTIFACT   → ``artifact_id``
      * FOLDER     → ``folder_path``
      * PROCESS    → ``process_ids``
      * FRAMEWORK  → ``framework_id``
      * DOC_CLASS  → ``document_level`` + ``kind`` (+ ``concrete_type``)
    plus ``lifecycle_state`` (the ABAC lifecycle predicate). ``framework_id``/``kind`` are direct
    NOT-NULL columns; ``process_ids`` and the resolved ``DocumentType`` are supplied by callers
    because they need the session. Deriving both ``document_level`` and ``concrete_type`` from that
    one catalog row prevents the pair from drifting while this helper remains pure and
    session-free for async and batched builders alike (R60/#345).
    """
    document_level = document_type.document_level.value if document_type is not None else None
    concrete_type = document_type.code if document_type is not None else None
    return ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=document_level,
        kind=doc.kind.value,
        concrete_type=concrete_type,
        process_ids=process_ids,
        lifecycle_state=doc.current_state.value,
        framework_id=str(doc.framework_id),
    )


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
    document_type: DocumentType | None = None
    if doc.document_type_id:
        document_type = await session.get(DocumentType, doc.document_type_id)
    return resource_from_doc(
        doc,
        document_type=document_type,
        process_ids=await vault_repo.process_ids_for_doc(session, doc.id),
    )
