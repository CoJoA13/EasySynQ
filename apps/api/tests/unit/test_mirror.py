"""S7 + S9b unit proofs — the mirror's atomic symlink-swap, render seam, and the clause-aligned
placement / cross-clause symlinks (no DB)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from easysynq_api.services.vault import mirror as mirror_mod
from easysynq_api.services.vault.mirror import (
    ClauseRef,
    EffectiveDoc,
    _placement_dirs,
    atomic_swap,
    build_tree,
)
from easysynq_api.services.vault.render import (
    LoggingRenderSink,
    RenderRequest,
    RenderResult,
    RenderStatus,
)

# A fixed framework id + the seven top-level ISO words, mirroring fetch_top_words output, so the
# pure placement tests don't need a DB. (The integration suite proves the real query.)
_FW = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_TOP_WORDS = {
    (_FW, "4"): "Context",
    (_FW, "5"): "Leadership",
    (_FW, "6"): "Planning",
    (_FW, "7"): "Support",
    (_FW, "8"): "Operation",
    (_FW, "9"): "Performance",
    (_FW, "10"): "Improvement",
}


def _ref(number: str, phase: str, *, title: str = "X", star: bool = False) -> ClauseRef:
    return ClauseRef(
        number=number, pdca_phase=phase, title=title, is_mandatory_star=star, framework_id=_FW
    )


def _doc_dirname() -> str:
    return "SOP-PUR-001_Rev A"  # _safe(f"{identifier}_{revision_label}") for the _eff() default


def _make_build(mirror: Path, name: str) -> Path:
    build = mirror / ".builds" / name
    build.mkdir(parents=True)
    (build / "marker.txt").write_text(name)
    return build


def _eff(**overrides: Any) -> EffectiveDoc:
    fields: dict[str, Any] = {
        "identifier": "SOP-PUR-001",
        "title": "Purchasing Procedure",
        "revision_label": "Rev A",
        "change_significance": "MAJOR",
        "change_reason": "initial",
        "effective_from": None,
        "owner_user_id": uuid.uuid4(),
        "owner_display": "p.author",
        "classification": "Internal",
        "source_sha256": "de" * 32,  # a real 32-byte sha256 hex (the verify token mints over it)
        "mime_type": "application/pdf",
        "size_bytes": 3,
        "bucket": "documents",
        "object_key": "deadbeef",
        "document_id": uuid.uuid4(),
        "version_id": uuid.uuid4(),
        "org_id": uuid.uuid4(),
        "rendition_blob_sha256": None,
    }
    fields.update(overrides)
    return EffectiveDoc(**fields)


_GENERATED = {"metadata.json", "CHANGELOG.md", "INDEX.md", "manifest.json"}


def _only_source(build: Path) -> Path:
    """The single source file anywhere under ``build`` — it now nests under ``{PHASE}/{NN}-Word/``
    or ``_unmapped/`` (S9b), so descend rather than assume a top-level doc dir. Excludes the
    generated metadata/changelog/index/manifest and any symlink."""
    sources = [
        f
        for f in build.rglob("*")
        if f.is_file() and not f.is_symlink() and f.name not in _GENERATED
    ]
    assert len(sources) == 1, [str(f) for f in sources]
    return sources[0]


def test_atomic_swap_repoints_current_and_prunes(tmp_path: Path) -> None:
    """A swap repoints ``current`` at the new build and prunes the prior one."""
    mirror = tmp_path / "m"
    atomic_swap(mirror, _make_build(mirror, "b1"))
    assert os.readlink(mirror / "current") == os.path.join(".builds", "b1")
    assert (mirror / "current" / "marker.txt").read_text() == "b1"

    atomic_swap(mirror, _make_build(mirror, "b2"))
    assert os.readlink(mirror / "current") == os.path.join(".builds", "b2")
    assert (mirror / "current" / "marker.txt").read_text() == "b2"
    assert not (mirror / ".builds" / "b1").exists()  # prior build pruned


def test_mirror_atomic_swap_no_partial_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the swap rename fails, ``current`` still points at the prior build — never a partial or
    half-written tree (the invariant the AC#2 re-sync relies on)."""
    mirror = tmp_path / "m"
    atomic_swap(mirror, _make_build(mirror, "b1"))
    b2 = _make_build(mirror, "b2")

    def _boom(src: object, dst: object) -> None:
        raise OSError("injected swap failure")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="injected swap failure"):
        atomic_swap(mirror, b2)

    assert os.readlink(mirror / "current") == os.path.join(".builds", "b1")  # unchanged
    assert (mirror / "current" / "marker.txt").read_text() == "b1"
    assert not list(mirror.glob(".current.*.tmp"))  # the temp symlink is cleaned up on failure


async def test_build_tree_overwrites_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build_tree rewrites a pre-existing (tampered) file IN PLACE — so the AC#2 autocorrect would
    hold even if a future refactor built into a reused dir instead of a fresh one. This directly
    defeats a 'skip files that already exist' writer, which the fresh-uuid + swap path cannot."""

    async def _fetch(object_key: str, *, bucket: str | None = None) -> bytes:
        return b"VAULT-BYTES"

    monkeypatch.setattr(mirror_mod.storage, "fetch_bytes", _fetch)
    build = tmp_path / "b"
    build.mkdir()
    eff = _eff()

    await build_tree(build, [eff], LoggingRenderSink())
    source = _only_source(build)
    assert source.read_bytes() == b"VAULT-BYTES"
    # The no-op sink → PENDING: source bytes + render_status="pending", no R26 flag.
    meta = json.loads((source.parent / "metadata.json").read_text())
    assert meta["render_status"] == "pending"
    assert "no_controlled_rendition" not in meta

    _only_source(build).write_bytes(b"TAMPERED")  # drift, in a dir that already exists
    await build_tree(build, [eff], LoggingRenderSink())  # rebuild into the SAME dir
    assert _only_source(build).read_bytes() == b"VAULT-BYTES"  # overwritten in place


async def test_build_tree_rendered_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rendered branch (a sink returning PDF bytes) writes a .pdf source + render_status
    'rendered' — guards the otherwise-dead S7b path from bit-rotting before the renderer lands."""

    async def _fetch(object_key: str, *, bucket: str | None = None) -> bytes:
        return b"DOCX-SOURCE"

    monkeypatch.setattr(mirror_mod.storage, "fetch_bytes", _fetch)

    class _PdfSink:
        async def render(self, request: RenderRequest, source_bytes: bytes) -> RenderResult:
            return RenderResult.rendered(b"%PDF-1.7 rendered")

    build = tmp_path / "b"
    build.mkdir()
    # session=None → render still writes the .pdf, but the rendition cache (blob/FK) is skipped.
    _, pending = await build_tree(build, [_eff()], _PdfSink())

    assert pending == 0
    source = _only_source(build)
    assert source.suffix == ".pdf"
    assert source.read_bytes() == b"%PDF-1.7 rendered"
    meta = json.loads((source.parent / "metadata.json").read_text())
    assert meta["render_status"] == "rendered"
    assert "no_controlled_rendition" not in meta


async def test_build_tree_non_renderable_marks_r26(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NON_RENDERABLE result → source bytes + render_status 'unrenderable' + the R26 flag
    (distinct from 'pending')."""

    async def _fetch(object_key: str, *, bucket: str | None = None) -> bytes:
        return b"CAD-SOURCE"

    monkeypatch.setattr(mirror_mod.storage, "fetch_bytes", _fetch)

    class _NonRenderableSink:
        async def render(self, request: RenderRequest, source_bytes: bytes) -> RenderResult:
            return RenderResult.non_renderable()

    build = tmp_path / "b"
    build.mkdir()
    await build_tree(build, [_eff(mime_type="application/octet-stream")], _NonRenderableSink())
    source = _only_source(build)
    assert source.read_bytes() == b"CAD-SOURCE"  # source kept (no PDF)
    meta = json.loads((source.parent / "metadata.json").read_text())
    assert meta["render_status"] == "unrenderable"
    assert meta["no_controlled_rendition"] is True


async def test_logging_render_sink_defers_to_pending() -> None:
    """The no-op default render sink returns PENDING — the mirror falls back to source bytes."""
    result = await LoggingRenderSink().render(
        RenderRequest(
            identifier="SOP-PUR-001",
            title="Purchasing Procedure",
            revision_label="Rev A",
            effective_from=None,
            classification="Internal",
            copy_status="CONTROLLED COPY",
            owner="p.author",
            mime_type="application/pdf",
            source_filename="x.pdf",
            version_id=uuid.uuid4(),
        ),
        b"some-bytes",
    )
    assert result.status is RenderStatus.PENDING
    assert result.pdf is None


# --- S9b: clause-aligned placement (the pure _placement_dirs function) -------------------------


def test_placement_single_clause() -> None:
    primary, others = _placement_dirs([_ref("8.4", "DO")], _TOP_WORDS)
    assert primary == "DO/08-Operation"
    assert others == []


def test_placement_dedup_same_top_level_bucket() -> None:
    """Two clauses under the same top-level (8.4 + 8.5.2) collapse to one folder, no symlink."""
    primary, others = _placement_dirs([_ref("8.4", "DO"), _ref("8.5.2", "DO")], _TOP_WORDS)
    assert primary == "DO/08-Operation"
    assert others == []


def test_placement_clause7_split_two_phases() -> None:
    """The clause-7 PLAN/DO split: 7.2 (PLAN) + 7.5 (DO) → primary under PLAN, symlink under DO."""
    primary, others = _placement_dirs([_ref("7.5", "DO"), _ref("7.2", "PLAN")], _TOP_WORDS)
    assert primary == "PLAN/07-Support"  # 7.2 is numerically lower
    assert others == ["DO/07-Support"]


def test_placement_primary_is_numeric_not_lexical() -> None:
    """4.1 < 10.3 numerically (not lexically, where '10' < '4') — the #1 silent-bug trap."""
    primary, others = _placement_dirs([_ref("10.3", "ACT"), _ref("4.1", "PLAN")], _TOP_WORDS)
    assert primary == "PLAN/04-Context"
    assert others == ["ACT/10-Improvement"]


def test_placement_other_dirs_canonical_phase_order() -> None:
    """other_dirs is ordered PLAN<DO<CHECK<ACT (then top number), not alphabetical."""
    refs = [_ref("9.1", "CHECK"), _ref("4.1", "PLAN"), _ref("8.1", "DO"), _ref("10.2", "ACT")]
    primary, others = _placement_dirs(refs, _TOP_WORDS)
    assert primary == "PLAN/04-Context"  # 4.1 lowest
    assert others == ["DO/08-Operation", "CHECK/09-Performance", "ACT/10-Improvement"]


def test_placement_empty_refs_unmapped() -> None:
    assert _placement_dirs([], _TOP_WORDS) == ("_unmapped", [])


def test_placement_zero_pads_top_number() -> None:
    primary, _ = _placement_dirs([_ref("4", "PLAN")], _TOP_WORDS)
    assert primary == "PLAN/04-Context"


# --- S9b: build_tree places real bytes + cross-clause symlinks --------------------------------


async def _build_with_clauses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refs: list[ClauseRef],
    payload: bytes = b"BYTES",
) -> tuple[Path, EffectiveDoc, list[dict[str, Any]]]:
    async def _fetch(object_key: str, *, bucket: str | None = None) -> bytes:
        return payload

    monkeypatch.setattr(mirror_mod.storage, "fetch_bytes", _fetch)
    build = tmp_path / "b"
    build.mkdir()
    eff = _eff()
    manifest, _ = await build_tree(
        build,
        [eff],
        LoggingRenderSink(),
        clauses_by_doc={eff.document_id: refs},
        top_words=_TOP_WORDS,
    )
    return build, eff, manifest


async def test_build_tree_places_real_bytes_under_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build, _, _ = await _build_with_clauses(tmp_path, monkeypatch, [_ref("8.4", "DO")])
    doc_dir = build / "DO" / "08-Operation" / _doc_dirname()
    assert doc_dir.is_dir() and not doc_dir.is_symlink()
    assert _only_source(build).read_bytes() == b"BYTES"
    meta = json.loads((doc_dir / "metadata.json").read_text())
    assert [c["number"] for c in meta["clauses"]] == ["8.4"]


async def test_build_tree_symlinks_into_other_clause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build, _, _ = await _build_with_clauses(
        tmp_path, monkeypatch, [_ref("4.1", "PLAN"), _ref("8.1", "DO")], b"SHARED"
    )
    dirname = _doc_dirname()
    real = build / "PLAN" / "04-Context" / dirname
    link = build / "DO" / "08-Operation" / dirname
    assert real.is_dir() and not real.is_symlink()
    assert link.is_symlink()
    target = link.readlink()
    assert not target.is_absolute() and str(target).startswith("..")  # relative, traverses up
    assert link.resolve() == real.resolve()  # resolves to the real folder
    assert (link / "metadata.json").exists()  # readable through the link
    # bytes stored once: real source + (the symlink isn't a separate file).
    assert _only_source(build).read_bytes() == b"SHARED"


async def test_build_tree_symlink_target_stays_in_build_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build, _, _ = await _build_with_clauses(
        tmp_path, monkeypatch, [_ref("4.1", "PLAN"), _ref("8.1", "DO")]
    )
    link = build / "DO" / "08-Operation" / _doc_dirname()
    assert link.resolve().is_relative_to(build.resolve())


async def test_build_tree_manifest_distinguishes_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, manifest = await _build_with_clauses(
        tmp_path, monkeypatch, [_ref("4.1", "PLAN"), _ref("8.1", "DO")]
    )
    symlinks = [e for e in manifest if "symlink_to" in e]
    files = [e for e in manifest if "sha256" in e]
    assert len(symlinks) == 1
    assert symlinks[0]["path"].startswith("DO/08-Operation/")
    assert symlinks[0]["symlink_to"].startswith("..") and "sha256" not in symlinks[0]
    assert files and all("size_bytes" in f and "symlink_to" not in f for f in files)


async def test_build_tree_unmapped_doc_lands_in_unmapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build, _, _ = await _build_with_clauses(tmp_path, monkeypatch, [])
    assert (build / "_unmapped" / _doc_dirname()).is_dir()


async def test_build_tree_fresh_dir_only_remap_into_reused_dir_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documents the fresh-dir-only contract: rebuilding into a REUSED dir after the primary clause
    flips turns a path that was a real dir into a symlink target → FileExistsError. Production
    builds into a fresh ``.builds/<uuid>`` and swaps, so this never bites there."""

    async def _fetch(object_key: str, *, bucket: str | None = None) -> bytes:
        return b"BYTES"

    monkeypatch.setattr(mirror_mod.storage, "fetch_bytes", _fetch)
    build = tmp_path / "b"
    build.mkdir()
    eff = _eff()
    # Build 1: mapped to 8.1 only → real dir at DO/08-Operation.
    await build_tree(
        build,
        [eff],
        LoggingRenderSink(),
        clauses_by_doc={eff.document_id: [_ref("8.1", "DO")]},
        top_words=_TOP_WORDS,
    )
    # Build 2 into the SAME dir: now also mapped to 4.1 → primary flips to PLAN/04-Context, and a
    # symlink is attempted at DO/08-Operation where a real dir still exists.
    with pytest.raises(OSError):
        await build_tree(
            build,
            [eff],
            LoggingRenderSink(),
            clauses_by_doc={eff.document_id: [_ref("4.1", "PLAN"), _ref("8.1", "DO")]},
            top_words=_TOP_WORDS,
        )
