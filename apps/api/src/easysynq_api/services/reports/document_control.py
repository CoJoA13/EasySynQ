# apps/api/src/easysynq_api/services/reports/document_control.py
"""The Controlled Document Register report (ISO 9001 §7.5.3 master list; doc 13 §6.1, doc 15 §8.15).

``GET /reports/document-control`` (api/reports.py) returns the org's master list of controlled
Documents — permission-filtered by ``document.read`` (the ``list_documents`` row-filter), with an
audit-defensible provenance header + a content hash over the full as-of set. Read-only: NO
audit_event, NO WORM write, NO migration. The pure helpers (hash + provenance) are DB-free and
unit-tested; ``compute_document_control_register`` does the query + authz filter + batched
enrichment.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ...db.models._signature_enums import SignatureMeaning, SignedObjectType
from ...db.models._vault_enums import DocumentKind
from ...db.models.app_user import AppUser
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.signature_event import SignatureEvent
from ...domain.authz import RequestContext, ResourceContext, authorize
from ..authz import gather_grants
from ..vault import repository as vault_repo
from ..vault.review import review_state, today_org

_REPORT_NAME = "Controlled Document Register"


def register_content_hash(rows: list[dict[str, Any]]) -> str:
    """A deterministic sha256 over the register's ROW DATA (not the provenance block, whose
    wall-clock ``generated_at`` would make every hash unique). Rows are sorted by ``identifier`` and
    canonically serialized so the hash is independent of DB return order and reproducible given the
    same filtered set + as-of. Filter-sensitive: a different row set → a different hash."""
    ordered = sorted(rows, key=lambda r: str(r.get("identifier") or ""))
    canonical = json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_provenance(
    *,
    generated_by: str,
    generated_at: datetime.datetime,
    scope: str,
    app_version: str,
    filters: dict[str, str],
    row_count: int,
    content_hash: str,
) -> dict[str, Any]:
    """The audit-defensibility header block (doc 13 §6). ``as_of`` mirrors ``generated_at`` (the
    instant the register was materialized). ``filters`` echoes the applied ``filter[...]`` params so
    the content hash is reproducible."""
    stamp = generated_at.isoformat()
    return {
        "report_name": _REPORT_NAME,
        "generated_by": generated_by,
        "generated_at": stamp,
        "as_of": stamp,
        "scope": scope,
        "app_version": app_version,
        "filters": filters,
        "row_count": row_count,
        "content_hash": content_hash,
    }


@dataclass(frozen=True)
class RegisterResult:
    rows: list[dict[str, Any]]
    content_hash: str
    row_count: int


def _display(user: AppUser | None) -> str | None:
    if user is None:
        return None
    return user.display_name or user.email or str(user.id)


async def compute_document_control_register(
    session: AsyncSession,
    caller: AppUser,
    *,
    filters: list[ColumnElement[bool]],
    source_ip: str | None,
) -> RegisterResult:
    """The permission-filtered master list. Scans ALL org DOCUMENT rows matching ``filters`` (no
    cap — the register is complete), row-filters by ``document.read`` (the ``list_documents`` loop),
    then batch-enriches the visible set. No N+1; no audit_event."""
    docs = (
        (
            await session.execute(
                select(DocumentedInformation)
                .where(
                    DocumentedInformation.org_id == caller.org_id,
                    DocumentedInformation.kind == DocumentKind.DOCUMENT,
                    *filters,
                )
                # deterministic candidate order; the final rows re-sort by identifier in the hash.
                .order_by(DocumentedInformation.identifier)
            )
        )
        .scalars()
        .all()
    )

    # document_level per doc-type (needed for the document.read ResourceContext) — the
    # list_documents ``levels`` map.
    type_ids = {d.document_type_id for d in docs if d.document_type_id}
    type_level: dict[uuid.UUID, str] = {}
    type_name: dict[uuid.UUID, str] = {}
    if type_ids:
        for dt in (
            (await session.execute(select(DocumentType).where(DocumentType.id.in_(type_ids))))
            .scalars()
            .all()
        ):
            type_level[dt.id] = dt.document_level.value
            type_name[dt.id] = dt.name

    process_ids_by_doc = await vault_repo.process_ids_for_docs(session, [d.id for d in docs])

    grants = await gather_grants(session, caller.id, caller.org_id, "document.read")
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC), source_ip=source_ip)
    visible: list[DocumentedInformation] = []
    for d in docs:
        resource = ResourceContext(
            artifact_id=str(d.id),
            folder_path=d.folder_path,
            document_level=type_level.get(d.document_type_id) if d.document_type_id else None,
            process_ids=process_ids_by_doc.get(d.id, frozenset()),
        )
        if authorize(grants, "document.read", resource, ctx).allow:
            visible.append(d)

    # --- batched enrichment over the visible set only ---
    eff_ids = [d.current_effective_version_id for d in visible if d.current_effective_version_id]
    versions: dict[uuid.UUID, DocumentVersion] = {}
    if eff_ids:
        for v in (
            (await session.execute(select(DocumentVersion).where(DocumentVersion.id.in_(eff_ids))))
            .scalars()
            .all()
        ):
            versions[v.id] = v

    # clause refs WITH the ★ mandatory flag (clause.is_mandatory_star) — the register's own loader
    # (vault_repo.clause_numbers_for_docs returns numbers only, no star).
    clause_by_doc: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if visible:
        for doc_id, number, starred in (
            await session.execute(
                select(
                    ClauseMapping.documented_information_id,
                    Clause.number,
                    Clause.is_mandatory_star,
                )
                .join(Clause, ClauseMapping.clause_id == Clause.id)
                .where(ClauseMapping.documented_information_id.in_([d.id for d in visible]))
                .order_by(Clause.number)
            )
        ).all():
            clause_by_doc.setdefault(doc_id, []).append(
                {"clause": number, "starred": bool(starred)}
            )

    # approval signature on the effective version → approver + date (latest wins; excludes a
    # voided signature). An imported-baseline version carries only import_baseline (not
    # approval), so it correctly reports approved_by/approved_on = None (doc 13 §6.1).
    approval_by_version: dict[uuid.UUID, SignatureEvent] = {}
    if eff_ids:
        for sig in (
            (
                await session.execute(
                    select(SignatureEvent)
                    .where(
                        SignatureEvent.signed_object_type == SignedObjectType.document_version,
                        SignatureEvent.signed_object_id.in_(eff_ids),
                        SignatureEvent.meaning == SignatureMeaning.approval,
                        SignatureEvent.voided_by.is_(None),
                    )
                    .order_by(SignatureEvent.created_at)
                )
            )
            .scalars()
            .all()
        ):
            approval_by_version[sig.signed_object_id] = sig  # last (latest) wins

    # display names for owners + signers.
    user_ids: set[uuid.UUID] = {d.owner_user_id for d in visible}
    user_ids |= {s.signer_user_id for s in approval_by_version.values() if s.signer_user_id}
    users: dict[uuid.UUID, AppUser] = {}
    if user_ids:
        for u in (
            (await session.execute(select(AppUser).where(AppUser.id.in_(user_ids)))).scalars().all()
        ):
            users[u.id] = u

    today = today_org()
    rows: list[dict[str, Any]] = []
    for d in visible:
        ev = (
            versions.get(d.current_effective_version_id) if d.current_effective_version_id else None
        )
        approval_sig = (
            approval_by_version.get(d.current_effective_version_id)
            if d.current_effective_version_id
            else None
        )
        rows.append(
            {
                "id": str(d.id),
                "identifier": d.identifier,
                "title": d.title,
                "document_type_id": str(d.document_type_id) if d.document_type_id else None,
                "document_type": type_name.get(d.document_type_id) if d.document_type_id else None,
                "current_state": d.current_state.value,
                "owner_user_id": str(d.owner_user_id),
                "owner_display": _display(users.get(d.owner_user_id)),
                "effective_revision_label": ev.revision_label if ev else None,
                "effective_from": ev.effective_from.isoformat()
                if ev and ev.effective_from
                else None,
                "blob_sha256": ev.source_blob_sha256 if ev else None,
                "clause_refs": clause_by_doc.get(d.id, []),
                "process_links": sorted(process_ids_by_doc.get(d.id, frozenset())),
                "approved_by": _display(users.get(approval_sig.signer_user_id))
                if approval_sig and approval_sig.signer_user_id
                else None,
                "approved_on": approval_sig.created_at.isoformat() if approval_sig else None,
                "next_review_due": d.next_review_due.isoformat() if d.next_review_due else None,
                "review_state": review_state(d.next_review_due, today),
            }
        )

    content_hash = register_content_hash(rows)
    return RegisterResult(rows=rows, content_hash=content_hash, row_count=len(rows))
