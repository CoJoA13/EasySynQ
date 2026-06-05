"""Ingestion run + scan/inventory API (slice S-ing-1, doc 09, doc 15 §8.19).

The authenticated ``/admin/imports`` surface for the v1 Ingestion engine's first stage. A SEPARATE
router (the records/retention precedent). Mounted under ``/api/v1/admin`` and **NOT latch-exempt**
— the
whole surface returns 423 ``setup_incomplete`` until ``setup_state == OPERATIONAL`` (``main.py``).

Authz (doc 09 §15, R5/R35): ``import.*`` are SYSTEM-scope, admin-only keys (already seeded in 0004;
held by the System Administrator role bundle). A deliberate SoD-as-data split — **writes**
(``POST`` create + cancel) → ``import.execute`` (Avery operates), **reads** (all ``GET``) →
``import.review`` (Mara reviews) — so a future reviewer-only grant works with zero code change. Both
gate at SYSTEM scope via ``require``'s default ``_system_scope`` (no ``{id}``-keyed resolver —
these are
org-level operations, not artifact-scoped). S-ing-1 exposes EXACTLY these five verbs and writes
nothing
to the vault (no ``/commit``, no ``/decision`` — those are later slices)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._ingestion_enums import (
    ImportConfidenceBand,
    ImportDupeMethod,
    ImportKind,
    ImportRunStatus,
)
from ..db.models.app_user import AppUser
from ..db.models.import_classification import ImportClassification
from ..db.models.import_dupe_cluster import ImportDupeCluster
from ..db.models.import_extract import ImportExtract
from ..db.models.import_file import ImportFile
from ..db.models.import_proposal_node import ImportProposalNode
from ..db.models.import_run import ImportRun
from ..db.models.import_version_family import ImportVersionFamily
from ..db.session import get_session
from ..services.authz import require
from ..services.ingestion import review as review_svc
from ..services.ingestion import service as svc

router = APIRouter(prefix="/api/v1", tags=["imports"])

_import_execute = require("import.execute")
_import_review = require("import.review")


class ImportRunCreate(BaseModel):
    source_root: str = Field(min_length=1, max_length=4096)
    profile: str | None = Field(default=None, max_length=128)
    # Accepted + persisted now (they're in the doc 15 §8.19 body / doc 14 §13 columns) but UNUSED in
    # S-ing-1 — ocr_enabled bites at slice 2 (extract), classifier_version at slice 3 (classify).
    ocr_enabled: bool = False
    classifier_version: str | None = Field(default=None, max_length=128)


# --- S-ing-4 review request models (writes gate import.review; honour an Idempotency-Key) ---


class FileDecisionBody(BaseModel):
    """A per-file dimensional decision (doc 15 §8.19, refined). ``action`` is the dimensional set
    accept/correct/exclude/defer — merge/split are rejected here (they have dedicated endpoints).
    ``after`` carries the confirmed/changed dimensions (kind/type_code/clause_numbers/process_names/
    identifier/owner); the R10 kind-confirm rides ``after.kind``."""

    action: str
    after: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=2000)


class BulkSelector(BaseModel):
    """A filter selection for a bulk decision — the EXISTING classification/scan dimensions only
    (NOT a derived review_status)."""

    kind: str | None = None
    band: str | None = None
    disposition: str | None = None


class BulkDecisionBody(BaseModel):
    action: str
    file_ids: list[uuid.UUID] | None = None
    selector: BulkSelector | None = None
    after: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=2000)


class MergeBody(BaseModel):
    file_ids: list[uuid.UUID] = Field(min_length=2)
    effective_file_id: uuid.UUID | None = None
    reconstruct_revision_chain: bool | None = None
    reason: str | None = Field(default=None, max_length=2000)


class SplitBody(BaseModel):
    target_kind: str
    target_id: uuid.UUID
    separate_file_ids: list[uuid.UUID] = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=2000)


def _iso(value: datetime.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _view(run: ImportRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status.value,
        "source_root": run.source_root,
        "profile": run.profile,
        "ocr_enabled": run.ocr_enabled,
        "classifier_version": run.classifier_version,
        "counts": run.counts,
        "error": run.error,
        "created_by": str(run.created_by),
        "created_at": _iso(run.created_at),
        "scan_started_at": _iso(run.scan_started_at),
        "completed_at": _iso(run.completed_at),
    }


def _classification_view(
    c: ImportClassification | None, *, with_evidence: bool = False
) -> dict[str, Any] | None:
    """The Stage-3 scored proposal (R10: kind is a suggestion only — confirmation is S-ing-4)."""
    if c is None:
        return None
    view: dict[str, Any] = {
        "kind": c.kind.value,
        "kind_conf": c.kind_conf,
        "type_code": c.type_code,
        "type_conf": c.type_conf,
        "clause_numbers": list(c.clause_numbers),
        "clause_conf": c.clause_conf,
        "process_names": list(c.process_names) if c.process_names else [],
        "process_conf": c.process_conf,
        "pdca_phase": c.pdca_phase.value if c.pdca_phase is not None else None,
        "band": c.band.value,
        "ambiguous": c.ambiguous,
        "top2_margin": c.top2_margin,
        "classifier_version": c.classifier_version,
    }
    if with_evidence:
        view["evidence"] = c.evidence
    return view


def _extract_view(e: ImportExtract | None) -> dict[str, Any] | None:
    """The Stage-2 extraction detail (the per-file preview)."""
    if e is None:
        return None
    return {
        "status": e.status.value,
        "full_text": e.full_text,
        "text_truncated": e.text_truncated,
        "header_block": e.header_block,
        "embedded_props": e.embedded_props,
        "language": e.language,
        "structure_hints": e.structure_hints,
        "ocr_used": e.ocr_used,
        "ocr_confidence": e.ocr_confidence,
        "char_count": e.char_count,
        "page_count": e.page_count,
        "error": e.error,
        "extractor_version": e.extractor_version,
    }


def _file_view(f: ImportFile, classification: ImportClassification | None = None) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "rel_path": f.rel_path,
        "filename": f.filename,
        "ext": f.ext,
        "size_bytes": f.size_bytes,
        "mime_type": f.mime_type,
        "sha256": f.sha256,
        "staged_blob_uri": f.staged_blob_uri,
        "scan_flags": f.scan_flags,
        "included_candidate": f.included_candidate,
        "mtime": _iso(f.mtime),
        "ctime": _iso(f.ctime),
        "classification": _classification_view(classification),
    }


def _dupe_cluster_view(c: ImportDupeCluster) -> dict[str, Any]:
    """A Stage-4 duplicate cluster (S-ing-3, doc 09 §7.1)."""
    return {
        "id": str(c.id),
        "method": c.method.value,
        "member_file_ids": [str(m) for m in c.member_file_ids],
        "canonical_file_id": str(c.canonical_file_id),
        "jaccard": c.jaccard,
        "evidence": c.evidence,
    }


def _version_family_view(fam: ImportVersionFamily) -> dict[str, Any]:
    """A Stage-4 reconstructed version family (S-ing-3, doc 09 §7.3)."""
    return {
        "id": str(fam.id),
        "family_key": fam.family_key,
        "base_name": fam.base_name,
        "doc_code": fam.doc_code,
        "ordered_member_file_ids": [str(m) for m in fam.ordered_member_file_ids],
        "effective_file_id": str(fam.effective_file_id),
        "reconstruct_revision_chain": fam.reconstruct_revision_chain,
        "evidence": fam.evidence,
    }


def _proposal_view(n: ImportProposalNode | None) -> dict[str, Any] | None:
    """The Stage-5 per-keep-item proposal (S-ing-3, doc 09 §8); NULL for a non-keep file."""
    if n is None:
        return None
    return {
        "proposed_identifier": n.proposed_identifier,
        "identifier_source": n.identifier_source,
        "target_ia_path": n.target_ia_path,
        "proposed_owner": n.proposed_owner,
        "owner_source": n.owner_source,
        "conflict_flags": n.conflict_flags,
    }


def _dedup_membership_view(
    file_id: uuid.UUID,
    clusters: list[ImportDupeCluster],
    family: ImportVersionFamily | None,
) -> dict[str, Any]:
    """A file's derived dedup/family role for the review row (which cluster/family it is in, and
    whether it is the canonical/effective keep or a redundant/superseded member)."""
    exact = next((c for c in clusters if c.method is ImportDupeMethod.EXACT), None)
    near = next((c for c in clusters if c.method is ImportDupeMethod.NEAR), None)
    redundant_of = next(
        (str(c.canonical_file_id) for c in clusters if c.canonical_file_id != file_id), None
    )
    is_canonical = any(c.canonical_file_id == file_id for c in clusters) if clusters else None
    in_family = family is not None
    is_effective = (family.effective_file_id == file_id) if family is not None else None
    superseded_by = (
        str(family.effective_file_id)
        if family is not None and family.effective_file_id != file_id
        else None
    )
    return {
        "in_exact_cluster": exact is not None,
        "in_near_cluster": near is not None,
        "is_canonical": is_canonical,
        "redundant_of_file_id": redundant_of,
        "in_version_family": in_family,
        "is_effective": is_effective,
        "superseded_by_file_id": superseded_by,
    }


@router.post("/admin/imports", status_code=status.HTTP_202_ACCEPTED)
async def create_import_run_endpoint(
    body: ImportRunCreate,
    caller: AppUser = Depends(_import_execute),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Start an import run + enqueue the scan. 422 if ``source_root`` escapes/does not exist; 409
    if a
    scan is already active for the root. Needs ``import.execute``."""
    run = await svc.create_import_run(
        session,
        caller,
        source_root=body.source_root,
        profile=body.profile,
        ocr_enabled=body.ocr_enabled,
        classifier_version=body.classifier_version,
    )
    return _view(run)


@router.get("/admin/imports")
async def list_import_runs_endpoint(
    run_status: ImportRunStatus | None = None,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The org's import runs (newest first; optional ``?run_status=`` filter). Needs
    ``import.review``."""
    runs = await svc.list_import_runs(session, caller, status=run_status)
    return [_view(r) for r in runs]


@router.get("/admin/imports/{import_id}")
async def get_import_run_endpoint(
    import_id: uuid.UUID,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One import run's status + inventory summary. Needs ``import.review``."""
    run = await svc.get_import_run(session, caller, import_id)
    return _view(run)


@router.get("/admin/imports/{import_id}/files")
async def list_import_files_endpoint(
    import_id: uuid.UUID,
    disposition: str | None = None,
    kind: ImportKind | None = None,
    band: ImportConfidenceBand | None = None,
    review_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Paginated file inventory + each file's classification proposal + the S-ing-4 ``review``
    folded effective state. Optional ``?disposition=included|excluded|quarantine`` (scan flag),
    ``?kind=DOCUMENT|RECORD|UNKNOWN``, ``?band=HIGH|MEDIUM|LOW|AMBIGUOUS``, and the derived
    ``?review_status=included|excluded|deferred|undecided`` (folded disposition) filters. Needs
    ``import.review``."""
    run, rows = await review_svc.list_files_review(
        session,
        caller,
        import_id,
        disposition=disposition,
        kind=kind,
        band=band,
        review_status=review_status,
        limit=limit,
        offset=offset,
    )
    return {
        "run_id": str(run.id),
        "files": [{**_file_view(f, c), "review": review} for f, c, review in rows],
    }


@router.get("/admin/imports/{import_id}/files/{file_id}")
async def get_import_file_endpoint(
    import_id: uuid.UUID,
    file_id: uuid.UUID,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One file's full review detail: inventory + extraction (text/props/structure) + the scored
    classification proposal with its evidence list + the S-ing-3 dedup membership and per-keep-item
    proposal (identifier / IA path / conflicts). Needs ``import.review``."""
    run, f, ext, cls = await svc.list_import_file_detail(session, caller, import_id, file_id)
    clusters, family, node = await svc.get_import_file_membership(
        session, caller, import_id, file_id
    )
    view = _file_view(f, cls)
    view["run_id"] = str(run.id)
    view["extract"] = _extract_view(ext)
    view["classification"] = _classification_view(cls, with_evidence=True)
    view["dedup"] = _dedup_membership_view(f.id, list(clusters), family)
    view["proposal"] = _proposal_view(node)
    view["review"] = await review_svc.get_file_review(session, caller, import_id, file_id)
    return view


@router.get("/admin/imports/{import_id}/dupe-clusters")
async def list_dupe_clusters_endpoint(
    import_id: uuid.UUID,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The run's Stage-4 duplicate clusters (exact + near; doc 09 §7.1). Needs ``import.review``."""
    run, clusters = await svc.list_import_dupe_clusters(session, caller, import_id)
    return {"run_id": str(run.id), "clusters": [_dupe_cluster_view(c) for c in clusters]}


@router.get("/admin/imports/{import_id}/version-families")
async def list_version_families_endpoint(
    import_id: uuid.UUID,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The run's Stage-4 reconstructed version families (doc 09 §7.3). Needs ``import.review``."""
    run, families = await svc.list_import_version_families(session, caller, import_id)
    return {"run_id": str(run.id), "families": [_version_family_view(fam) for fam in families]}


@router.get("/admin/imports/{import_id}/checklist")
async def import_checklist_endpoint(
    import_id: uuid.UUID,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The §9.3 pre-commit checklist: ``ready`` + blocking conflicts (duplicate-identifier / vault
    collision / ambiguous-over-threshold, over the EFFECTIVE folded state) + the non-blocking
    ★-coverage projection + advisory counts + folded review stats. Needs ``import.review``."""
    return await review_svc.compute_review_checklist(session, caller, import_id)


@router.get("/admin/imports/{import_id}/decisions")
async def list_import_decisions_endpoint(
    import_id: uuid.UUID,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The run's append-only review-decision log, newest-first (doc 09 §12.2). Needs
    ``import.review``."""
    run, decisions = await review_svc.list_decisions(session, caller, import_id)
    return {"run_id": str(run.id), "decisions": decisions}


@router.post("/admin/imports/{import_id}/files/{file_id}/decision")
async def record_file_decision_endpoint(
    import_id: uuid.UUID,
    file_id: uuid.UUID,
    body: FileDecisionBody,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Record one per-file dimensional decision (accept/correct/exclude/defer + the R10 kind-confirm
    via ``after.kind``). 422 on merge/split (use the dedicated endpoints). 409 if the run is not
    Proposed/Reviewing. Needs ``import.review``."""
    return await review_svc.record_file_decision(
        session,
        caller,
        import_id,
        file_id,
        action=body.action,
        after=body.after,
        reason=body.reason,
        idem_key=idempotency_key,
    )


@router.post("/admin/imports/{import_id}/decisions")
async def record_bulk_decisions_endpoint(
    import_id: uuid.UUID,
    body: BulkDecisionBody,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Apply ONE dimensional action across an explicit ``file_ids`` list OR a ``selector`` filter
    (kind/band/disposition) — the §9.2a scale lever. Bulk kind-confirm (``after.kind``) is the
    explicit human act. Needs ``import.review``."""
    return await review_svc.record_bulk_decisions(
        session,
        caller,
        import_id,
        action=body.action,
        file_ids=body.file_ids,
        selector=body.selector.model_dump() if body.selector is not None else None,
        after=body.after,
        reason=body.reason,
        idem_key=idempotency_key,
    )


@router.post("/admin/imports/{import_id}/merge")
async def merge_files_endpoint(
    import_id: uuid.UUID,
    body: MergeBody,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Combine ≥2 files into one version family (force the revision chain). Sets the family's
    effective member + ``reconstruct_revision_chain`` (the per-family R10 opt-in), preserves
    other families' flags, re-derives the proposal nodes. Needs ``import.review``."""
    return await review_svc.merge_files(
        session,
        caller,
        import_id,
        file_ids=body.file_ids,
        effective_file_id=body.effective_file_id,
        reconstruct_revision_chain=body.reconstruct_revision_chain,
        reason=body.reason,
        idem_key=idempotency_key,
    )


@router.post("/admin/imports/{import_id}/split")
async def split_cluster_endpoint(
    import_id: uuid.UUID,
    body: SplitBody,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Break members out of a dupe-cluster / version-family (a group dropping <2 members is deleted;
    survivors become standalone keep-items), then re-derive the proposal nodes. Needs
    ``import.review``."""
    return await review_svc.split_cluster(
        session,
        caller,
        import_id,
        target_kind=body.target_kind,
        target_id=body.target_id,
        separate_file_ids=body.separate_file_ids,
        reason=body.reason,
        idem_key=idempotency_key,
    )


@router.post("/admin/imports/{import_id}/cancel")
async def cancel_import_run_endpoint(
    import_id: uuid.UUID,
    caller: AppUser = Depends(_import_execute),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Abort an import run (409 if already terminal); the worker stops cooperatively. Needs
    ``import.execute``."""
    run = await svc.cancel_import_run(session, caller, import_id)
    return _view(run)
