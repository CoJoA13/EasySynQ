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
    document_level: str | None,
    process_ids: frozenset[str],
    concrete_type: str | None = None,
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
    NOT-NULL columns; ``process_ids`` and ``document_level`` are resolved by the caller (they need
    the session) and passed in, so this stays a pure, session-free function both the async builder
    and the register's batched builder can call without drifting.

    ``concrete_type`` is a documented-but-unimplemented leaf-type selector — there is no column on
    ``documented_information``, no producer, and no grant selector references it in v1 — so it is
    threaded as an explicit ``None`` (today's behavior). When a source is defined it is populated in
    this one place; tracked in #345.
    """
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
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    return resource_from_doc(
        doc,
        document_level=level,
        process_ids=await vault_repo.process_ids_for_doc(session, doc.id),
    )
