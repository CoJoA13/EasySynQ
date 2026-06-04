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

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._ingestion_enums import (
    ImportConfidenceBand,
    ImportKind,
    ImportRunStatus,
)
from ..db.models.app_user import AppUser
from ..db.models.import_classification import ImportClassification
from ..db.models.import_extract import ImportExtract
from ..db.models.import_file import ImportFile
from ..db.models.import_run import ImportRun
from ..db.session import get_session
from ..services.authz import require
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
    limit: int = 100,
    offset: int = 0,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Paginated file inventory + each file's classification proposal (optional
    ``?disposition=included|excluded|quarantine``, ``?kind=DOCUMENT|RECORD|UNKNOWN``,
    ``?band=HIGH|MEDIUM|LOW|AMBIGUOUS`` filters). Needs ``import.review``."""
    run, rows = await svc.list_import_files(
        session,
        caller,
        import_id,
        disposition=disposition,
        kind=kind,
        band=band,
        limit=limit,
        offset=offset,
    )
    return {"run_id": str(run.id), "files": [_file_view(f, c) for f, c in rows]}


@router.get("/admin/imports/{import_id}/files/{file_id}")
async def get_import_file_endpoint(
    import_id: uuid.UUID,
    file_id: uuid.UUID,
    caller: AppUser = Depends(_import_review),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One file's full review detail: inventory + extraction (text/props/structure) + the scored
    classification proposal with its evidence list. Needs ``import.review``."""
    run, f, ext, cls = await svc.list_import_file_detail(session, caller, import_id, file_id)
    view = _file_view(f, cls)
    view["run_id"] = str(run.id)
    view["extract"] = _extract_view(ext)
    view["classification"] = _classification_view(cls, with_evidence=True)
    return view


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
