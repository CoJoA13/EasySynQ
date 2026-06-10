"""The read-only filesystem mirror (S7 + S9b, AC#2) — regenerate the on-disk tree from PG+MinIO.

The mirror is a regenerated, **read-only** export of the **Effective-only** state of the vault
(doc 04 §10): authority flows vault → mirror, never the reverse (D2). It exists for offline
browsing, OS-level backup convenience, and human reassurance. It is fully regenerable from PG +
MinIO and is **never backup-critical**.

**What S7 built (the minimal, proof-focused slice):**
- Enumerate every ``Effective`` ``document_version`` (gate on ``version_state``; drafts/superseded/
  obsolete are provably excluded), pull its **source bytes** from MinIO, and lay out the tree
  holding, per document, the source file + ``metadata.json`` + ``CHANGELOG.md``, with a top-level
  ``INDEX.md`` + ``_meta/manifest.json``.
- Write the whole tree into a fresh ``.builds/<uuid>/`` then **atomically swap** the
  ``current`` symlink onto it (renaming a symlink over an existing symlink is atomic on one
  filesystem). This is the AC#2 mechanism: an edited mirror file is overwritten because the *whole
  tree* is rebuilt and the live pointer repointed — drift can never become a competing truth.

**What S9b adds (the clause-aligned tree, doc 04 §10.3):** now that ``clause_mapping`` exists, the
flat ``current/{identifier}_{revision_label}/`` layout becomes the IA tree a human browsing the disk
recognizes: ``current/{PHASE}/{NN}-{Word}/{identifier}_{revision_label}/`` where ``PHASE`` is the
*mapped clause's own* ``pdca_phase`` (PLAN/DO/CHECK/ACT) and ``{NN}-{Word}`` is its top-level
ancestor (e.g. ``DO/08-Operation``; clause 7 splits 7.1-7.4 → PLAN, 7.5 → DO). A document mapping
several clauses lives **once** under its numerically-lowest mapped clause and is reached from every
other mapped clause folder via a **relative symlink** (spec-faithful "without duplicating bytes",
§10.3/§10.4). A document with no mappings (only reachable as a pre-S9 upgrade artifact — the
``submit-review`` ≥1-clause gate forbids it otherwise) lands in ``_unmapped/``.

**What S9d adds (the by-process secondary index, doc 04 §10.3):** now that ``process_link`` exists,
a parallel ``current/by-process/{name}/`` tree of **relative symlinks** into the same real doc
folders — a doc linked to a process is reachable from its ``by-process/{name}/`` folder too (bytes
never duplicated). Always built (the doc-14 ``storage_config.mirror_layout`` toggle is deferred to
its config UI; the index is cheap). A doc with no process links simply gets no by-process entry.

**Single-org invariant (D1).** ``list_effective_versions`` is org-agnostic (no ``org_id`` filter);
under D1 (one organization per install) the per-doc ``{identifier}_{revision_label}`` directory name
is globally unique, so clause-bucketing introduces no cross-org collision. Multi-tenant namespacing
is out of scope for v1 (this is pre-existing S7 behavior, not introduced here).

**Rendering is S7b (live). The drift scan is S-drift-2:** the D2+D3 SHA-256 integrity scan /
quarantine / ``MIRROR_STALE`` + ``MIRROR_TAMPER`` audit events live in ``mirror_scan.py``; this
module persists each build's manifest into ``mirror_build`` (the scan's vault-side expected state —
the on-disk ``_meta/manifest.json`` is a generated artifact, verified but never trusted as
authority).
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

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._clause_enums import PdcaPhase
from ...db.models._vault_enums import VersionState
from ...db.models.app_user import AppUser
from ...db.models.blob import Blob
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_blob import EvidenceBlob
from ...db.models.import_run import ImportRun
from ...db.models.mirror_build import MirrorBuild
from ...db.models.process import Process
from ...db.models.process_link import ProcessLink
from ...db.session import get_sessionmaker
from ..common.org import get_single_org_id
from . import storage, verify_token
from .render import RenderRequest, RenderSink, RenderStatus, get_render_sink

logger = logging.getLogger("easysynq.mirror")
_KEEP_BUILD_ROWS = 20


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
    document_id: uuid.UUID
    version_id: uuid.UUID
    org_id: uuid.UUID
    rendition_blob_sha256: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class ClauseRef:
    """A clause a document maps to, with the fields the S9b tree placement + metadata need.

    ``framework_id`` keys the top-level-word lookup so two frameworks' "8" never collide; the
    derived ``top_number``/``sort_key`` drive folder placement (``8.4`` → top ``8``) and the numeric
    ordering that picks the primary placement (``8.5`` before ``8.10``, ``10`` after ``9``)."""

    number: str
    pdca_phase: str
    title: str
    is_mandatory_star: bool
    framework_id: uuid.UUID

    @property
    def top_number(self) -> str:
        return self.number.split(".")[0]

    @property
    def sort_key(self) -> tuple[int, ...]:
        return _clause_sort_key_py(self.number)


@dataclasses.dataclass(frozen=True, slots=True)
class ProcessRef:
    """A process a document is linked to — drives the by-process secondary index (S9d, doc 04
    §10.3) + the metadata.json ``processes`` array."""

    process_id: uuid.UUID
    process_name: str


@dataclasses.dataclass(frozen=True, slots=True)
class MirrorSyncResult:
    documents: int
    files: int
    symlinks: int
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
            document_id=doc.id,
            version_id=ver.id,
            org_id=ver.org_id,
            rendition_blob_sha256=ver.rendition_blob_sha256,
        )
        for ver, doc, blob, owner in rows
    ]


async def fetch_clause_refs(
    session: AsyncSession, doc_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ClauseRef]]:
    """Every clause each document maps to, grouped by ``documented_information_id`` — one batch
    query (index-backed by ``ix_clause_mapping_documented_information_id``), so no N+1 per doc."""
    if not doc_ids:
        return {}
    rows = (
        await session.execute(
            select(
                ClauseMapping.documented_information_id,
                Clause.number,
                Clause.pdca_phase,
                Clause.title,
                Clause.is_mandatory_star,
                Clause.framework_id,
            )
            .join(Clause, ClauseMapping.clause_id == Clause.id)
            .where(ClauseMapping.documented_information_id.in_(doc_ids))
        )
    ).all()
    grouped: dict[uuid.UUID, list[ClauseRef]] = {}
    for doc_id, number, phase, title, star, framework_id in rows:
        grouped.setdefault(doc_id, []).append(
            ClauseRef(
                number=number,
                pdca_phase=phase.value,
                title=title,
                is_mandatory_star=star,
                framework_id=framework_id,
            )
        )
    return grouped


async def fetch_top_words(session: AsyncSession) -> dict[tuple[uuid.UUID, str], str]:
    """``{(framework_id, top_level_number): first-word-of-title}`` for every top-level clause (the
    seven 4..10 per framework, ``parent_id IS NULL``) — the ``{NN}-{Word}`` folder label, keyed by
    framework so a future second standard's "8" can't collide with ISO's."""
    rows = (
        await session.execute(
            select(Clause.framework_id, Clause.number, Clause.title).where(
                Clause.parent_id.is_(None)
            )
        )
    ).all()
    return {(framework_id, number): _top_word(title) for framework_id, number, title in rows}


async def fetch_process_links(
    session: AsyncSession, doc_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ProcessRef]]:
    """Every process each document is linked to, grouped by ``documented_information_id`` — one
    batch query (index-backed by ``ix_process_link_documented_information_id``), the
    ``fetch_clause_refs`` twin. Drives the by-process secondary index (S9d, doc 04 §10.3)."""
    if not doc_ids:
        return {}
    rows = (
        await session.execute(
            select(ProcessLink.documented_information_id, Process.id, Process.name)
            .join(Process, ProcessLink.process_id == Process.id)
            .where(ProcessLink.documented_information_id.in_(doc_ids))
        )
    ).all()
    grouped: dict[uuid.UUID, list[ProcessRef]] = {}
    for doc_id, process_id, name in rows:
        grouped.setdefault(doc_id, []).append(ProcessRef(process_id=process_id, process_name=name))
    return grouped


@dataclasses.dataclass(frozen=True, slots=True)
class ImportReportRef:
    """A committed run's §12.1 Import Report record + the WORM blob holding its markdown bytes."""

    label: str
    object_key: str
    bucket: str
    sha256: str


async def fetch_import_reports(session: AsyncSession) -> list[ImportReportRef]:
    """The §12.1 Import Report of every committed import run (S-ing-5, doc 09 §10.3) — joined
    import_run → its report record's evidence_blob → blob (read ``bucket`` FROM the blob row, the
    packs precedent). Drives the read-only ``current/_ImportReport/`` mirror section. Org-agnostic
    (single-org, D1; the ``list_effective_versions`` precedent). A run whose import_run row has been
    TTL-purged simply drops from the mirror — the RETAIN_PERMANENT report record itself persists."""
    rows = (
        await session.execute(
            select(
                ImportRun.id,
                ImportRun.source_root,
                Blob.object_key,
                Blob.bucket,
                Blob.sha256,
            )
            .join(EvidenceBlob, EvidenceBlob.record_id == ImportRun.report_record_id)
            .join(Blob, Blob.sha256 == EvidenceBlob.blob_sha256)
            .where(ImportRun.report_record_id.isnot(None))
        )
    ).all()
    out: list[ImportReportRef] = []
    for run_id, source_root, object_key, bucket, sha256 in rows:
        label = f"{_safe(Path(source_root).name) or 'import'}-{run_id.hex[:8]}"
        out.append(
            ImportReportRef(label=label, object_key=object_key, bucket=bucket, sha256=sha256)
        )
    return out


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


# Canonical PDCA folder order (PLAN<DO<CHECK<ACT, doc 04 §10.3 visual order) — NOT alphabetical.
_PHASE_ORDER = {phase.value: i for i, phase in enumerate(PdcaPhase)}
# Where an Effective document with zero clause mappings lands (only a pre-S9 upgrade artifact).
_UNMAPPED_DIR = "_unmapped"
# The by-process secondary index root (doc 04 §10.3) — a top-level sibling of the phase folders.
_BY_PROCESS_DIR = "by-process"


def _clause_sort_key_py(number: str) -> tuple[int, ...]:
    """Numeric per-dotted-segment sort key — the Python twin of ``repository._clause_sort_key``
    (``string_to_array(number,'.')::int[]``): ``8.5`` before ``8.10``, ``10`` after ``9`` (not
    lexical). A non-numeric segment sorts last rather than crashing the build."""
    parts: list[int] = []
    for seg in number.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(1_000_000)
    return tuple(parts)


def _top_word(title: str) -> str:
    """The first word of a top-level clause title → the folder label ('Context', 'Operation')."""
    words = title.split()
    return _safe(words[0]) if words else "untitled"


def _placement(phase: str, top_number: str, word: str) -> str:
    """The clause-folder path for a placement: ``'{PHASE}/{NN}-{Word}'`` (e.g. ``DO/08-Operation``).
    ``NN`` is the top-level clause number zero-padded to two digits (doc 04 §10.3)."""
    try:
        nn = f"{int(top_number):02d}"
    except ValueError:
        nn = _safe(top_number)
    return f"{phase}/{nn}-{word}"


def _dir_for(ref: ClauseRef, top_words: dict[tuple[uuid.UUID, str], str]) -> str:
    word = top_words.get((ref.framework_id, ref.top_number), _safe(ref.top_number))
    return _placement(ref.pdca_phase, ref.top_number, word)


def _placement_dirs(
    clause_refs: list[ClauseRef], top_words: dict[tuple[uuid.UUID, str], str]
) -> tuple[str, list[str]]:
    """Resolve a document's on-disk placement from its mapped clauses (doc 04 §10.3).

    Returns ``(primary_dir, other_dirs)``: the real doc folder is written under ``primary_dir`` (the
    placement of the numerically-lowest mapped clause), and every OTHER distinct placement in
    ``other_dirs`` gets a relative symlink to it. Placements are deduped by ``(phase, top_number)``:
    two clauses in one top-level bucket collapse to one folder, while the clause-7 PLAN/DO split
    yields two. ``other_dirs`` is ordered canonically (PLAN<DO<CHECK<ACT, then top number) for a
    deterministic tree/manifest. No mappings → ``(_unmapped, [])`` (a pre-S9 upgrade artifact)."""
    if not clause_refs:
        return _UNMAPPED_DIR, []
    ordered = sorted(clause_refs, key=lambda c: c.sort_key)
    primary = _dir_for(ordered[0], top_words)
    others: dict[str, tuple[int, int]] = {}
    for ref in ordered:
        directory = _dir_for(ref, top_words)
        if directory == primary or directory in others:
            continue
        try:
            top = int(ref.top_number)
        except ValueError:
            top = 1_000_000
        others[directory] = (_PHASE_ORDER.get(ref.pdca_phase, 99), top)
    return primary, sorted(others, key=lambda d: others[d])


def ia_placement_dir(
    clause_refs: list[ClauseRef], top_words: dict[tuple[uuid.UUID, str], str]
) -> str:
    """The single IA home for a document from its mapped clauses — the placement of the
    numerically-lowest mapped clause (``{PHASE}/{NN}-{Word}``), or ``_unmapped`` with no mappings.
    A thin public wrapper over ``_placement_dirs`` (pure) so the S-ing-3 import proposal reuses the
    exact mirror layout — the proposed ``target_ia_path`` byte-matches the eventual mirror."""
    return _placement_dirs(clause_refs, top_words)[0]


def _placement_process_dirs(process_refs: list[ProcessRef]) -> list[str]:
    """The ``by-process/{name}/`` folders a document is symlinked into (doc 04 §10.3, S9d) — one per
    linked process, **deduped by the safe dir string** (two names that ``_safe`` alike collapse to
    one symlink, no FileExistsError) and **sorted** for a byte-deterministic tree/manifest."""
    return sorted({f"{_BY_PROCESS_DIR}/{_safe(ref.process_name)}" for ref in process_refs})


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


def _clause_payload(clause_refs: list[ClauseRef]) -> list[dict[str, object]]:
    """The mapped-clause list for metadata.json / INDEX, numeric-sorted so two builds are
    byte-identical (the §10.4 idempotency invariant)."""
    return [
        {
            "number": ref.number,
            "pdca_phase": ref.pdca_phase,
            "title": ref.title,
            "is_mandatory_star": ref.is_mandatory_star,
        }
        for ref in sorted(clause_refs, key=lambda c: c.sort_key)
    ]


def _process_payload(process_refs: list[ProcessRef]) -> list[dict[str, object]]:
    """The linked-process list for metadata.json, sorted by ``(name, id)`` so two builds are
    byte-identical (the §10.4 idempotency invariant)."""
    return [
        {"id": str(ref.process_id), "name": ref.process_name}
        for ref in sorted(process_refs, key=lambda p: (p.process_name, str(p.process_id)))
    ]


def _index_md(effs: list[EffectiveDoc], clauses_by_doc: dict[uuid.UUID, list[ClauseRef]]) -> str:
    lines = [
        "# EasySynQ Controlled Document Mirror",
        "",
        "Effective documents only — read-only, regenerated from the vault "
        "(authority flows vault → mirror; D2). Organized by the ISO clause spine "
        "(PLAN/DO/CHECK/ACT → top-level clause, doc 04 §10.3).",
        "",
        "| Identifier | Title | Rev | Clauses | Effective | SHA-256 |",
        "|---|---|---|---|---|---|",
    ]
    for eff in effs:
        refs = sorted(clauses_by_doc.get(eff.document_id, []), key=lambda c: c.sort_key)
        clauses = ", ".join(ref.number for ref in refs) or "—"
        lines.append(
            f"| {eff.identifier} | {eff.title} | {eff.revision_label} | {clauses} | "
            f"{_effective_date(eff)} | {eff.source_sha256} |"
        )
    return "\n".join(lines) + "\n"


def _metadata(
    eff: EffectiveDoc,
    source_filename: str,
    render_status: str,
    no_controlled_rendition: bool,
    clause_refs: list[ClauseRef],
    process_refs: list[ProcessRef],
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
        # The clauses this document maps to (doc 02 §2.1) — the basis for its tree placement and the
        # compliance-checklist coverage view (doc 13). Empty only for a pre-S9 upgrade artifact.
        "clauses": _clause_payload(clause_refs),
        # The processes this document is linked to (doc 02 §6.2) — the by-process index + map lens.
        "processes": _process_payload(process_refs),
        # "rendered" (watermarked PDF) | "pending" (transient) | "unrenderable" (R26).
        "render_status": render_status,
    }
    if no_controlled_rendition:
        # R26 (doc 04 §11.4): a genuinely non-renderable format — surfaced for the QM dashboard
        # (doc 13). Distinct from "pending"; only present when true.
        meta["no_controlled_rendition"] = True
    return (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode()


def _write(
    path: Path,
    data: bytes,
    manifest: list[dict[str, object]],
    rel_root: Path,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)  # parent-safe (the _ImportReport/<label>/ case)
    path.write_bytes(data)
    entry: dict[str, object] = {
        "path": str(path.relative_to(rel_root)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    if extra:
        entry.update(extra)
    manifest.append(entry)


def _write_symlink(
    link_path: Path, real_folder: Path, build_root: Path, manifest: list[dict[str, object]]
) -> None:
    """Create a RELATIVE symlink at ``link_path`` pointing at the real doc folder ``real_folder`` (a
    doc reachable from another mapped clause, §10.3). Relative — not absolute — so it survives the
    atomic ``current`` swap (it resolves within the build tree wherever the tree is mounted) and
    never leaks a host path into the read-only mirror. Records a ``{path, symlink_to}`` manifest
    entry (no bytes to hash). Defence-in-depth: the target must resolve within the build tree."""
    target = os.path.relpath(real_folder, link_path.parent)
    resolved = os.path.normpath(os.path.join(link_path.parent, target))
    if os.path.relpath(resolved, build_root).startswith(".."):
        raise ValueError(f"mirror symlink target escapes the build tree: {target!r}")
    link_path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link_path, target_is_directory=True)
    manifest.append({"path": str(link_path.relative_to(build_root)), "symlink_to": target})


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

    # S7c: a signed verify token (doc 05 §6.4) over the immutable {doc, version, source digest},
    # drawn as a QR in the footer. Deterministic (Ed25519 + immutable claims) so the rendition stays
    # content-addressed. ``content_digest`` is the version's source bytes.
    token = verify_token.mint(eff.document_id, eff.version_id, eff.source_sha256)
    verify_url = f"{get_settings().public_base_url.rstrip('/')}/api/v1/verify?t={token}"

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
        verify_url=verify_url,
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
    *,
    clauses_by_doc: dict[uuid.UUID, list[ClauseRef]] | None = None,
    top_words: dict[tuple[uuid.UUID, str], str] | None = None,
    processes_by_doc: dict[uuid.UUID, list[ProcessRef]] | None = None,
) -> tuple[list[dict[str, object]], int]:
    """Write the complete clause-aligned mirror tree into ``build_root`` (a fresh dir, S9b / doc 04
    §10.3). Each Effective version renders to a watermarked controlled-copy PDF (cached after the
    first render); a renderer outage falls back to source bytes (``pending``), a non-renderable
    format to source + R26 ``no_controlled_rendition``. The doc folder is placed under
    ``{PHASE}/{NN}-{Word}/`` for its numerically-lowest mapped clause, with a relative symlink from
    each other mapped clause folder; an unmapped doc lands in ``_unmapped/``. Returns the manifest
    entry list (file entries carry ``sha256``, doc-owned ones also ``document_id``/``version_id``;
    symlink entries carry ``symlink_to``) + the count of
    pending renditions. ``session`` (the worker's, under the advisory lock) is needed to cache
    renditions; without one (pure-unit / no-op sink) rendering still writes the bytes but does not
    persist the cache. ``clauses_by_doc`` / ``top_words`` default to empty (the unit render tests
    pass none → docs land in ``_unmapped/``). **Fresh-dir-only:** a path can flip dir↔symlink
    between builds, so production builds into a new ``.builds/<uuid>`` and swaps (never reuse)."""
    (build_root / "_meta").mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    pending = 0
    cbd = clauses_by_doc or {}
    words = top_words or {}
    pbd = processes_by_doc or {}

    for eff in effs:
        source_bytes = await storage.fetch_bytes(eff.object_key, bucket=eff.bucket)
        content, ext, render_status, no_rendition = await _resolve_rendition(
            eff, source_bytes, render_sink, session
        )
        if render_status == RenderStatus.PENDING.value:
            pending += 1

        refs = cbd.get(eff.document_id, [])
        proc_refs = pbd.get(eff.document_id, [])
        primary_dir, other_dirs = _placement_dirs(refs, words)
        dirname = _doc_dirname(eff)
        doc_dir = build_root / primary_dir / dirname
        doc_dir.mkdir(parents=True, exist_ok=True)
        source_filename = _source_filename(eff, ext)
        # S-drift-2: doc attribution for the scan (additive manifest keys — schema stays /1).
        doc_ref: dict[str, object] = {
            "document_id": str(eff.document_id),
            "version_id": str(eff.version_id),
        }
        _write(doc_dir / source_filename, content, manifest, build_root, extra=doc_ref)
        _write(
            doc_dir / "metadata.json",
            _metadata(eff, source_filename, render_status, no_rendition, refs, proc_refs),
            manifest,
            build_root,
            extra=doc_ref,
        )
        _write(
            doc_dir / "CHANGELOG.md",
            _changelog_md(eff).encode(),
            manifest,
            build_root,
            extra=doc_ref,
        )
        # §10.3: reachable from EVERY mapped clause — real bytes once, a relative symlink each.
        for other in other_dirs:
            _write_symlink(build_root / other / dirname, doc_dir, build_root, manifest)
        # §10.3: the by-process secondary index — a relative symlink from each linked process folder
        # into the same real doc folder (works whether doc_dir is under a clause or _unmapped/).
        for proc_dir in _placement_process_dirs(proc_refs):
            _write_symlink(build_root / proc_dir / dirname, doc_dir, build_root, manifest)

    # §10.3: the read-only Import Report section — each committed run's RETAIN_PERMANENT §12.1
    # report markdown, fetched from the records WORM bucket (records are NOT otherwise mirrored). It
    # needs the
    # session to look them up; a pure/no-op build (no session) skips it. Best-effort per report: a
    # missing blob (e.g. a TTL-purged record) is logged, never fatal to the whole mirror.
    if session is not None:
        for rpt in await fetch_import_reports(session):
            try:
                report_bytes = await storage.fetch_bytes(rpt.object_key, bucket=rpt.bucket)
            except Exception:  # noqa: BLE001 — a missing/unreadable report drops from the mirror
                logger.warning(
                    "mirror.import_report.fetch_failed",
                    extra={"extra_fields": {"label": rpt.label, "sha256": rpt.sha256}},
                )
                continue
            _write(
                build_root / "_ImportReport" / rpt.label / "Import-Report.md",
                report_bytes,
                manifest,
                build_root,
            )

    _write(build_root / "INDEX.md", _index_md(effs, cbd).encode(), manifest, build_root)
    # The machine manifest (doc 04 §10.3). A generated artifact the S-drift-2 scan byte-VERIFIES
    # against the PG-persisted ``manifest_sha256`` but never reads as authority (the
    # ``mirror_build`` row is the expected state). ``generated_at`` is the one non-deterministic
    # field by design — it makes recompute impossible; the stored byte digest is the sound check.
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
    (the non-owner ``easysynq_app`` role — SELECT the vault, INSERT a rendition ``blob`` + UPDATE
    the rendition FK, plus S-drift-2's ``mirror_build`` INSERT/UPDATE/DELETE and ``organization``
    SELECT, all granted by 0046) when not supplied. The session is held through ``build_tree`` and
    **committed** (renditions + the baseline row); the atomic swap then publishes the tree, and a
    final small commit stamps ``swapped_at`` (the pointer-integrity anchor — a crash between swap
    and stamp self-heals at the next scan)."""
    root = Path(mirror_path) if mirror_path is not None else Path(get_settings().mirror_path)
    sink = render_sink if render_sink is not None else get_render_sink()

    builds = root / ".builds"
    builds.mkdir(parents=True, exist_ok=True)
    build_root = builds / uuid.uuid4().hex
    build_root.mkdir(parents=True, exist_ok=True)

    # The row `current` points at must survive the prune (see _build); resolve it up front.
    try:
        current_target: str | None = Path(os.readlink(root / "current")).name
    except OSError:
        current_target = None

    async def _build(s: AsyncSession) -> tuple[list[dict[str, object]], int, int]:
        effs = await list_effective_versions(s)
        doc_ids = [e.document_id for e in effs]
        clauses_by_doc = await fetch_clause_refs(s, doc_ids)
        top_words = await fetch_top_words(s)
        processes_by_doc = await fetch_process_links(s, doc_ids)
        manifest, pending = await build_tree(
            build_root,
            effs,
            sink,
            s,
            clauses_by_doc=clauses_by_doc,
            top_words=top_words,
            processes_by_doc=processes_by_doc,
        )
        # S-drift-2: persist the build manifest as the scan's expected-state baseline (keyed by
        # the .builds/<name> dir — commit-then-swap means an orphan row for a never-swapped build
        # is harmless; the scan verifies current's target against the newest SWAPPED row).
        # manifest_sha256 = the EXACT bytes just written (generated_at makes recompute impossible
        # — deliberate).
        manifest_bytes = (build_root / "_meta" / "manifest.json").read_bytes()
        org_id = await get_single_org_id(s)
        if org_id is None:
            logger.info("mirror.sync: no organization yet; baseline row skipped")
        else:
            s.add(
                MirrorBuild(
                    org_id=org_id,
                    build_name=build_root.name,
                    manifest=manifest,
                    manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    documents=len(effs),
                    files=sum(1 for e in manifest if "sha256" in e),
                    symlinks=sum(1 for e in manifest if "symlink_to" in e),
                )
            )
            await s.flush()
            # Keep-last-N prune — but NEVER the row `current` still points at: under a
            # persistent swap-failure mode, orphan rows pile above it and deleting it would
            # silently disable tamper detection on the still-served tree (the 4-lens fold §11.4).
            stale_ids = (
                (
                    await s.execute(
                        select(MirrorBuild.id)
                        .where(MirrorBuild.build_name != (current_target or ""))
                        .order_by(MirrorBuild.built_at.desc(), MirrorBuild.id.desc())
                        .offset(_KEEP_BUILD_ROWS)
                    )
                )
                .scalars()
                .all()
            )
            if stale_ids:
                await s.execute(delete(MirrorBuild).where(MirrorBuild.id.in_(stale_ids)))
        await s.commit()  # persist the rendition cache writes (blob rows + version FKs) + baseline
        return manifest, pending, len(effs)

    async def _build_swap_stamp(s: AsyncSession) -> tuple[list[dict[str, object]], int, int]:
        manifest, pending, count = await _build(s)  # commits: renditions + the baseline row
        atomic_swap(root, build_root)
        # Stamp swap success (pointer integrity, spec §11.1). A crash between the swap and this
        # commit self-heals: the scan treats current→newest-unswapped-row as the crash window
        # and persist_scan_results stamps it. No-op when the baseline row was skipped (no org).
        await s.execute(
            update(MirrorBuild)
            .where(MirrorBuild.build_name == build_root.name)
            .values(swapped_at=func.now())
        )
        await s.commit()
        return manifest, pending, count

    if session is not None:
        manifest, pending, count = await _build_swap_stamp(session)
    else:
        async with get_sessionmaker()() as own:
            manifest, pending, count = await _build_swap_stamp(own)

    files = sum(1 for entry in manifest if "sha256" in entry)
    symlinks = sum(1 for entry in manifest if "symlink_to" in entry)
    return MirrorSyncResult(
        documents=count, files=files, symlinks=symlinks, pending_renditions=pending
    )
