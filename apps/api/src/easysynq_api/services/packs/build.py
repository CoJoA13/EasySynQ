"""The evidence-pack build worker — re-resolve, assemble, seal (slice S-pack-1, doc 06 §7).

``build`` runs on the Celery worker (``tasks/packs.py``). It is **fail-closed, single-transaction,
and idempotent**:

* Loads the pack ``FOR UPDATE`` and no-ops if it is not BUILDING or already has a ``pack_record_id``
  (``task_acks_late=True`` re-delivery safety — a retry never double-registers the EVIDENCE record).
* **Re-resolves + re-classifies** at build time and atomically replaces the preview ``pack_item``
  rows, so the seal is over one coherent set (the TOCTOU fix — never trust stale preview rows).
* Assembles the pack contents (records' evidence originals + their pinned governing versions +
  manifest/cover/gap/exclusion reports) into a ZIP, seals it with the domain-separated
  ``pack_content_hash`` (computed over the content list, NOT the non-deterministic ZIP bytes),
  writes it to the WORM ``records`` bucket, and **registers it as a RETAIN_PERMANENT EVIDENCE** via
  the records ``capture_record`` (blob-row-iff-bytes holds; the fresh ZIP passes the cross-bucket
  423 guard).
* Commits the EVIDENCE record + the SEALED flip in ONE transaction; on any error it writes FAILED +
  the reason and emits PACK_BUILD_FAILED, then re-raises for Celery visibility.

Rendering note (S-pack-1 scope): a pinned version is included as its cached controlled rendition
blob when present, else its source bytes (a ``document_version`` always has a source blob). Live
Gotenberg rendering of pinned/superseded versions + the §11.3 band are deferred to S-pack-2's
export-format work — so S-pack-1 never depends on the renderer and the authentic edition-in-force is
always included (no R28 silent drop).
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import uuid
import zipfile
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import EventType
from ...db.models._pack_enums import PackInclusionStatus, PackScopeKind, PackStatus
from ...db.models.app_user import AppUser, UserStatus
from ...db.models.document_version import DocumentVersion
from ...db.models.evidence_pack import EvidencePack
from ...domain.packs.content_hash import pack_content_hash
from ..records import capture_record
from ..records import repository as records_repo
from ..vault import storage
from . import repository as repo
from . import service
from .dossier import DossierBuild, build_dossier


def _scope_ids(pack: EvidencePack) -> list[uuid.UUID]:
    key = service._SCOPE_SELECTOR_KEY[pack.scope_kind.value]
    return [uuid.UUID(str(v)) for v in (pack.scope_selector.get(key) or [])]


async def _gather_record_files(
    session: AsyncSession, record_id: uuid.UUID
) -> tuple[bool, list[tuple[str, bytes]], list[str]]:
    """Fetch an INCLUDED record's evidence originals. Returns (ok, files, shas). ``ok`` is False
    when a blob's bytes are physically gone (head 404) — a build-time genuine absence the preview's
    DB-only tombstone check could miss. A form-only record (no blobs) is ``ok`` with empty files."""
    blobs = await records_repo.list_evidence_blobs(session, record_id)
    files: list[tuple[str, bytes]] = []
    shas: list[str] = []
    for eb, blob in blobs:
        head = await storage.head(blob.object_key, bucket=blob.bucket)
        if not head.exists:
            return False, [], []
        data = await storage.fetch_bytes(blob.object_key, bucket=blob.bucket)
        name = eb.filename or f"{eb.blob_sha256}.bin"
        files.append((f"records/{record_id}/{name}", data))
        shas.append(eb.blob_sha256)
    return True, files, shas


async def _gather_version_files(
    version: DocumentVersion,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Fetch a pinned governing version's bytes — its cached controlled rendition if present, else
    its source bytes (always present; a version's source_blob is NOT NULL). Returns (files,shas)."""
    settings = get_settings()
    shas: list[str] = [version.source_blob_sha256]
    if version.rendition_blob_sha256 is not None:
        shas.append(version.rendition_blob_sha256)
        data = await storage.fetch_bytes(
            version.rendition_blob_sha256, bucket=settings.s3_bucket_renditions
        )
        return [(f"documents/{version.id}.pdf", data)], shas
    data = await storage.fetch_bytes(
        version.source_blob_sha256, bucket=settings.s3_bucket_documents
    )
    return [(f"documents/{version.id}.bin", data)], shas


async def _assemble(
    session: AsyncSession,
    pack: EvidencePack,
    included: list[service.ClassifiedRecord],
    excluded: list[service.ClassifiedRecord],
    versions: dict[uuid.UUID, DocumentVersion],
    *,
    content_hash: str,
    gap: dict[str, Any],
    exclusion: dict[str, Any],
    generated_at: datetime.datetime,
    files: list[tuple[str, bytes]],
    dossier: DossierBuild | None = None,
) -> bytes:
    """Build the pack ZIP: the cover sheet (carries the manifest content hash), the machine-readable
    traceability manifest, the gap + exclusion reports, the evidence/version files, and (for
    FINDING/CAPA scope) the dossier subject files. ``files`` already includes the dossier bytes;
    ``dossier`` carries its manifest index + the sealed digest."""
    manifest_records: list[dict[str, Any]] = []
    for c in included:
        links = await records_repo.list_evidence_links(session, c.record.id)
        manifest_records.append(
            {
                "id": str(c.record.id),
                "identifier": c.base.identifier,
                "record_type": c.record.record_type.value,
                "title": c.base.title,
                "captured_at": c.record.captured_at.isoformat() if c.record.captured_at else None,
                "content_hash": c.record.content_hash,
                "source_document_id": (
                    str(c.record.source_document_id) if c.record.source_document_id else None
                ),
                "source_version_id": (
                    str(c.record.source_version_id) if c.record.source_version_id else None
                ),
                "evidence_for": [
                    {"target_type": link.target_type.value, "target_id": str(link.target_id)}
                    for link in links
                ],
            }
        )
    manifest_versions = [
        {
            "id": str(v.id),
            "document_id": str(v.document_id),
            "revision_label": v.revision_label,
            "version_seq": v.version_seq,
            "source_blob_sha256": v.source_blob_sha256,
            "rendition_blob_sha256": v.rendition_blob_sha256,
            "rendition_included": v.rendition_blob_sha256 is not None,
        }
        for v in versions.values()
    ]
    period_lo = pack.period_start.isoformat() if pack.period_start else None
    period_hi = pack.period_end.isoformat() if pack.period_end else None
    manifest: dict[str, Any] = {
        "evidence_pack_id": str(pack.id),
        "title": pack.title,
        "scope_kind": pack.scope_kind.value,
        "scope_selector": pack.scope_selector,
        "period": [period_lo, period_hi],
        "period_basis": "record captured_at (not activity date)",
        "framework_id": str(pack.framework_id),
        "generated_at": generated_at.isoformat(),
        "content_hash": content_hash,
        "records": manifest_records,
        "governing_versions": manifest_versions,
        "excluded": [
            {"record_id": str(c.record.id), "status": c.status.value, "reason": c.reason}
            for c in excluded
        ],
    }
    if dossier is not None:
        # The scope subjects (findings/CAPAs) are NOT pack_item records — they live here, with their
        # content_hash + dossier path. dossier.digest is reconstructable from these per-file shas.
        manifest["dossier_subjects"] = dossier.subjects
        manifest["dossier"] = {"files": dossier.file_manifest, "digest": dossier.digest}

    scheme = "easysynq.evidencepack.v2" if dossier is not None else "easysynq.evidencepack.v1"
    if gap.get("applicable", True) is False:
        gap_line = "Gap report:     N/A (finding/CAPA scope)\n"
    else:
        gap_line = (
            f"Gap report:     {gap['gap_count']} of {gap['in_scope_star_clauses']} in-scope "
            "mandatory clauses lacking current evidence\n"
        )
    dossier_line = (
        f"Dossier:        {len(dossier.subjects)} subject(s), digest {dossier.digest}\n"
        if dossier is not None
        else ""
    )
    cover = (
        "EVIDENCE PACK — controlled audit bundle\n"
        f"Pack ID:        {pack.id}\n"
        f"Title:          {pack.title}\n"
        f"Scope:          {pack.scope_kind.value} {pack.scope_selector}\n"
        f"Period:         {period_lo} .. {period_hi} (by captured_at)\n"
        f"Generated at:   {generated_at.isoformat()}\n"
        f"Records:        {len(included)} included, {len(excluded)} excluded\n"
        f"Governing docs: {len(versions)} pinned version(s)\n"
        f"{gap_line}"
        f"{dossier_line}"
        f"Content hash:   {content_hash}\n"
        f"\nVerify: re-hash the manifest content list with the {scheme} scheme and\n"
        "compare to Content hash above. Each evidence/version/dossier file's SHA-256 is in the\n"
        "manifest (the dossier digest is the hash over its sorted per-file SHA-256s).\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("cover.txt", cover)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        zf.writestr("gap_report.json", json.dumps(gap, indent=2, sort_keys=True))
        zf.writestr("exclusion_report.json", json.dumps(exclusion, indent=2, sort_keys=True))
        for path, data in files:
            zf.writestr(path, data)
    return buf.getvalue()


async def _fail(session: AsyncSession, pack_id: uuid.UUID, reason: str) -> None:
    """Mark a build FAILED + record the reason + emit PACK_BUILD_FAILED, in its own transaction."""
    await session.rollback()
    pack = await repo.get_pack(session, pack_id, for_update=True)
    if pack is None:  # pragma: no cover - defensive
        return
    pack.status = PackStatus.FAILED
    pack.error = reason[:1000]
    generator = await session.get(AppUser, pack.created_by)
    if generator is not None:
        service.emit_pack_event(
            session, generator, EventType.PACK_BUILD_FAILED, pack.id, after={"error": pack.error}
        )
    else:  # pragma: no cover - the generator FK is RESTRICT
        service.emit_pack_event_system(
            session, pack.org_id, EventType.PACK_BUILD_FAILED, pack.id, after={"error": pack.error}
        )
    await session.commit()


async def build(session: AsyncSession, pack_id: uuid.UUID) -> None:
    """Assemble + seal a pack. Single transaction; idempotent on retry; fail-closed."""
    pack = await repo.get_pack(session, pack_id, for_update=True)
    if pack is None or pack.status is not PackStatus.BUILDING or pack.pack_record_id is not None:
        return  # nothing to do (already sealed, not building, or a redundant re-delivery)

    generator = await session.get(AppUser, pack.created_by)
    if generator is None or generator.status is UserStatus.DISABLED:
        await _fail(session, pack_id, "generator account is gone or disabled")
        return

    try:
        scope_ids = _scope_ids(pack)
        candidates = await repo.resolve_candidates(
            session,
            pack.org_id,
            scope_kind=pack.scope_kind.value,
            scope_ids=scope_ids,
            period_start=pack.period_start,
            period_end=pack.period_end,
        )
        classified = await service.classify_candidates(session, generator, candidates)

        # Gather files for INCLUDED records; a build-time byte absence downgrades to absence.
        files: list[tuple[str, bytes]] = []
        evidence_shas: list[str] = []
        final: list[service.ClassifiedRecord] = []
        for c in classified:
            if c.status is not PackInclusionStatus.INCLUDED:
                final.append(c)
                continue
            ok, rec_files, shas = await _gather_record_files(session, c.record.id)
            if not ok:
                final.append(
                    service.ClassifiedRecord(
                        c.record,
                        c.base,
                        PackInclusionStatus.EXCLUDED_ABSENCE,
                        "evidence unavailable",
                    )
                )
                continue
            files.extend(rec_files)
            evidence_shas.extend(shas)
            final.append(c)

        included = [c for c in final if c.status is PackInclusionStatus.INCLUDED]
        excluded = [c for c in final if c.status is not PackInclusionStatus.INCLUDED]

        # Pinned governing versions of the included records.
        version_ids: list[uuid.UUID] = []
        for c in included:
            vid = c.record.source_version_id
            if vid is not None and vid not in version_ids:
                version_ids.append(vid)
        versions = await repo.get_document_versions(session, version_ids)
        for vid in version_ids:
            version = versions.get(vid)
            if version is None:  # pragma: no cover - source_version_id FK guarantees it
                continue
            vfiles, vshas = await _gather_version_files(version)
            files.extend(vfiles)
            evidence_shas.extend(vshas)

        # Synthesized dossier for FINDING/CAPA scope (the finding fields / the CAPA stage trail +
        # e-signatures); sealed via dossier_digest into the v2 hash. None for clause/process.
        dossier: DossierBuild | None = None
        dossier_digest: str | None = None
        if pack.scope_kind in (PackScopeKind.FINDING, PackScopeKind.CAPA):
            dossier = await build_dossier(
                session, pack.org_id, scope_kind=pack.scope_kind.value, scope_ids=scope_ids
            )
            files.extend(dossier.files)
            dossier_digest = dossier.digest
            # The dossier IS material pack content; the seal MUST cover it (else a re-verifier's
            # content_hash diverges from the cover). A None digest here is a hard build failure
            # (caught by the try/except → _fail), never a silent uncovered seal.
            if dossier_digest is None:  # pragma: no cover - build_dossier always returns a digest
                raise RuntimeError("FINDING/CAPA pack sealed without a dossier digest")

        # Seal over the content list (NOT the ZIP bytes — non-deterministic layout).
        excl = service.exclusion_summary(final)
        gap = await service.gap_summary(
            session, pack.org_id, scope_kind=pack.scope_kind.value, scope_ids=scope_ids
        )
        content_hash = pack_content_hash(
            scope_kind=pack.scope_kind.value,
            scope_selector=pack.scope_selector,
            period_start=pack.period_start.isoformat() if pack.period_start else None,
            period_end=pack.period_end.isoformat() if pack.period_end else None,
            included_record_ids=[str(c.record.id) for c in included],
            pinned_version_ids=[str(v) for v in version_ids],
            evidence_sha256s=evidence_shas,
            excluded_permission_record_ids=excl["permission"],
            excluded_absence_record_ids=excl["absence"],
            dossier_digest=dossier_digest,
        )
        generated_at = service._now()
        zip_bytes = await _assemble(
            session,
            pack,
            included,
            excluded,
            versions,
            content_hash=content_hash,
            gap=gap,
            exclusion=excl,
            generated_at=generated_at,
            files=files,
            dossier=dossier,
        )
        zip_sha = hashlib.sha256(zip_bytes).hexdigest()

        # Rebuild membership from the build-time classification (atomic replace of preview rows).
        await repo.delete_pack_items(session, pack.id)
        items, included_count = service._build_items(pack.org_id, pack.id, final)
        session.add_all(items)

        # Write the ZIP to staging, then register it as a RETAIN_PERMANENT EVIDENCE Record (the
        # capture path promotes staging→records WORM + inserts the blob row atomically).
        await storage.put_bytes(
            zip_bytes, zip_sha, bucket=storage._staging_bucket(), content_type="application/zip"
        )
        permanent = await records_repo.ensure_default_policy(session, pack.org_id)
        record = await capture_record(
            session,
            generator,
            record_type="EVIDENCE",
            title=f"Evidence Pack: {pack.title}",
            evidence=[(zip_sha, "application/zip")],
            retention_policy_id=permanent.id,
            _commit=False,
        )

        pack.status = PackStatus.SEALED
        pack.content_hash = content_hash
        pack.zip_blob_sha256 = zip_sha
        pack.pack_record_id = record.id
        pack.generated_at = generated_at
        pack.item_count = included_count
        pack.exclusion_summary = excl
        pack.gap_summary = gap
        pack.error = None
        service.emit_pack_event(
            session,
            generator,
            EventType.PACK_GENERATED,
            pack.id,
            after={
                "scope_kind": pack.scope_kind.value,
                "scope_selector": pack.scope_selector,
                "content_hash": content_hash,
                "zip_blob_sha256": zip_sha,
                "pack_record_id": str(record.id),
                "item_count": included_count,
                "included_records": len(included),
                "excluded_permission": excl["permission_count"],
                "excluded_absence": excl["absence_count"],
                "gap_count": gap["gap_count"],
            },
        )
        await session.commit()
    except Exception as exc:
        await _fail(session, pack_id, f"{type(exc).__name__}: {exc}")
        raise
