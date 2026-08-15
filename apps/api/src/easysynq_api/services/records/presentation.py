"""Authorization-safe display hydration for already-readable record rows and evidence links.

The helpers in this module are projections, not route gates. They use the pure PDP directly so a
display probe cannot emit an authorization-audit row, and every related artifact is evaluated
under its own canonical permission and complete resource tuple.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models.app_user import AppUser
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.record import Record
from ...domain.authz import RequestContext, ResolvedGrant, ResourceContext, authorize
from ..authz import gather_grants
from ..authz.resource import resource_from_doc
from ..vault import repository as vault_repo
from . import repository as records_repo


@dataclasses.dataclass(frozen=True, slots=True)
class RecordLabels:
    captured_by_display_name: str | None
    source_document_identifier: str | None
    source_document_title: str | None
    source_document_readable: bool
    source_version_label: str | None
    retention_policy_name: str | None
    correction_of_readable: bool
    superseded_by_correction_readable: bool


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceTargetLabel:
    label: str | None
    readable: bool


type GrantSetCache = dict[str, Sequence[ResolvedGrant]]


async def _grants_for(
    session: AsyncSession,
    caller: AppUser,
    permission_key: str,
    grant_sets: GrantSetCache,
) -> Sequence[ResolvedGrant]:
    """Gather one permission family lazily within a single request's presentation work."""
    if permission_key not in grant_sets:
        grant_sets[permission_key] = await gather_grants(
            session, caller.id, caller.org_id, permission_key
        )
    return grant_sets[permission_key]


def _record_resource(
    record: Record, base: DocumentedInformation, process_ids: set[str]
) -> ResourceContext:
    """Build the complete record tuple used by both route and list authorization."""
    return ResourceContext(
        artifact_id=str(record.id),
        kind="RECORD",
        folder_path=base.folder_path,
        framework_id=str(base.framework_id),
        process_ids=frozenset(process_ids),
        lifecycle_state=base.current_state.value,
    )


def _headline(identifier: str, title: str) -> str:
    return f"{identifier} — {title}"


async def hydrate_record_labels(
    session: AsyncSession,
    caller: AppUser,
    rows: Sequence[tuple[Record, DocumentedInformation]],
    ctx: RequestContext,
    *,
    grant_sets: GrantSetCache | None = None,
) -> dict[uuid.UUID, RecordLabels]:
    """Hydrate labels for an already-authorized output page or detail row.

    Actor and retention names are tenant-constrained attributes of the readable record. Source
    documents and lineage records independently pass ``document.read`` / ``record.read`` before
    any related label or readability signal is returned.
    """
    if not rows:
        return {}
    if grant_sets is None:
        grant_sets = {}

    records = [record for record, _base in rows]
    actor_ids = {record.captured_by for record in records}
    source_ids = {
        record.source_document_id for record in records if record.source_document_id is not None
    }
    version_ids = {
        record.source_version_id for record in records if record.source_version_id is not None
    }
    policy_ids = {record.retention_policy_id for record in records}
    lineage_ids = {
        related_id
        for record in records
        for related_id in (record.correction_of, record.superseded_by_correction)
        if related_id is not None
    }

    actors = await records_repo.load_label_actors(session, caller.org_id, actor_ids)
    documents = await records_repo.load_label_documents(session, caller.org_id, source_ids)
    versions = await records_repo.load_label_versions(session, caller.org_id, version_ids)
    policies = await records_repo.load_label_policies(session, caller.org_id, policy_ids)
    lineage = await records_repo.load_label_records(session, caller.org_id, lineage_ids)

    readable_documents: set[uuid.UUID] = set()
    if source_ids:
        document_grants = await _grants_for(session, caller, "document.read", grant_sets)
        process_ids_by_doc = await vault_repo.process_ids_for_docs(session, list(documents))
        for document_id, (document, document_type) in documents.items():
            # A non-null type whose tenant-anchored join did not resolve is a malformed cross-org
            # pointer. Fail closed instead of building an incomplete tuple that could drop a
            # matching DOC_CLASS deny.
            if document.document_type_id is not None and document_type is None:
                continue
            resource = resource_from_doc(
                document,
                document_type=document_type,
                process_ids=process_ids_by_doc.get(document_id, frozenset()),
            )
            if authorize(document_grants, "document.read", resource, ctx).allow:
                readable_documents.add(document_id)

    readable_lineage: set[uuid.UUID] = set()
    if lineage_ids:
        record_grants = await _grants_for(session, caller, "record.read", grant_sets)
        process_ids_by_record = await records_repo.record_process_ids_effective_for(
            session, [record for record, _base in lineage.values()]
        )
        for record_id, (record, base) in lineage.items():
            resource = _record_resource(record, base, process_ids_by_record.get(record_id) or set())
            if authorize(record_grants, "record.read", resource, ctx).allow:
                readable_lineage.add(record_id)

    out: dict[uuid.UUID, RecordLabels] = {}
    for record, _base in rows:
        source_id = record.source_document_id
        source = documents.get(source_id) if source_id is not None else None
        source_readable = source_id in readable_documents if source_id is not None else False
        pinned_version = (
            versions.get(record.source_version_id) if record.source_version_id is not None else None
        )
        version_label = (
            pinned_version.revision_label
            if source_readable
            and pinned_version is not None
            and pinned_version.document_id == source_id
            else None
        )
        actor = actors.get(record.captured_by)
        policy = policies.get(record.retention_policy_id)
        out[record.id] = RecordLabels(
            captured_by_display_name=actor.display_name if actor is not None else None,
            source_document_identifier=(
                source[0].identifier if source_readable and source is not None else None
            ),
            source_document_title=(
                source[0].title if source_readable and source is not None else None
            ),
            source_document_readable=source_readable,
            source_version_label=version_label,
            retention_policy_name=policy.name if policy is not None else None,
            correction_of_readable=(
                record.correction_of in readable_lineage
                if record.correction_of is not None
                else False
            ),
            superseded_by_correction_readable=(
                record.superseded_by_correction in readable_lineage
                if record.superseded_by_correction is not None
                else False
            ),
        )
    return out


async def hydrate_evidence_target_labels(
    session: AsyncSession,
    caller: AppUser,
    links: Sequence[EvidenceForLink],
    ctx: RequestContext,
    *,
    grant_sets: GrantSetCache | None = None,
) -> dict[uuid.UUID, EvidenceTargetLabel]:
    """Hydrate evidence targets only after each target independently passes its read permission."""
    out = {link.id: EvidenceTargetLabel(label=None, readable=False) for link in links}
    if not links:
        return out
    if grant_sets is None:
        grant_sets = {}

    ids_by_type: dict[EvidenceForTargetType, set[uuid.UUID]] = {
        target_type: set() for target_type in EvidenceForTargetType
    }
    for link in links:
        ids_by_type[link.target_type].add(link.target_id)

    document_ids = ids_by_type[EvidenceForTargetType.DOCUMENT]
    if document_ids:
        documents = await records_repo.load_label_documents(session, caller.org_id, document_ids)
        grants = await _grants_for(session, caller, "document.read", grant_sets)
        process_ids_by_doc = await vault_repo.process_ids_for_docs(session, list(documents))
        readable: dict[uuid.UUID, str] = {}
        for document_id, (document, document_type) in documents.items():
            if document.document_type_id is not None and document_type is None:
                continue
            resource = resource_from_doc(
                document,
                document_type=document_type,
                process_ids=process_ids_by_doc.get(document_id, frozenset()),
            )
            if authorize(grants, "document.read", resource, ctx).allow:
                readable[document_id] = _headline(document.identifier, document.title)
        for link in links:
            if link.target_type is EvidenceForTargetType.DOCUMENT and link.target_id in readable:
                out[link.id] = EvidenceTargetLabel(readable[link.target_id], True)

    process_ids = ids_by_type[EvidenceForTargetType.PROCESS]
    if process_ids:
        processes = await records_repo.load_label_processes(session, caller.org_id, process_ids)
        grants = await _grants_for(session, caller, "process.read", grant_sets)
        readable = {
            process_id: process.name
            for process_id, process in processes.items()
            if authorize(
                grants,
                "process.read",
                ResourceContext(process_ids=frozenset({str(process_id)})),
                ctx,
            ).allow
        }
        for link in links:
            if link.target_type is EvidenceForTargetType.PROCESS and link.target_id in readable:
                out[link.id] = EvidenceTargetLabel(readable[link.target_id], True)

    clause_ids = ids_by_type[EvidenceForTargetType.CLAUSE]
    if clause_ids:
        clauses = await records_repo.load_label_clauses(session, caller.org_id, clause_ids)
        grants = await _grants_for(session, caller, "clauseMap.read", grant_sets)
        allowed = authorize(grants, "clauseMap.read", ResourceContext.system(), ctx).allow
        if allowed:
            clause_labels = {
                clause_id: _headline(clause.number, clause.title)
                for clause_id, clause in clauses.items()
            }
            for link in links:
                if (
                    link.target_type is EvidenceForTargetType.CLAUSE
                    and link.target_id in clause_labels
                ):
                    out[link.id] = EvidenceTargetLabel(clause_labels[link.target_id], True)

    finding_ids = ids_by_type[EvidenceForTargetType.FINDING]
    if finding_ids:
        findings = await records_repo.load_label_findings(session, caller.org_id, finding_ids)
        grants = await _grants_for(session, caller, "finding.read", grant_sets)
        allowed = authorize(grants, "finding.read", ResourceContext.system(), ctx).allow
        if allowed:
            finding_labels = {
                finding_id: _headline(base.identifier, base.title)
                for finding_id, (_finding, base) in findings.items()
            }
            for link in links:
                if (
                    link.target_type is EvidenceForTargetType.FINDING
                    and link.target_id in finding_labels
                ):
                    out[link.id] = EvidenceTargetLabel(finding_labels[link.target_id], True)

    stage_ids = ids_by_type[EvidenceForTargetType.CAPA_STAGE]
    if stage_ids:
        stages = await records_repo.load_label_capa_stages(session, caller.org_id, stage_ids)
        grants = await _grants_for(session, caller, "capa.read", grant_sets)
        stage_labels: dict[uuid.UUID, str] = {}
        for stage_id, (stage, capa, base) in stages.items():
            resource = (
                ResourceContext(process_ids=frozenset({str(capa.process_id)}))
                if capa.process_id is not None
                else ResourceContext.system()
            )
            if authorize(grants, "capa.read", resource, ctx).allow:
                stage_labels[stage_id] = f"{base.identifier} — {stage.stage.value}"
        for link in links:
            if (
                link.target_type is EvidenceForTargetType.CAPA_STAGE
                and link.target_id in stage_labels
            ):
                out[link.id] = EvidenceTargetLabel(stage_labels[link.target_id], True)

    return out
