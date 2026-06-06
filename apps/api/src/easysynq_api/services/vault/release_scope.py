"""The SoD-2 release-scope enrichment — shared by the direct ``document.release`` endpoint and the
DCR-as-orchestrator implement path (slice S-dcr-5).

SoD-2 (the author may not release their own edit; the sole approver may not release unless the org
sets ``allow_approver_release``) is an ABAC overlay the PDP evaluates ONLY for the
``document.release`` permission key. Both the direct ``POST /documents/{id}/release`` endpoint and a
REVISE/CREATE DCR ``POST /dcrs/{id}/implement`` (which drives the same cutover) must therefore
``enforce("document.release", scope, sig_hook=True)`` over the SAME version the cutover promotes,
so the overlay fires identically on both paths — no DCR side-door past document control (decisions
register R40 S-dcr-5 addendum). This helper resolves that version's immutable author + approval
signers and folds them onto an already-built base document scope, so the two call sites share ONE
implementation rather than two drifting copies.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._signature_enums import SignatureMeaning
from ...db.models._vault_enums import VersionState
from ...db.models.document_version import DocumentVersion
from ...db.models.signature_event import SignatureEvent as SignatureEventRow
from ...domain.authz import ResourceContext


async def enrich_release_sod_scope(
    session: AsyncSession,
    base: ResourceContext,
    doc_id: uuid.UUID,
    version_id: uuid.UUID | None,
) -> ResourceContext:
    """Fold the SoD-2 inputs for the version the cutover will promote (``version_id`` if supplied,
    else the latest Approved) onto ``base``: its immutable ``author_user_id`` + the approver set
    from its recorded approval signatures. Degrades to ``base`` unchanged when there is no Approved
    version (the FSM 409 then fires at the cutover). The author-side block is signature-independent
    (the robust backstop); the approver-side is only as strong as in-band approval-signature
    emission (the ``decide()`` path always emits it) — exactly the direct-endpoint semantics."""
    version: DocumentVersion | None
    if version_id is not None:
        version = await session.get(DocumentVersion, version_id)
        if version is None or version.document_id != doc_id:
            return base
    else:
        version = (
            await session.execute(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == doc_id,
                    DocumentVersion.version_state == VersionState.Approved,
                )
                .order_by(DocumentVersion.version_seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if version is None:
        return base
    signers = (
        (
            await session.execute(
                select(SignatureEventRow.signer_user_id).where(
                    SignatureEventRow.signed_object_id == version.id,
                    SignatureEventRow.meaning == SignatureMeaning.approval,
                )
            )
        )
        .scalars()
        .all()
    )
    return dataclasses.replace(
        base,
        version_id=str(version.id),
        author_user_id=str(version.author_user_id),
        approver_user_ids=frozenset(str(s) for s in signers if s is not None),
    )
