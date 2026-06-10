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
