"""The read-only filesystem mirror (slice S7, AC#2) — regenerate the on-disk tree from the vault.

The mirror is a regenerated, **read-only** export of the **Effective-only** state of the vault
(doc 04 §10): authority flows vault → mirror, never the reverse (D2). It exists for offline
browsing, OS-level backup convenience, and human reassurance. It is fully regenerable from PG +
MinIO and is **never backup-critical**.

**What S7 builds (the minimal, proof-focused slice):**
- Enumerate every ``Effective`` ``document_version`` (gate on ``version_state``; drafts/superseded/
  obsolete are provably excluded), pull its **source bytes** from MinIO, and lay out a flat tree:
  ``current/{identifier}_{revision_label}/`` holding the source file + ``metadata.json`` +
  ``CHANGELOG.md``, with a top-level ``INDEX.md`` + ``_meta/manifest.json``.
- Write the whole tree into a fresh ``.builds/<uuid>/`` then **atomically swap** the
  ``current`` symlink onto it (renaming a symlink over an existing symlink is atomic on one
  filesystem). This is the AC#2 mechanism: an edited mirror file is overwritten because the *whole
  tree* is rebuilt and the live pointer repointed — drift can never become a competing truth.

**Deferred (with seams):** rendering is deferred to S7b — the source bytes are written and
``metadata.json`` records ``render_status:"pending"`` (NOT R26's ``no_controlled_rendition``); the
``RenderSink`` seam (``render.py``) swaps in the Gotenberg-backed watermarked-PDF renderer later.
The clause/process IA tree (doc 04 §10.3) is deferred to **S9** (needs ``clause_mapping``); S7 uses
a deliberately flat layout. The SHA-256 drift scan / quarantine / ``MIRROR_DRIFT_DETECTED`` alarm
are **v1** (D-6): the ``_meta/manifest.json`` here is a generated artifact only — there is no
comparison/scan code in S7.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._vault_enums import VersionState
from ...db.models.app_user import AppUser
from ...db.models.blob import Blob
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.session import get_sessionmaker
from . import storage
from .render import RenderRequest, RenderSink, RenderStatus, get_render_sink

logger = logging.getLogger("easysynq.mirror")


@dataclasses.dataclass(frozen=True, slots=True)
class EffectiveDoc:
    """The materialized join (document + Effective version + blob + owner) the build needs."""

    identifier: str
    title: str
    revision_label: str
    change_significance: str
    change_reason: str
    effective_from: datetime.datetime | None
    owner_user_id: uuid.UUID
    owner_display: str
    classification: str
    source_sha256: str
    mime_type: str
    size_bytes: int
    bucket: str
    object_key: str
    version_id: uuid.UUID
    org_id: uuid.UUID
    rendition_blob_sha256: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class MirrorSyncResult:
    documents: int
    files: int
    pending_renditions: int


async def list_effective_versions(session: AsyncSession) -> list[EffectiveDoc]:
    """Every Effective version joined to its document + source blob, ordered by identifier.

    Gated on ``DocumentVersion.version_state == Effective`` (the version enum), the authoritative
    selector the cutover maintains — NOT ``documented_information.current_state``."""
    rows = (
        await session.execute(
            select(DocumentVersion, DocumentedInformation, Blob, AppUser)
            .join(DocumentedInformation, DocumentVersion.document_id == DocumentedInformation.id)
            .join(Blob, DocumentVersion.source_blob_sha256 == Blob.sha256)
            .join(AppUser, DocumentedInformation.owner_user_id == AppUser.id)
            .where(DocumentVersion.version_state == VersionState.Effective)
            .order_by(DocumentedInformation.identifier)
        )
    ).all()
    return [
        EffectiveDoc(
            identifier=doc.identifier,
            title=doc.title,
            revision_label=ver.revision_label,
            change_significance=ver.change_significance.value,
            change_reason=ver.change_reason,
            effective_from=ver.effective_from,
            owner_user_id=doc.owner_user_id,
            owner_display=owner.display_name,
            classification=doc.classification.value,
            source_sha256=ver.source_blob_sha256,
            mime_type=blob.mime_type,
            size_bytes=blob.size_bytes,
            bucket=blob.bucket,
            object_key=blob.object_key,
            version_id=ver.id,
            org_id=ver.org_id,
            rendition_blob_sha256=ver.rendition_blob_sha256,
        )
        for ver, doc, blob, owner in rows
    ]


def _safe(name: str) -> str:
    """Make a path component filesystem-safe (no separators / NUL); never empty."""
    cleaned = name.replace("/", "_").replace("\\", "_").replace("\x00", "").strip()
    return cleaned or "untitled"


def _ext(mime_type: str) -> str:
    base = mime_type.split(";")[0].strip() if mime_type else ""
    return (mimetypes.guess_extension(base) if base else None) or ".bin"


def _doc_dirname(eff: EffectiveDoc) -> str:
    return _safe(f"{eff.identifier}_{eff.revision_label}")


def _source_filename(eff: EffectiveDoc, ext: str) -> str:
    return _safe(f"{eff.identifier} {eff.title} (Rev {eff.revision_label})") + ext


def _effective_date(eff: EffectiveDoc) -> str:
    # UTC calendar date (R8 org-tz display deferred with the storage_config/org-settings model).
    return eff.effective_from.date().isoformat() if eff.effective_from else "—"


def _changelog_md(eff: EffectiveDoc) -> str:
    return (
        f"# {eff.identifier} — {eff.title}\n\n"
        f"**Rev {eff.revision_label}** · Effective {_effective_date(eff)} · "
        f"{eff.change_significance}\n\n"
        f"{eff.change_reason}\n"
    )


def _index_md(effs: list[EffectiveDoc]) -> str:
    lines = [
        "# EasySynQ Controlled Document Mirror",
        "",
        "Effective documents only — read-only, regenerated from the vault "
        "(authority flows vault → mirror; D2).",
        "",
        "| Identifier | Title | Rev | Effective | SHA-256 |",
        "|---|---|---|---|---|",
    ]
    for eff in effs:
        lines.append(
            f"| {eff.identifier} | {eff.title} | {eff.revision_label} | "
            f"{_effective_date(eff)} | {eff.source_sha256} |"
        )
    return "\n".join(lines) + "\n"


def _metadata(
    eff: EffectiveDoc, source_filename: str, render_status: str, no_controlled_rendition: bool
) -> bytes:
    meta: dict[str, object] = {
        "identifier": eff.identifier,
        "title": eff.title,
        "revision_label": eff.revision_label,
        "change_significance": eff.change_significance,
        "change_reason": eff.change_reason,
        "effective_from": eff.effective_from.isoformat() if eff.effective_from else None,
        "owner_user_id": str(eff.owner_user_id),
        "classification": eff.classification,
        # Coupled to the gate (list_effective_versions filters to Effective) — not a bare literal.
        "version_state": VersionState.Effective.value,
        "source_sha256": eff.source_sha256,
        "source_filename": source_filename,
        "mime_type": eff.mime_type,
        "size_bytes": eff.size_bytes,
        # "rendered" (watermarked PDF) | "pending" (transient) | "unrenderable" (R26).
        "render_status": render_status,
    }
    if no_controlled_rendition:
        # R26 (doc 04 §11.4): a genuinely non-renderable format — surfaced for the QM dashboard
        # (doc 13). Distinct from "pending"; only present when true.
        meta["no_controlled_rendition"] = True
    return (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode()


def _write(path: Path, data: bytes, manifest: list[dict[str, object]], rel_root: Path) -> None:
    path.write_bytes(data)
    manifest.append(
        {
            "path": str(path.relative_to(rel_root)),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    )


async def _cache_rendition(session: AsyncSession, eff: EffectiveDoc, pdf: bytes) -> None:
    """Persist a freshly-rendered controlled PDF: PUT it (content-addressed) into the non-WORM
    renditions bucket, INSERT a derived ``Blob`` row, and point the version's
    ``rendition_blob_sha256`` at it — so the next sync is a cache hit (no Gotenberg). Staged on
    ``session`` (sync_mirror commits)."""
    bucket = get_settings().s3_bucket_renditions
    sha = hashlib.sha256(pdf).hexdigest()
    await storage.put_bytes(pdf, sha, bucket=bucket, content_type="application/pdf")
    await session.execute(
        pg_insert(Blob)
        .values(
            sha256=sha,
            org_id=eff.org_id,
            size_bytes=len(pdf),
            mime_type="application/pdf",
            bucket=bucket,
            object_key=sha,
            worm_locked=False,  # renditions are derived + rebuildable (doc 14 §5.4)
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    await session.execute(
        update(DocumentVersion)
        .where(DocumentVersion.id == eff.version_id)
        .values(rendition_blob_sha256=sha)
    )


async def _resolve_rendition(
    eff: EffectiveDoc, source_bytes: bytes, render_sink: RenderSink, session: AsyncSession | None
) -> tuple[bytes, str, str, bool]:
    """(content, ext, render_status, no_controlled_rendition). Cache hit first; else render via the
    sink and (when a session is available — the worker path) cache a RENDERED result."""
    # Cache hit — a prior sync already rendered this exact version.
    if eff.rendition_blob_sha256:
        try:
            cached = await storage.fetch_bytes(
                eff.rendition_blob_sha256, bucket=get_settings().s3_bucket_renditions
            )
            return cached, ".pdf", RenderStatus.RENDERED.value, False
        except Exception:  # noqa: BLE001 — cached rendition vanished; re-render below
            logger.warning(
                "mirror.rendition_cache_miss",
                extra={"extra_fields": {"version_id": str(eff.version_id)}},
            )

    request = RenderRequest(
        identifier=eff.identifier,
        title=eff.title,
        revision_label=eff.revision_label,
        effective_from=eff.effective_from,
        classification=eff.classification,
        copy_status="CONTROLLED COPY",  # only Effective reaches the mirror (doc 04 §11.2)
        owner=eff.owner_display,
        mime_type=eff.mime_type,
        source_filename=_source_filename(eff, _ext(eff.mime_type)),
        version_id=eff.version_id,
    )
    result = await render_sink.render(request, source_bytes)
    if result.status is RenderStatus.RENDERED and result.pdf is not None:
        if session is not None:
            await _cache_rendition(session, eff, result.pdf)
        return result.pdf, ".pdf", RenderStatus.RENDERED.value, False
    if result.status is RenderStatus.NON_RENDERABLE:
        return source_bytes, _ext(eff.mime_type), RenderStatus.NON_RENDERABLE.value, True
    return source_bytes, _ext(eff.mime_type), RenderStatus.PENDING.value, False


async def build_tree(
    build_root: Path,
    effs: list[EffectiveDoc],
    render_sink: RenderSink,
    session: AsyncSession | None = None,
) -> tuple[list[dict[str, object]], int]:
    """Write the complete mirror tree into ``build_root`` (a fresh dir). Each Effective version is
    rendered to a watermarked controlled-copy PDF (cached after the first render); a renderer outage
    falls back to source bytes (``pending``), a non-renderable format to source + R26
    ``no_controlled_rendition``. Returns the manifest file list + the count of pending renditions.
    ``session`` (the worker's, under the advisory lock) is needed to cache renditions; without one
    (pure-unit / no-op sink) rendering still writes the bytes but does not persist the cache."""
    (build_root / "_meta").mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    pending = 0

    for eff in effs:
        source_bytes = await storage.fetch_bytes(eff.object_key, bucket=eff.bucket)
        content, ext, render_status, no_rendition = await _resolve_rendition(
            eff, source_bytes, render_sink, session
        )
        if render_status == RenderStatus.PENDING.value:
            pending += 1

        doc_dir = build_root / _doc_dirname(eff)
        doc_dir.mkdir(parents=True, exist_ok=True)
        source_filename = _source_filename(eff, ext)
        _write(doc_dir / source_filename, content, manifest, build_root)
        _write(
            doc_dir / "metadata.json",
            _metadata(eff, source_filename, render_status, no_rendition),
            manifest,
            build_root,
        )
        _write(doc_dir / "CHANGELOG.md", _changelog_md(eff).encode(), manifest, build_root)

    _write(build_root / "INDEX.md", _index_md(effs).encode(), manifest, build_root)
    # The machine manifest (doc 04 §10.3). Generated artifact only — NO scan/diff consumes it in S7
    # (drift detection is v1, D-6). ``generated_at`` is the one non-deterministic field by design.
    manifest_doc = {
        "schema": "easysynq.mirror.manifest/1",
        "generated_at": datetime.datetime.now(tz=datetime.UTC).isoformat(),
        "files": sorted(manifest, key=lambda f: str(f["path"])),
    }
    (build_root / "_meta" / "manifest.json").write_bytes(
        (json.dumps(manifest_doc, indent=2, sort_keys=True) + "\n").encode()
    )
    return manifest, pending


def _prune_builds(mirror_path: Path, keep_name: str) -> None:
    builds = mirror_path / ".builds"
    if builds.is_dir():
        for child in builds.iterdir():
            if child.name != keep_name:
                shutil.rmtree(child, ignore_errors=True)
    for stray in mirror_path.glob(".current.*.tmp"):
        try:
            stray.unlink()
        except OSError:
            pass


def atomic_swap(mirror_path: Path, build_root: Path) -> None:
    """Atomically repoint ``current`` at ``build_root`` (which must live under
    ``mirror_path/.builds``). A relative symlink is created at a temp name then ``os.replace``'d
    onto ``current`` — renaming a symlink over an existing symlink is atomic on one filesystem, so a
    browser never sees a half-written tree and the prior tree stays intact if anything fails before
    the rename. Stale builds are pruned afterward."""
    current = mirror_path / "current"
    relative_target = os.path.join(".builds", build_root.name)
    tmp_link = mirror_path / f".current.{uuid.uuid4().hex}.tmp"
    os.symlink(relative_target, tmp_link)
    try:
        os.replace(tmp_link, current)
    except OSError:
        tmp_link.unlink(missing_ok=True)
        raise
    _prune_builds(mirror_path, keep_name=build_root.name)


async def sync_mirror(
    *,
    mirror_path: str | os.PathLike[str] | None = None,
    render_sink: RenderSink | None = None,
    session: AsyncSession | None = None,
) -> MirrorSyncResult:
    """Full rebuild + atomic swap of the read-only mirror. Idempotent: a duplicate call re-converges
    on the same content (renditions are cached after the first render). ``mirror_path`` defaults to
    ``settings.mirror_path`` (tests override it); ``session`` is opened from the app sessionmaker
    (the non-owner ``easysynq_app`` role — SELECT the vault + INSERT a rendition ``blob`` + UPDATE
    the rendition FK is all it needs) when not supplied. The session is held through ``build_tree``
    and **committed** so cached renditions persist; the atomic swap then publishes the tree."""
    root = Path(mirror_path) if mirror_path is not None else Path(get_settings().mirror_path)
    sink = render_sink if render_sink is not None else get_render_sink()

    builds = root / ".builds"
    builds.mkdir(parents=True, exist_ok=True)
    build_root = builds / uuid.uuid4().hex
    build_root.mkdir(parents=True, exist_ok=True)

    async def _build(s: AsyncSession) -> tuple[list[dict[str, object]], int, int]:
        effs = await list_effective_versions(s)
        manifest, pending = await build_tree(build_root, effs, sink, s)
        await s.commit()  # persist the rendition cache writes (blob rows + version FKs)
        return manifest, pending, len(effs)

    if session is not None:
        manifest, pending, count = await _build(session)
    else:
        async with get_sessionmaker()() as own:
            manifest, pending, count = await _build(own)

    atomic_swap(root, build_root)
    return MirrorSyncResult(documents=count, files=len(manifest), pending_renditions=pending)
