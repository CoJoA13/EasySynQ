"""S7 unit proofs — the mirror's atomic symlink-swap, in-place rebuild, and render seam (no DB)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from easysynq_api.services.vault import mirror as mirror_mod
from easysynq_api.services.vault.mirror import EffectiveDoc, atomic_swap, build_tree
from easysynq_api.services.vault.render import LoggingRenderSink, RenderRequest


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
        "classification": "Internal",
        "source_sha256": "deadbeef",
        "mime_type": "application/pdf",
        "size_bytes": 3,
        "bucket": "documents",
        "object_key": "deadbeef",
    }
    fields.update(overrides)
    return EffectiveDoc(**fields)


def _only_source(build: Path) -> Path:
    doc_dir = next(p for p in build.iterdir() if p.is_dir() and p.name != "_meta")
    sources = [f for f in doc_dir.iterdir() if f.name not in ("metadata.json", "CHANGELOG.md")]
    assert len(sources) == 1, [f.name for f in sources]
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
    assert _only_source(build).read_bytes() == b"VAULT-BYTES"

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
        def render(self, request: RenderRequest, source_bytes: bytes) -> bytes:
            return b"%PDF-1.7 rendered"

    build = tmp_path / "b"
    build.mkdir()
    _, pending = await build_tree(build, [_eff()], _PdfSink())

    assert pending == 0
    source = _only_source(build)
    assert source.suffix == ".pdf"
    assert source.read_bytes() == b"%PDF-1.7 rendered"
    meta = json.loads((source.parent / "metadata.json").read_text())
    assert meta["render_status"] == "rendered"


def test_logging_render_sink_defers() -> None:
    """The default S7 render sink renders nothing — the mirror falls back to source bytes."""
    request = RenderRequest(
        identifier="SOP-PUR-001",
        title="Purchasing Procedure",
        revision_label="Rev A",
        effective_from=None,
        classification="Internal",
        copy_status="CONTROLLED COPY",
        mime_type="application/pdf",
        source_filename="x.pdf",
    )
    assert LoggingRenderSink().render(request, b"some-bytes") is None
