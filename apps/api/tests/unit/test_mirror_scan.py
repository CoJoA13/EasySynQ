"""S-drift-2 unit proofs — the pure compare/classify/quarantine core (no DB).

The classification matrix (doc 05 §9.1 D2/D3): content mismatch (pre-classified, resolved against
vault digests), EXTRA, MISSING, SYMLINK_DIVERGENT (retarget + both type-swaps), the manifest-tamper
self-check, the never-follow-symlinks walk, unreadable-file findings, quarantine layout + failure
tolerance, and the counts() math. Symlink-creating tests may need Windows Developer Mode locally;
they run in Linux CI regardless.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from easysynq_api.services.vault import mirror_scan as scan_mod
from easysynq_api.services.vault.mirror_scan import (
    _CONTENT_MISMATCH,
    CLASS_EXTRA,
    CLASS_MISSING,
    CLASS_STALE,
    CLASS_SYMLINK,
    CLASS_UNEXPECTED,
    Finding,
    _quarantine_dir,
    classify_mismatch,
    compare_tree,
    quarantine_tree,
    write_quarantine,
    write_quarantine_index,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_build(
    build: Path,
    files: dict[str, bytes],
    links: dict[str, str] | None = None,
    **extra_by_path: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """Lay a fabricated build tree + its manifest (the build_tree output shape) into ``build``.
    Returns (manifest entry list, manifest_sha256-of-the-on-disk-manifest.json)."""
    manifest: list[dict[str, Any]] = []
    for rel, data in files.items():
        p = build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        entry: dict[str, Any] = {"path": rel, "sha256": _sha(data), "size_bytes": len(data)}
        entry.update(extra_by_path.get(rel, {}))
        manifest.append(entry)
    for rel, target in (links or {}).items():
        p = build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, p, target_is_directory=True)
        manifest.append({"path": rel, "symlink_to": target})
    doc = {
        "schema": "easysynq.mirror.manifest/1",
        "generated_at": "2026-06-09T00:00:00+00:00",
        "files": sorted(manifest, key=lambda e: str(e["path"])),
    }
    raw = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
    (build / "_meta").mkdir(parents=True, exist_ok=True)
    (build / "_meta" / "manifest.json").write_bytes(raw)
    return manifest, _sha(raw)


def _by_path(findings: list[Finding]) -> dict[str, Finding]:
    return {f.path: f for f in findings}


def test_clean_tree_no_findings(tmp_path: Path) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"DO/08-Operation/SOP_RevA/source.pdf": b"PDF"})
    findings, scanned = compare_tree(build, manifest, msha)
    assert findings == []
    assert scanned == 2  # the source file + _meta/manifest.json


def test_content_mismatch_is_pre_classified_with_doc_attribution(tmp_path: Path) -> None:
    build = tmp_path / "b"
    doc_id = str(uuid.uuid4())
    manifest, msha = _make_build(
        build,
        {"a/source.pdf": b"GOOD"},
        **{"a/source.pdf": {"document_id": doc_id, "version_id": str(uuid.uuid4())}},
    )
    (build / "a" / "source.pdf").write_bytes(b"EVIL")
    findings, _ = compare_tree(build, manifest, msha)
    f = _by_path(findings)["a/source.pdf"]
    assert f.classification == _CONTENT_MISMATCH  # resolved against vault digests by scan_mirror
    assert f.expected_sha256 == _sha(b"GOOD")
    assert f.found_sha256 == _sha(b"EVIL")
    assert f.document_id == doc_id


def test_classify_mismatch_stale_vs_unexpected() -> None:
    known = {_sha(b"OLD-REV")}
    assert classify_mismatch(_sha(b"OLD-REV"), known) == CLASS_STALE
    assert classify_mismatch(_sha(b"FOREIGN"), known) == CLASS_UNEXPECTED
    assert classify_mismatch(_sha(b"ANYTHING"), set()) == CLASS_UNEXPECTED


def test_extra_and_missing_files(tmp_path: Path) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"a/keep.pdf": b"K", "a/gone.pdf": b"G"})
    (build / "a" / "gone.pdf").unlink()
    (build / "STRAY.txt").write_bytes(b"not from the vault")
    findings, _ = compare_tree(build, manifest, msha)
    by = _by_path(findings)
    assert by["a/gone.pdf"].classification == CLASS_MISSING
    assert by["STRAY.txt"].classification == CLASS_EXTRA
    assert by["STRAY.txt"].found_sha256 == _sha(b"not from the vault")
    assert len(findings) == 2


def test_symlink_retarget_and_type_swaps(tmp_path: Path) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(
        build,
        {"real/doc/source.pdf": b"P", "swapped-to-link.txt": b"T"},
        links={"PLAN/04-Context/doc": "../../real/doc", "retargeted": "../real/doc"},
    )
    # retarget one symlink; swap a file→symlink; swap a symlink→file
    (build / "retargeted").unlink()
    os.symlink("real", build / "retargeted", target_is_directory=True)
    (build / "swapped-to-link.txt").unlink()
    os.symlink("real/doc/source.pdf", build / "swapped-to-link.txt")
    link_path = build / "PLAN" / "04-Context" / "doc"
    link_path.unlink()
    link_path.write_bytes(b"now a file")
    findings, _ = compare_tree(build, manifest, msha)
    by = _by_path(findings)
    assert by["retargeted"].classification == CLASS_SYMLINK
    assert by["retargeted"].symlink_expected == "../real/doc"
    assert by["retargeted"].symlink_found == "real"
    assert by["swapped-to-link.txt"].classification == CLASS_SYMLINK
    assert by["PLAN/04-Context/doc"].classification == CLASS_SYMLINK
    assert len(findings) == 3


def test_manifest_tamper_detected_via_stored_digest(tmp_path: Path) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"a/source.pdf": b"P"})
    mpath = build / "_meta" / "manifest.json"
    mpath.write_bytes(mpath.read_bytes().replace(b"easysynq", b"tampered"))
    findings, _ = compare_tree(build, manifest, msha)
    f = _by_path(findings)["_meta/manifest.json"]
    assert f.classification == CLASS_UNEXPECTED
    assert f.expected_sha256 == msha


def test_walker_never_follows_symlinks(tmp_path: Path) -> None:
    """A symlinked dir's contents must NOT be re-walked (py3.12 rglob would); an out-of-tree
    symlink target must never be entered."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"OUTSIDE")
    build = tmp_path / "b"
    manifest, msha = _make_build(
        build, {"real/doc/source.pdf": b"P"}, links={"alias/doc": "../real/doc"}
    )
    os.symlink(outside, build / "escape", target_is_directory=True)
    findings, _scanned = compare_tree(build, manifest, msha)
    by = _by_path(findings)
    assert set(by) == {"escape"}  # the extra symlink itself — never its contents
    assert by["escape"].classification == CLASS_EXTRA
    assert not any("secret" in f.path or "alias/doc/" in f.path for f in findings)


def test_unreadable_file_is_a_tamper_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"a/source.pdf": b"P"})

    def _boom(path: Path) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(scan_mod, "_hash_file", _boom)
    findings, _ = compare_tree(build, manifest, msha)
    f = _by_path(findings)["a/source.pdf"]
    assert f.classification == CLASS_UNEXPECTED
    assert f.note is not None and "unreadable" in f.note


def test_quarantine_copies_divergent_and_extra_only(tmp_path: Path) -> None:
    mirror_root = tmp_path / "m"
    build = mirror_root / ".builds" / "abc"
    _manifest, _msha = _make_build(build, {"a/source.pdf": b"GOOD"})
    (build / "a" / "source.pdf").write_bytes(b"EVIL")
    (build / "STRAY.txt").write_bytes(b"STRAY")
    findings = [
        Finding("a/source.pdf", CLASS_UNEXPECTED, _sha(b"GOOD"), _sha(b"EVIL")),
        Finding("STRAY.txt", CLASS_EXTRA, None, _sha(b"STRAY")),
        Finding("gone.pdf", CLASS_MISSING, _sha(b"G"), None),
        Finding("link", CLASS_SYMLINK, symlink_expected="../a", symlink_found="../b"),
    ]
    scan_id = uuid.uuid4()
    qdir = _quarantine_dir(mirror_root, scan_id)
    write_quarantine(qdir, build, findings)
    write_quarantine_index(qdir, "abc", scan_id, findings)
    qdirs = list((mirror_root / ".quarantine").iterdir())
    assert len(qdirs) == 1 and scan_id.hex in qdirs[0].name
    assert (qdirs[0] / "a" / "source.pdf").read_bytes() == b"EVIL"
    assert (qdirs[0] / "STRAY.txt").read_bytes() == b"STRAY"
    assert not (qdirs[0] / "gone.pdf").exists()
    index = json.loads((qdirs[0] / "quarantine.json").read_text())
    assert index["build_name"] == "abc" and index["scan_id"] == str(scan_id)
    assert len(index["findings"]) == 4  # ALL findings recorded, even uncopyable ones
    assert findings[0].quarantine_path is not None  # stamped back for the audit payload
    assert findings[0].quarantined_sha256 == _sha(b"EVIL")  # chain of custody: re-hashed copy
    assert findings[2].quarantine_path is None


def test_quarantine_copy_failure_is_noted_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror_root = tmp_path / "m"
    build = mirror_root / ".builds" / "abc"
    _manifest, _msha = _make_build(build, {"a/source.pdf": b"GOOD"})
    findings = [Finding("a/source.pdf", CLASS_UNEXPECTED, _sha(b"GOOD"), _sha(b"EVIL"))]

    def _boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(scan_mod.shutil, "copy2", _boom)
    qdir = _quarantine_dir(mirror_root, uuid.uuid4())
    write_quarantine(qdir, build, findings)  # must not raise
    assert findings[0].quarantine_path is None
    assert findings[0].note is not None and "quarantine copy failed" in findings[0].note


def test_manifest_deleted_is_a_missing_finding(tmp_path: Path) -> None:
    """A DELETED manifest.json is flagged, not silent (the tampered case alone is asymmetric)."""
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"a/source.pdf": b"P"})
    (build / "_meta" / "manifest.json").unlink()
    findings, _ = compare_tree(build, manifest, msha)
    f = _by_path(findings)["_meta/manifest.json"]
    assert f.classification == CLASS_MISSING
    assert f.expected_sha256 == msha


def test_quarantine_tree_moves_bytes_out(tmp_path: Path) -> None:
    """A foreign/rogue tree is quarantined BY MOVE — bytes preserved exactly, source gone (so
    _prune_builds can never destroy it and a rogue `current` dir no longer blocks the swap)."""
    mirror_root = tmp_path / "m"
    feral = mirror_root / ".builds" / "feral"
    (feral / "deep").mkdir(parents=True)
    (feral / "deep" / "payload.bin").write_bytes(b"PLANTED")
    finding = Finding(".builds/feral", CLASS_EXTRA)
    qdir = _quarantine_dir(mirror_root, uuid.uuid4())
    quarantine_tree(qdir, feral, finding)
    assert not feral.exists()  # moved, not copied
    assert (qdir / ".builds" / "feral" / "deep" / "payload.bin").read_bytes() == b"PLANTED"
    assert finding.quarantine_path is not None
