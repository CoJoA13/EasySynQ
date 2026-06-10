"""The D2+D3 mirror tamper/staleness scan (S-drift-2; doc 05 §9.1-§9.2.1, R11).

The mirror is NEVER trusted as truth: the expected state is the PG-persisted ``mirror_build``
manifest (keyed by ``current``'s actual ``.builds/<name>`` target), and the on-disk
``_meta/manifest.json`` is itself byte-verified against the build-time ``manifest_sha256``.
Divergent bytes are QUARANTINED to ``<mirror>/.quarantine/`` BEFORE any rebuild (R11 — the rebuild
prunes the old tree, so scan-first is what preserves forensic evidence); every anomaly is audited
(``MIRROR_STALE`` = known vault bytes of the same document at the wrong currency;
``MIRROR_TAMPER`` = foreign/extra/missing/symlink divergence); one ``drift_scan`` summary row per
scan. This module is split pure-core (``compare_tree``/``classify_mismatch``/``write_quarantine``
— no DB) vs orchestration (``scan_mirror``/``persist_scan_results``/``scan_and_sync``). Callers
hold ``LOCK_MIRROR_SYNC`` (scan and sync serialize — a swap can never prune a tree mid-walk).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._drift_enums import DriftScanKind, DriftScanStatus
from ...db.models._vault_enums import VersionState
from ...db.models.audit_event import AuditEvent
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.drift_scan import DriftScan
from ...db.models.mirror_build import MirrorBuild
from ..common.org import get_single_org_id
from ..common.pg_locks import LOCK_MIRROR_SYNC, holds_advisory_lock
from .mirror import MirrorSyncResult, sync_mirror
from .render import RenderSink

logger = logging.getLogger("easysynq.mirror.scan")

MANIFEST_PATH = "_meta/manifest.json"

# Doc 05 §9.1 D3 classifications (the event type rides on them: STALE → MIRROR_STALE, the rest →
# MIRROR_TAMPER).
CLASS_STALE = "STALE_REVISION"
CLASS_UNEXPECTED = "UNEXPECTED_CONTENT"
CLASS_EXTRA = "EXTRA"
CLASS_MISSING = "MISSING"
CLASS_SYMLINK = "SYMLINK_DIVERGENT"
# The `current` pointer itself diverges (missing / a real directory / a foreign target / a
# rollback to an older swapped build) — always MIRROR_TAMPER (spec §11.1).
CLASS_POINTER = "POINTER_DIVERGENT"
# Pre-classification: a digest mismatch awaiting the vault digest check (scan_mirror resolves it
# to STALE_REVISION or UNEXPECTED_CONTENT).
_CONTENT_MISMATCH = "CONTENT_MISMATCH"


@dataclasses.dataclass(slots=True)
class Finding:
    path: str
    classification: str
    expected_sha256: str | None = None
    found_sha256: str | None = None
    document_id: str | None = None
    version_id: str | None = None  # the expected entry's version — STALE excludes its own digests
    note: str | None = None
    symlink_expected: str | None = None
    symlink_found: str | None = None
    quarantine_path: str | None = None
    quarantined_sha256: str | None = None


@dataclasses.dataclass(slots=True)
class ScanReport:
    scan_id: uuid.UUID
    started_at: datetime.datetime
    baseline: str  # "ok" | "none" (EMPTY registry only — fresh install / pre-0046 upgrade)
    status: str  # "CLEAN" | "DIVERGENT" | "FAILED"
    is_current: bool
    build_name: str | None
    findings: list[Finding]
    scanned: int = 0
    error: str | None = None
    # resolve_pointer's verdict on `current` itself (spec §11.1):
    # "ok" | "none" | "selfheal" | "missing" | "rogue_dir" | "foreign" | "rollback"
    pointer: str = "ok"

    def counts(self) -> dict[str, object]:
        by: dict[str, int] = {}
        for f in self.findings:
            by[f.classification] = by.get(f.classification, 0) + 1
        # MISSING findings have no on-disk path and POINTER findings are about the `current`
        # symlink itself — neither was a walked path, so neither subtracts from `ok`.
        present_divergent = sum(
            1 for f in self.findings if f.classification not in (CLASS_MISSING, CLASS_POINTER)
        )
        out: dict[str, object] = {
            "scanned": self.scanned,
            "ok": max(self.scanned - present_divergent, 0),
            "stale": by.get(CLASS_STALE, 0),
            "tampered": sum(
                by.get(c, 0)
                for c in (
                    CLASS_UNEXPECTED,
                    CLASS_EXTRA,
                    CLASS_MISSING,
                    CLASS_SYMLINK,
                    CLASS_POINTER,
                )
            ),
            "extra": by.get(CLASS_EXTRA, 0),
            "missing": by.get(CLASS_MISSING, 0),
            "symlink_divergent": by.get(CLASS_SYMLINK, 0),
            "quarantined": sum(1 for f in self.findings if f.quarantine_path is not None),
            "errors": sum(1 for f in self.findings if f.note is not None),
            "build_name": self.build_name,
            "is_current": self.is_current,
            "baseline": self.baseline,
            "pointer": self.pointer,
            "scan_id": str(self.scan_id),
        }
        if self.error:
            out["error"] = self.error
        return out


def _now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_tree(root: Path) -> dict[str, str]:
    """Relative-posix-path → ``'file' | 'symlink'`` for everything under ``root``. Built on
    ``os.walk(followlinks=False)`` — NEVER ``rglob`` (Py3.12 follows symlinks); a symlinked dir is
    recorded as a symlink and pruned so its contents are never entered (in-tree aliases would
    double-walk, out-of-tree targets must never be read)."""
    found: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for d in list(dirnames):
            full = base / d
            if full.is_symlink():
                found[full.relative_to(root).as_posix()] = "symlink"
                dirnames.remove(d)
        for name in filenames:
            full = base / name
            kind = "symlink" if full.is_symlink() else "file"
            found[full.relative_to(root).as_posix()] = kind
    return found


def classify_mismatch(found_sha256: str, known_digests: set[str]) -> str:
    """Doc 05 §9.1 D3: known vault bytes of the SAME document (any version's source or cached
    rendition) → STALE_REVISION; anything else → UNEXPECTED_CONTENT."""
    return CLASS_STALE if found_sha256 in known_digests else CLASS_UNEXPECTED


def compare_tree(
    build_dir: Path, manifest: list[dict[str, object]], manifest_sha256: str
) -> tuple[list[Finding], int]:
    """Walk ``build_dir`` against the PG-persisted manifest. Returns (findings, paths-scanned).
    Content mismatches come back as ``_CONTENT_MISMATCH`` (the caller resolves them against the
    vault digests); everything else is final. Pure: no DB, no writes."""
    files = {str(e["path"]).replace("\\", "/"): e for e in manifest if "sha256" in e}
    links = {str(e["path"]).replace("\\", "/"): e for e in manifest if "symlink_to" in e}
    found = _walk_tree(build_dir)
    findings: list[Finding] = []

    for rel, entry in files.items():
        expected = str(entry["sha256"])
        doc_id = str(entry["document_id"]) if "document_id" in entry else None
        ver_id = str(entry["version_id"]) if "version_id" in entry else None
        kind = found.get(rel)
        if kind is None:
            findings.append(
                Finding(rel, CLASS_MISSING, expected_sha256=expected, document_id=doc_id)
            )
            continue
        if kind == "symlink":
            # A type swap (file → symlink): expected_sha256 + symlink_found convey it; `note`
            # stays reserved for the error channel feeding counts()["errors"].
            findings.append(
                Finding(
                    rel,
                    CLASS_SYMLINK,
                    expected_sha256=expected,
                    document_id=doc_id,
                    symlink_found=os.readlink(build_dir / rel),
                )
            )
            continue
        try:
            got = _hash_file(build_dir / rel)
        except OSError as exc:
            findings.append(
                Finding(
                    rel,
                    CLASS_UNEXPECTED,
                    expected_sha256=expected,
                    document_id=doc_id,
                    note=f"unreadable: {exc}",
                )
            )
            continue
        if got != expected:
            findings.append(
                Finding(
                    rel,
                    _CONTENT_MISMATCH,
                    expected_sha256=expected,
                    found_sha256=got,
                    document_id=doc_id,
                    version_id=ver_id,
                )
            )

    for rel, entry in links.items():
        target = str(entry["symlink_to"])
        kind = found.get(rel)
        if kind is None:
            findings.append(Finding(rel, CLASS_MISSING, symlink_expected=target))
        elif kind == "file":
            # A type swap (symlink → file): symlink_expected with no symlink_found conveys it.
            findings.append(Finding(rel, CLASS_SYMLINK, symlink_expected=target))
        else:
            actual = os.readlink(build_dir / rel)
            if actual != target:
                findings.append(
                    Finding(rel, CLASS_SYMLINK, symlink_expected=target, symlink_found=actual)
                )

    expected_paths = set(files) | set(links)
    for rel, kind in sorted(found.items()):
        if rel in expected_paths:
            continue
        if rel == MANIFEST_PATH:
            # The manifest is expected on disk but lives OUTSIDE its own entry list — verify it
            # byte-wise against the build-time digest (never read it as authority).
            try:
                got = _hash_file(build_dir / rel)
            except OSError as exc:
                findings.append(
                    Finding(
                        rel,
                        CLASS_UNEXPECTED,
                        expected_sha256=manifest_sha256,
                        note=f"unreadable: {exc}",
                    )
                )
                continue
            if got != manifest_sha256:
                findings.append(
                    Finding(
                        rel,
                        CLASS_UNEXPECTED,
                        expected_sha256=manifest_sha256,
                        found_sha256=got,
                    )
                )
            continue
        if kind == "symlink":
            try:
                actual_link: str | None = os.readlink(build_dir / rel)
            except OSError:
                actual_link = None
            findings.append(Finding(rel, CLASS_EXTRA, symlink_found=actual_link))
        else:
            try:
                got_extra: str | None = _hash_file(build_dir / rel)
            except OSError as exc:
                findings.append(Finding(rel, CLASS_EXTRA, note=f"unreadable: {exc}"))
                continue
            findings.append(Finding(rel, CLASS_EXTRA, found_sha256=got_extra))

    # A DELETED manifest.json must be flagged too — it lives outside its own entry list, so the
    # MISSING loop above never sees it (the 4-lens fold §11.6: only the tampered case was caught).
    if MANIFEST_PATH not in found:
        findings.append(Finding(MANIFEST_PATH, CLASS_MISSING, expected_sha256=manifest_sha256))

    return findings, len(found)


def _quarantine_dir(mirror_root: Path, scan_id: uuid.UUID) -> Path:
    """The per-scan quarantine dir, created 0o700 — users on the mirror export must never be able
    to browse tampered lookalike content (the 4-lens fold §11.6; chmod is weak on Windows — the
    production mount is Linux)."""
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    qdir = mirror_root / ".quarantine" / f"{stamp}__{scan_id.hex}"
    qdir.mkdir(parents=True, exist_ok=True)
    os.chmod(qdir.parent, 0o700)
    os.chmod(qdir, 0o700)
    return qdir


def quarantine_tree(qdir: Path, src: Path, finding: Finding) -> None:
    """Quarantine a whole foreign/rogue tree BY MOVE (same-volume rename): preserves the bytes
    exactly, takes them out of ``_prune_builds``' reach, and (for a rogue real-dir ``current``)
    unblocks the next atomic swap. A move failure is noted, never raised."""
    dest = qdir / finding.path
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        finding.quarantine_path = str(dest)
    except OSError as exc:
        finding.note = (f"{finding.note}; " if finding.note else "") + (
            f"quarantine move failed: {exc}"
        )


def write_quarantine(
    qdir: Path,
    base: Path,
    findings: list[Finding],
) -> None:
    """R11: copy divergent bytes OUT of the tree BEFORE any rebuild can prune it. Copies every
    readable divergent/extra regular file (``found_sha256`` set, final classification), resolved
    against ``base``; MISSING/symlink findings have no bytes to copy and are recorded in the index
    only. Each copy is RE-HASHED (``quarantined_sha256`` — chain of custody: the preserved bytes
    must provably match the audited ``found_sha256``). A copy failure is noted on the finding,
    never raised — quarantine must not block correction."""
    for f in findings:
        if f.found_sha256 is None or f.classification not in (
            CLASS_STALE,
            CLASS_UNEXPECTED,
            CLASS_EXTRA,
        ):
            continue
        dest = qdir / f.path
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base / f.path, dest)
            f.quarantine_path = str(dest)
            f.quarantined_sha256 = _hash_file(dest)
            if f.quarantined_sha256 != f.found_sha256:
                f.note = (f"{f.note}; " if f.note else "") + (
                    "quarantined bytes differ from the scanned digest (concurrent writer?)"
                )
        except OSError as exc:
            f.note = (f"{f.note}; " if f.note else "") + f"quarantine copy failed: {exc}"


def write_quarantine_index(
    qdir: Path, build_name: str | None, scan_id: uuid.UUID, findings: list[Finding]
) -> None:
    """The per-scan ``quarantine.json`` — every finding is recorded, even uncopyable ones."""
    index = {
        "schema": "easysynq.mirror.quarantine/1",
        "scan_id": str(scan_id),
        "build_name": build_name,
        "created_at": _now().isoformat(),
        "findings": [
            {
                "path": f.path,
                "classification": f.classification,
                "expected_sha256": f.expected_sha256,
                "found_sha256": f.found_sha256,
                "quarantined_sha256": f.quarantined_sha256,
                "symlink_expected": f.symlink_expected,
                "symlink_found": f.symlink_found,
                "quarantine_path": f.quarantine_path,
                "note": f.note,
            }
            for f in findings
        ],
    }
    (qdir / "quarantine.json").write_bytes(
        (json.dumps(index, indent=2, sort_keys=True) + "\n").encode()
    )


@dataclasses.dataclass(frozen=True, slots=True)
class PointerRow:
    """The (build_name, built_at, swapped_at) projection the pointer-integrity check needs."""

    build_name: str
    built_at: datetime.datetime
    swapped_at: datetime.datetime | None


def resolve_pointer(
    current_target: str | None, current_is_real_dir: bool, rows: list[PointerRow]
) -> tuple[str, PointerRow | None]:
    """Pure pointer-integrity matrix (spec §11.1): verify `current` against the registry, never
    trust it. Returns (pointer_state, the row to scan against or None). States: 'none' (empty
    registry — the only benign no-baseline), 'ok', 'selfheal' (the swap-then-crash window:
    current → the newest not-yet-stamped row; persist completes the bookkeeping), and the four
    MIRROR_TAMPER states 'missing' / 'rogue_dir' / 'foreign' / 'rollback'."""
    if not rows:
        return ("none", None)
    swapped = [r for r in rows if r.swapped_at is not None]
    newest_swapped = max(swapped, key=lambda r: r.built_at) if swapped else None
    if current_target is None:
        return ("rogue_dir" if current_is_real_dir else "missing", None)
    cur = next((r for r in rows if r.build_name == current_target), None)
    if cur is None:
        return ("foreign", None)
    if cur.swapped_at is None:
        if newest_swapped is None or cur.built_at >= newest_swapped.built_at:
            return ("selfheal", cur)
        return ("rollback", cur)  # an ancient never-swapped orphan resurrected under current
    if newest_swapped is not None and cur.build_name != newest_swapped.build_name:
        return ("rollback", cur)
    return ("ok", cur)


async def _pointer_rows(session: AsyncSession) -> list[PointerRow]:
    rows = (
        await session.execute(
            select(MirrorBuild.build_name, MirrorBuild.built_at, MirrorBuild.swapped_at).order_by(
                MirrorBuild.built_at
            )
        )
    ).all()
    return [PointerRow(name, built, swapped) for name, built, swapped in rows]


def _scan_builds_area(
    root: Path, registered: set[str], current_target: str | None
) -> list[Finding]:
    """Unregistered ``.builds/`` children are EXTRA → MIRROR_TAMPER: the next sync's
    ``_prune_builds`` would rmtree them UNAUDITED (spec §11.2; they get quarantined BY MOVE).
    Registered orphans (failed-swap leftovers) are benign; current's own target belongs to the
    pointer check. Mirror-root siblings stay deliberately out of scope."""
    findings: list[Finding] = []
    builds = root / ".builds"
    if not builds.is_dir():
        return findings
    for child in sorted(builds.iterdir()):
        if child.name in registered or child.name == current_target:
            continue
        findings.append(Finding(f".builds/{child.name}", CLASS_EXTRA))
    return findings


async def _known_digests(
    session: AsyncSession, document_id: uuid.UUID, exclude_version_id: uuid.UUID | None
) -> set[str]:
    """Every digest the vault knows for this document EXCEPT the expected version's own (spec
    §11.3 — doc 05's STALE is "matches an OLDER version"; same-version bytes in the wrong role,
    e.g. raw source bytes over the banded controlled-copy rendition, are TAMPER)."""
    stmt = select(DocumentVersion.source_blob_sha256, DocumentVersion.rendition_blob_sha256).where(
        DocumentVersion.document_id == document_id
    )
    if exclude_version_id is not None:
        stmt = stmt.where(DocumentVersion.id != exclude_version_id)
    rows = (await session.execute(stmt)).all()
    return {digest for row in rows for digest in row if digest}


async def _is_current(session: AsyncSession, manifest: list[dict[str, object]]) -> bool:
    """The D3 staleness backstop: does the scanned build still cover EXACTLY the live Effective
    version set? Behind-vault is NOT tamper (no audit) — it just makes the hourly task rebuild."""
    expected = {str(e["version_id"]) for e in manifest if "version_id" in e}
    live = (
        (
            await session.execute(
                select(DocumentVersion.id).where(
                    DocumentVersion.version_state == VersionState.Effective
                )
            )
        )
        .scalars()
        .all()
    )
    return expected == {str(v) for v in live}


async def scan_mirror(
    session: AsyncSession, *, mirror_path: str | os.PathLike[str] | None = None
) -> ScanReport:
    """The D2+D3 scan: verify the `current` POINTER against the registry (spec §11.1) → load the
    PG baseline → walk + classify → sweep the .builds area → QUARANTINE divergent bytes (R11,
    before any rebuild; foreign/rogue trees by MOVE). Read-only on the DB. NEVER raises — an
    infrastructure failure returns an honest FAILED report (the backup posture). Persistence is
    ``persist_scan_results``."""
    scan_id = uuid.uuid4()
    started_at = _now()
    root = Path(mirror_path) if mirror_path is not None else Path(get_settings().mirror_path)
    current = root / "current"
    current_is_real_dir = current.is_dir() and not current.is_symlink()
    try:
        current_target: str | None = Path(os.readlink(current)).name
    except OSError:
        current_target = None
    build_name = current_target
    try:
        rows = await _pointer_rows(session)
        pointer, cur = resolve_pointer(current_target, current_is_real_dir, rows)
        if pointer == "none":
            return ScanReport(
                scan_id=scan_id,
                started_at=started_at,
                baseline="none",
                status="CLEAN",
                is_current=False,
                build_name=build_name,
                findings=[],
                pointer="none",
            )

        findings: list[Finding] = []
        tree_findings: list[Finding] = []
        scanned = 0
        is_current = False
        build_dir: Path | None = None

        if pointer in ("missing", "rogue_dir", "foreign"):
            findings.append(Finding("current", CLASS_POINTER, symlink_found=current_target))
        if cur is not None:
            row = (
                await session.execute(
                    select(MirrorBuild).where(MirrorBuild.build_name == cur.build_name)
                )
            ).scalar_one()
            build_dir = root / ".builds" / cur.build_name
            if build_dir.is_dir():
                tree_findings, scanned = compare_tree(build_dir, row.manifest, row.manifest_sha256)
                for f in tree_findings:
                    if f.classification == _CONTENT_MISMATCH:
                        known: set[str] = set()
                        if f.document_id is not None and f.found_sha256 is not None:
                            known = await _known_digests(
                                session,
                                uuid.UUID(f.document_id),
                                uuid.UUID(f.version_id) if f.version_id else None,
                            )
                        f.classification = classify_mismatch(f.found_sha256 or "", known)
                findings.extend(tree_findings)
            if pointer == "rollback":
                # The per-file pass above covered the tree against ITS OWN row's manifest (known
                # old vault bytes — no wholesale quarantine needed); this is the pointer event.
                findings.append(Finding("current", CLASS_POINTER, symlink_found=current_target))
            if pointer in ("ok", "selfheal"):
                is_current = await _is_current(session, row.manifest)

        builds_findings = _scan_builds_area(root, {r.build_name for r in rows}, current_target)
        findings.extend(builds_findings)

        if findings:
            qdir = _quarantine_dir(root, scan_id)
            if build_dir is not None and build_dir.is_dir():
                write_quarantine(qdir, build_dir, tree_findings)
            for f in builds_findings:
                quarantine_tree(qdir, root / f.path, f)
            if pointer == "rogue_dir":
                pf = next(f for f in findings if f.classification == CLASS_POINTER)
                quarantine_tree(qdir, current, pf)  # also unblocks the next atomic swap
            elif pointer == "foreign" and current_target is not None:
                src = root / ".builds" / current_target
                if src.is_dir():
                    pf = next(f for f in findings if f.classification == CLASS_POINTER)
                    quarantine_tree(qdir, src, pf)
            write_quarantine_index(qdir, build_name, scan_id, findings)

        return ScanReport(
            scan_id=scan_id,
            started_at=started_at,
            baseline="ok",
            status="DIVERGENT" if findings else "CLEAN",
            is_current=is_current,
            build_name=build_name,
            findings=findings,
            scanned=scanned,
            pointer=pointer,
        )
    except Exception as exc:  # an infra failure is an honest FAILED, never a raise
        logger.exception("mirror.scan.failed")
        return ScanReport(
            scan_id=scan_id,
            started_at=started_at,
            baseline="ok",
            status="FAILED",
            is_current=False,
            build_name=build_name,
            findings=[],
            error=str(exc),
        )


async def persist_scan_results(
    session: AsyncSession, report: ScanReport, *, rebuild_triggered: bool, triggered_by: str
) -> bool:
    """One txn: a ``MIRROR_STALE``/``MIRROR_TAMPER`` audit event per anomaly (doc-attributable →
    object_type=document + scope_ref=identifier, the S-ing-5 precedent; else config keyed on the
    org) + the ``drift_scan`` summary row + the selfheal ``swapped_at`` stamp (spec §11.1).
    Quarantine files are already durably written (a crash between leaves bytes-without-events;
    the divergence is still on disk, so the next scan re-detects — self-healing). NO
    per-clean-scan audit event (hourly CLEAN events would spam the trail) — but EVERY scan gets
    its summary row (the row-per-scan contract). Returns success: a failure is logged, never
    raised, and the caller defers the rebuild when findings would otherwise go unrecorded
    (spec §11.5)."""
    if report.status == "FAILED":
        await session.rollback()  # the failed scan may have poisoned the txn
    try:
        org_id = await get_single_org_id(session)
        if org_id is None:
            logger.warning("mirror.scan: no organization yet; scan results not persisted")
            return False
        finished_at = _now()
        if report.pointer == "selfheal" and report.build_name is not None:
            # The swap-then-crash window: complete the crashed bookkeeping (the scan itself
            # stays read-only; an attacker cannot mint registry rows without DB write access).
            await session.execute(
                update(MirrorBuild)
                .where(
                    MirrorBuild.build_name == report.build_name,
                    MirrorBuild.swapped_at.is_(None),
                )
                .values(swapped_at=func.now())
            )
        for f in report.findings:
            event_type = (
                EventType.MIRROR_STALE
                if f.classification == CLASS_STALE
                else EventType.MIRROR_TAMPER
            )
            object_type, object_id, scope_ref = AuditObjectType.config, org_id, None
            if f.document_id is not None:
                doc_uuid = uuid.UUID(f.document_id)
                # Column-select, NOT session.get — a full entity would sit STALE in the identity
                # map when this same session's rebuild re-reads documents (the 4-lens fold §11.6).
                identifier = (
                    await session.execute(
                        select(DocumentedInformation.identifier).where(
                            DocumentedInformation.id == doc_uuid
                        )
                    )
                ).scalar_one_or_none()
                object_type, object_id, scope_ref = (
                    AuditObjectType.document,
                    doc_uuid,
                    identifier,
                )
            after: dict[str, object] = {
                "path": f.path,
                "classification": f.classification,
                "expected_sha256": f.expected_sha256,
                "found_sha256": f.found_sha256,
                "quarantine_path": f.quarantine_path,
                "quarantined_sha256": f.quarantined_sha256,
                "build_name": report.build_name,
                "scan_id": str(report.scan_id),
            }
            if f.classification == CLASS_POINTER:
                after["pointer_state"] = report.pointer
            if f.note:
                after["note"] = f.note
            if f.symlink_expected:
                after["symlink_expected"] = f.symlink_expected
            if f.symlink_found:
                after["symlink_found"] = f.symlink_found
            session.add(
                AuditEvent(
                    org_id=org_id,
                    occurred_at=finished_at,
                    actor_id=None,
                    actor_type=ActorType.system,
                    event_type=event_type,
                    object_type=object_type,
                    object_id=object_id,
                    scope_ref=scope_ref,
                    after=after,
                )
            )
        session.add(
            DriftScan(
                org_id=org_id,
                kind=DriftScanKind.MIRROR,
                started_at=report.started_at,
                finished_at=finished_at,
                status=DriftScanStatus(report.status),
                counts={**report.counts(), "rebuild_triggered": rebuild_triggered},
                triggered_by=triggered_by,
            )
        )
        await session.commit()
        return True
    except Exception:  # persistence must never raise into the pipeline
        logger.exception("mirror.scan: failed to persist scan results")
        await session.rollback()
        return False


async def scan_and_sync(
    session: AsyncSession,
    *,
    rebuild: Literal["always", "if_needed"],
    triggered_by: str,
    mirror_path: str | os.PathLike[str] | None = None,
    render_sink: RenderSink | None = None,
) -> tuple[ScanReport, MirrorSyncResult | None]:
    """The owner-fork §0.1 pipeline: scan-first (quarantine + audit + summary), THEN the rebuild
    as the vault-wins correction. ``always`` = the sync path (R11's per-sync leg; rebuilds even on
    a FAILED scan — a broken scan must never block correction). ``if_needed`` = the hourly path
    (rebuilds on DIVERGENT / behind-vault / no-baseline; NOT on FAILED — a scan failure is not
    evidence the mirror is wrong, and the nightly sync remains the convergence backstop). Two
    §11.5 guards: unpersisted FINDINGS defer the rebuild (it would erase the on-disk evidence the
    next scan needs to re-detect and audit — and a broken PG fails the rebuild anyway), and after
    any failure the advisory-lock ownership is re-verified (a dropped connection frees the
    session-level lock silently). The caller holds ``LOCK_MIRROR_SYNC``."""
    report = await scan_mirror(session, mirror_path=mirror_path)
    needs = report.status == "DIVERGENT" or report.baseline == "none" or not report.is_current
    do_rebuild = rebuild == "always" or (report.status != "FAILED" and needs)
    persisted = await persist_scan_results(
        session, report, rebuild_triggered=do_rebuild, triggered_by=triggered_by
    )
    if do_rebuild and not persisted and report.findings:
        logger.error(
            "mirror.scan: findings not persisted; deferring the rebuild to preserve re-detection",
            extra={"extra_fields": report.counts()},
        )
        do_rebuild = False
    if do_rebuild and (report.status == "FAILED" or not persisted):
        if not await holds_advisory_lock(session, LOCK_MIRROR_SYNC):
            logger.error("mirror.scan: advisory lock lost; skipping the rebuild this tick")
            return report, None
    result: MirrorSyncResult | None = None
    if do_rebuild:
        result = await sync_mirror(
            mirror_path=mirror_path, render_sink=render_sink, session=session
        )
    return report, result
