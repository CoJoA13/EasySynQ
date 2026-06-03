"""S7 + S9b integration proofs — the read-only, Effective-only, clause-aligned filesystem mirror.

Headline proof: ``test_ro_mirror_autocorrect`` — an edited mirror file is overwritten from the vault
on the next sync (and an out-of-band stray file is removed), because the whole tree is rebuilt and
the ``current`` symlink atomically repointed. Supporting proofs: only Effective versions reach the
mirror (drafts excluded); supersession/obsolescence prune the prior doc; release enqueues the
rebuild post-commit; the render deferral marks ``render_status:"pending"`` (NOT R26's
``no_controlled_rendition``); metadata/INDEX/manifest are written; the rebuild is byte-idempotent;
the advisory lock serializes overlapping syncs. **S9b:** a doc is placed under its
``{PHASE}/{NN}-Word/`` clause folder, reachable from every other mapped clause via a relative
symlink; the clause-7 PLAN/DO split, multi-clause symlinks, the symlink-survives-swap chain, and the
``_unmapped/`` upgrade fallback are all exercised.

The multi-actor setup reuses the S5 helpers: author ``a`` checks in + submits; approver/releaser
``b`` approves + releases (``allow_approver_release`` on). The mirror writer reads as the non-owner
``easysynq_app`` role (SELECT on document_version/blob) per the S6 role separation.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.process import Process
from easysynq_api.db.models.process_link import ProcessLink
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.common.pg_locks import LOCK_MIRROR_SYNC
from easysynq_api.services.vault.mirror import sync_mirror
from easysynq_api.services.vault.mirror_sink import (
    CapturingMirrorEnqueueSink,
    set_mirror_enqueue_sink,
)
from easysynq_api.services.vault.render import LoggingRenderSink

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _first_clause_id, _upload

pytestmark = pytest.mark.integration

_META_NAMES = {"metadata.json", "CHANGELOG.md"}


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}")


async def _grant_release_actors(subj: SimpleNamespace) -> None:
    """Author a + approver/releaser b both hold the full lifecycle set; SoD does the gating, and
    ``allow_approver_release`` lets b (the approver) also release."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)


async def _sync(mirror: Path) -> int:
    result = await sync_mirror(mirror_path=mirror, render_sink=LoggingRenderSink())
    return result.documents


def _doc_dir(mirror: Path, identifier: str) -> Path:
    """The single REAL mirror directory for ``identifier`` — now nested under ``{PHASE}/{NN}-Word/``
    (or ``_unmapped/``), so search recursively; cross-clause symlink copies are excluded. The
    session-scoped DB accumulates other tests' Effective docs, so scope every assertion to this
    test's own document."""
    matches = [
        p
        for p in (mirror / "current").rglob(f"{identifier}_*")
        if p.is_dir() and not p.is_symlink()
    ]
    assert len(matches) == 1, f"expected one dir for {identifier}, got {[str(m) for m in matches]}"
    return matches[0]


def _has_doc_dir(mirror: Path, identifier: str) -> bool:
    return any(
        p.is_dir() and not p.is_symlink() for p in (mirror / "current").rglob(f"{identifier}_*")
    )


def _source_in(doc_dir: Path) -> Path:
    sources = [f for f in doc_dir.iterdir() if f.name not in _META_NAMES]
    assert len(sources) == 1, f"expected one source file, got {[f.name for f in sources]}"
    return sources[0]


def _all_file_bytes(mirror: Path) -> list[bytes]:
    """Bytes of every REAL file under current/ — ``os.walk`` (followlinks=False) so a cross-clause
    symlink folder isn't traversed and its bytes double-counted."""
    out: list[bytes] = []
    for dirpath, _dirs, names in os.walk(mirror / "current"):
        for name in names:
            path = Path(dirpath) / name
            if not path.is_symlink():
                out.append(path.read_bytes())
    return out


def _content_snapshot(root: Path) -> dict[str, str]:
    """{relpath: sha256} for every real file under ``root`` EXCEPT ``_meta/manifest.json`` (whose
    generated-at timestamp legitimately varies between runs). Pass a single doc dir to keep the
    idempotency check scoped to this test's own document (the DB accumulates other tests' docs)."""
    snapshot: dict[str, str] = {}
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if f.is_file() and not f.is_symlink() and rel != Path("_meta/manifest.json"):
            snapshot[str(rel)] = hashlib.sha256(f.read_bytes()).hexdigest()
    return snapshot


async def _clause_id(number: str) -> str:
    """The iso9001:2015 clause id for a given clause number (e.g. '4.1', '7.5')."""
    async with get_sessionmaker()() as s:
        return str(
            (
                await s.execute(
                    text(
                        "SELECT c.id FROM clause c JOIN framework f ON c.framework_id = f.id "
                        "WHERE f.code = 'iso9001:2015' AND c.number = :n"
                    ),
                    {"n": number},
                )
            ).scalar_one()
        )


async def _remap_exactly(
    client: AsyncClient, h: dict[str, str], doc_id: str, numbers: list[str]
) -> None:
    """Replace ``drive_to_effective``'s single auto-mapped clause (``_first_clause_id``) with the
    EXACT given clause numbers so a placement test controls the on-disk tree shape (mappings live on
    the document, so this survives into the Effective tree — no re-release needed)."""
    for number in numbers:
        r = await client.post(
            f"/api/v1/documents/{doc_id}/clause-mappings",
            headers=h,
            json={"clause_id": await _clause_id(number)},
        )
        assert r.status_code == 201, r.text
    # drive_to_effective always maps _first_clause_id, and the loop above never re-adds it (that
    # would 409), so the auto-mapped clause is always present here — its delete must be a clean 204.
    drop = await client.delete(
        f"/api/v1/documents/{doc_id}/clause-mappings/{await _first_clause_id()}", headers=h
    )
    assert drop.status_code == 204, drop.text


async def test_ro_mirror_autocorrect(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """[AC#2] An edited mirror file is corrected from the vault on the next sync, and an out-of-band
    stray file is removed too. The mechanism is whole-tree rebuild + atomic symlink repoint (not an
    in-place diff): the stray's removal proves the *whole* tree is replaced, not just touched paths.
    The complementary `test_build_tree_overwrites_in_place` (unit) proves a rebuild into a reused
    dir also overwrites drift, so the autocorrect holds regardless of the build-dir strategy."""
    mirror = tmp_path / "qms-mirror"
    content = b"effective-source-bytes-v1"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), content)
    ident = doc["identifier"]

    await _sync(mirror)
    assert _source_in(_doc_dir(mirror, ident)).read_bytes() == content

    # Tamper: overwrite the controlled file with garbage AND drop a stray file the vault never knew.
    _source_in(_doc_dir(mirror, ident)).write_bytes(b"TAMPERED-BYTES")
    (mirror / "current" / "STRAY.txt").write_text("not from the vault")
    assert (mirror / "current" / "STRAY.txt").exists()

    # Re-sync: the vault is authority — the file is restored and the stray is gone.
    await _sync(mirror)
    assert _source_in(_doc_dir(mirror, ident)).read_bytes() == content
    assert not (mirror / "current" / "STRAY.txt").exists()


async def test_mirror_effective_only_drafts_excluded(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """A Draft (checked in, never released) is provably absent; only Effective versions mirror."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    draft = await _create(app_client, ha, type_id)
    draft_id, draft_ident = draft["id"], draft["identifier"]
    await app_client.post(f"/api/v1/documents/{draft_id}/checkout", headers=ha)
    dsha = await _upload(app_client, ha, draft_id, b"DRAFT-NEVER-IN-MIRROR")
    await _checkin(app_client, ha, draft_id, dsha, change_reason="d", change_significance="MAJOR")

    effdoc = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"EFFECTIVE-IN-MIRROR")

    await _sync(mirror)
    assert _source_in(_doc_dir(mirror, effdoc["identifier"])).read_bytes() == b"EFFECTIVE-IN-MIRROR"
    assert not _has_doc_dir(mirror, draft_ident)
    assert b"DRAFT-NEVER-IN-MIRROR" not in _all_file_bytes(mirror)


async def test_mirror_supersession_removes_prior(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """After a revision's release supersedes the prior Effective, the mirror reflects v2 only."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    doc = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"SUPER-V1")
    did, ident = doc["id"], doc["identifier"]
    await _sync(mirror)
    assert _source_in(_doc_dir(mirror, ident)).read_bytes() == b"SUPER-V1"

    # Revise (author a) → approve (b) → release (b): v2 supersedes v1.
    await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    sha2 = await _upload(app_client, ha, did, b"SUPER-V2")
    await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MINOR")
    await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    task_id = await s5.task_for_doc(did)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})

    await _sync(mirror)
    assert _source_in(_doc_dir(mirror, ident)).read_bytes() == b"SUPER-V2"
    assert b"SUPER-V1" not in _all_file_bytes(mirror)


async def test_mirror_obsolete_removes_from_tree(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Obsoleting the Effective version (T11) pulls the document from the mirror entirely."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)

    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"obs-doc")
    did, ident = doc["id"], doc["identifier"]
    await _sync(mirror)
    assert _has_doc_dir(mirror, ident)

    r = await app_client.post(
        f"/api/v1/documents/{did}/obsolete", headers=ha, json={"reason": "withdrawn"}
    )
    assert r.status_code == 200, r.text

    await _sync(mirror)
    assert not _has_doc_dir(mirror, ident)  # gone from the live tree (INDEX.md + _meta remain)


async def test_release_enqueues_mirror_sync_post_commit(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """release() enqueues exactly one mirror rebuild, and only after the cutover has committed (the
    document is Effective). A concurrent-release loser never reaches the enqueue (structural — it is
    raised inside the except, before the post-commit hook)."""
    capture = CapturingMirrorEnqueueSink()
    previous = set_mirror_enqueue_sink(capture)
    try:
        await _grant_release_actors(subj)
        ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
        doc = await s5.drive_to_effective(
            app_client, ha, hb, hb, await s5.type_id("SOP"), b"enqueue-once"
        )
        assert doc["current_state"] == "Effective"  # committed
        assert capture.reasons == ["release"]  # exactly one enqueue, post-commit
    finally:
        set_mirror_enqueue_sink(previous)


async def test_mirror_render_pending_marker(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """With rendering deferred (S7b), metadata records an honest render_status:"pending" — NOT
    R26's no_controlled_rendition (reserved for genuinely non-renderable formats)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), b"render-pending"
    )

    await _sync(mirror)
    meta = json.loads((_doc_dir(mirror, doc["identifier"]) / "metadata.json").read_text())
    assert meta["render_status"] == "pending"
    assert "no_controlled_rendition" not in meta


async def test_mirror_metadata_and_index(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Per-document metadata.json + CHANGELOG.md, the top-level INDEX.md, and the generated
    _meta/manifest.json (per-file sha256) are all written (doc 04 §10.3)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"meta-doc")

    await _sync(mirror)
    doc_dir = _doc_dir(mirror, doc["identifier"])
    meta = json.loads((doc_dir / "metadata.json").read_text())
    for key in ("identifier", "title", "revision_label", "effective_from", "source_sha256"):
        assert key in meta, key
    assert (doc_dir / "CHANGELOG.md").exists()

    # S9b: metadata carries the mapped-clause list (basis for the tree + the compliance checklist).
    assert meta["clauses"], "clauses array present + non-empty"
    c0 = meta["clauses"][0]
    assert {"number", "pdca_phase", "title", "is_mandatory_star"} <= c0.keys()

    index = (mirror / "current" / "INDEX.md").read_text()
    assert meta["identifier"] in index
    assert c0["number"] in index  # the clause number shows in the INDEX Clauses column

    manifest = json.loads((mirror / "current" / "_meta" / "manifest.json").read_text())
    # File entries carry sha256; symlink entries (from any multi-clause doc in the shared DB) carry
    # symlink_to instead. Both are valid manifest entries.
    files = [f for f in manifest["files"] if "symlink_to" not in f]
    assert files
    assert all("sha256" in f and "path" in f for f in files)
    assert all("path" in f for f in manifest["files"] if "symlink_to" in f)


async def test_mirror_rebuild_idempotent_and_regenerable(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Two syncs produce a byte-identical content tree (doc 04 §10.4 regenerability — a correctness
    dependency of the S11 restore drill). Only the manifest's generated-at differs, by design."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), b"idempotent"
    )
    ident = doc["identifier"]

    await _sync(mirror)
    first = _content_snapshot(_doc_dir(mirror, ident))
    await _sync(mirror)
    second = _content_snapshot(_doc_dir(mirror, ident))
    assert first == second
    assert first  # non-empty (source + metadata.json + CHANGELOG.md)


async def test_mirror_sync_advisory_lock_serializes(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contended sync skips rather than racing: while a holder holds LOCK_MIRROR_SYNC, the task
    path takes its ``if not held: return 0`` branch (so two syncs never build/swap concurrently);
    once the lock is released, the next run rebuilds. (The concurrency safety itself rests on the
    advisory lock being process-wide, which Postgres guarantees across connections.)"""
    from easysynq_api.config import get_settings
    from easysynq_api.tasks.mirror import _run_mirror_sync

    monkeypatch.setenv("MIRROR_PATH", str(tmp_path / "m"))
    get_settings.cache_clear()
    try:
        await _grant_release_actors(subj)
        ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
        await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"lock")

        async with get_sessionmaker()() as holder:
            held = (
                await holder.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_MIRROR_SYNC}
                )
            ).scalar()
            assert held is True
            assert await _run_mirror_sync() == 0  # contended → skipped
            await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_MIRROR_SYNC})

        assert await _run_mirror_sync() >= 1  # free → rebuilt
    finally:
        get_settings.cache_clear()


async def test_mirror_excludes_approved_not_yet_effective(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """The selector gates on version_state==Effective: a future-dated Approved version (not yet
    released by the Beat cutover) is provably excluded — guards the gate against a current_state
    regression that would wrongly include Approved/UnderRevision documents."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)).isoformat()
    await s5.drive_to_approved(
        app_client,
        ha,
        hb,
        await s5.type_id("SOP"),
        b"APPROVED-NOT-EFFECTIVE",
        effective_from=future,
    )

    await _sync(mirror)
    assert b"APPROVED-NOT-EFFECTIVE" not in _all_file_bytes(mirror)


async def test_release_commits_before_enqueue(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """The cutover commits BEFORE the mirror enqueue: a sink that raises in enqueue() makes the
    release error out, yet the document is still Effective — impossible if the enqueue ran inside
    the SERIALIZABLE cutover txn (the raise would roll it back). Decisive proof of post-commit
    ordering. (Fault-injection mirrors test_approval.test_decision_rolls_back_as_one_unit.)"""

    class _RaisingSink:
        def enqueue(self, reason: str | None = None) -> None:
            raise RuntimeError("boom at enqueue")

    previous = set_mirror_enqueue_sink(_RaisingSink())
    try:
        await _grant_release_actors(subj)
        ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
        did = await s5.drive_to_approved(
            app_client, ha, hb, await s5.type_id("SOP"), b"commit-then-enqueue"
        )
        with pytest.raises(RuntimeError, match="boom at enqueue"):
            await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
        assert await s5.effective_count(did) == 1  # the cutover committed before the enqueue raised
    finally:
        set_mirror_enqueue_sink(previous)


# --- S9b: the clause-aligned tree (doc 04 §10.3) ----------------------------------------------


async def test_mirror_clause_placement(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """A doc mapped to a known clause lands under its ``{PHASE}/{NN}-Word/`` folder (8.4→DO)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"placed")
    await _remap_exactly(app_client, ha, doc["id"], ["8.4"])

    await _sync(mirror)
    real = _doc_dir(mirror, doc["identifier"])
    assert real.parent == mirror / "current" / "DO" / "08-Operation"
    assert _source_in(real).read_bytes() == b"placed"


async def test_mirror_multi_clause_symlink(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """A doc mapped to two top-level buckets (4.1 + 8.1): real bytes under the numerically-lower
    clause (PLAN/04-Context), reached from the other via a relative symlink — bytes stored once."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"multi")
    await _remap_exactly(app_client, ha, doc["id"], ["4.1", "8.1"])

    await _sync(mirror)
    real = _doc_dir(mirror, doc["identifier"])
    assert real.parent == mirror / "current" / "PLAN" / "04-Context"  # 4.1 is lower than 8.1
    link = mirror / "current" / "DO" / "08-Operation" / real.name
    assert link.is_symlink()
    assert not link.readlink().is_absolute()  # relative target
    assert link.resolve() == real.resolve()  # resolves back to the real folder
    assert (link / "metadata.json").exists()  # readable through the link
    assert _all_file_bytes(mirror).count(b"multi") == 1  # the source is stored exactly once


async def test_mirror_clause7_split_two_phases(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Clause 7's PLAN/DO split: a doc mapped to 7.2 (PLAN) + 7.5 (DO) is real under PLAN/07-Support
    and symlinked under DO/07-Support — the same top-level folder appears in two phase trees."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"split")
    await _remap_exactly(app_client, ha, doc["id"], ["7.2", "7.5"])

    await _sync(mirror)
    real = _doc_dir(mirror, doc["identifier"])
    assert real.parent == mirror / "current" / "PLAN" / "07-Support"  # 7.2 is lower than 7.5
    link = mirror / "current" / "DO" / "07-Support" / real.name
    assert link.is_symlink() and link.resolve() == real.resolve()


async def test_mirror_unmapped_fallback(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """An Effective doc with ZERO clause mappings lands in ``_unmapped/`` — only reachable as a
    pre-S9 upgrade artifact (the submit-review gate forbids it via the API), so simulate it by
    deleting the mapping rows directly, then sync. The build must not crash on the orphan."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"orphan")
    async with get_sessionmaker()() as s:
        await s.execute(
            text("DELETE FROM clause_mapping WHERE documented_information_id = :d"),
            {"d": uuid.UUID(doc["id"])},
        )
        await s.commit()

    await _sync(mirror)
    real = _doc_dir(mirror, doc["identifier"])
    assert real.parent == mirror / "current" / "_unmapped"
    assert _source_in(real).read_bytes() == b"orphan"


async def test_mirror_symlinks_survive_swap(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """The load-bearing proof: a cross-clause symlink is RELATIVE and resolves end-to-end through
    the ``current → .builds/<uuid>`` chain after the atomic swap (the exact thing that silently
    breaks if a symlink were made absolute or relative-to-``current`` instead of to its own dir)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"swap")
    await _remap_exactly(app_client, ha, doc["id"], ["4.1", "8.1"])

    await _sync(mirror)
    dirname = _doc_dir(mirror, doc["identifier"]).name
    link = mirror / "current" / "DO" / "08-Operation" / dirname  # reached via the current symlink
    assert link.is_symlink()
    assert not link.readlink().is_absolute()
    meta = json.loads((link / "metadata.json").read_text())  # resolves current→.builds→../../PLAN/…
    assert meta["identifier"] == doc["identifier"]


# --- S9d: the by-process secondary index (doc 04 §10.3) ---------------------------------------


async def _link_processes(doc_id: str, names: list[str]) -> list[str]:
    """Direct-seed a Process per name + a ProcessLink to the doc (the mirror reads ``process_link``;
    this avoids needing the ungranted ``process.create`` in a mirror test — the ``_seed_org_role``
    precedent). Returns the unique process names (the by-process folder labels)."""
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(doc_id))
        created: list[str] = []
        for name in names:
            uname = f"{name}-{uuid.uuid4().hex[:6]}"
            proc = Process(
                org_id=doc.org_id,
                name=uname,
                pdca_phase=PdcaPhase.DO,
                created_by=doc.owner_user_id,
            )
            s.add(proc)
            await s.flush()
            s.add(
                ProcessLink(
                    org_id=doc.org_id,
                    process_id=proc.id,
                    documented_information_id=doc.id,
                    created_by=doc.owner_user_id,
                )
            )
            created.append(uname)
        await s.commit()
        return created


async def test_mirror_by_process_index(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """A process-linked Effective doc gets a ``by-process/{name}/`` relative symlink resolving to
    its real clause-tree folder, and its metadata.json lists the process."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"proc-doc")
    [pname] = await _link_processes(doc["id"], ["Purchasing"])

    await _sync(mirror)
    real = _doc_dir(mirror, doc["identifier"])
    link = mirror / "current" / "by-process" / pname / real.name
    assert link.is_symlink()
    assert not link.readlink().is_absolute()  # relative target
    assert link.resolve() == real.resolve()  # resolves to the real clause-tree folder
    assert (link / "metadata.json").exists()
    meta = json.loads((real / "metadata.json").read_text())
    assert pname in [p["name"] for p in meta["processes"]]


async def test_mirror_by_process_multi(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """A doc linked to two processes → two by-process symlinks, both resolving to the one real
    folder; the source bytes are stored exactly once (the symlinks add no real files)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), b"proc-multi"
    )
    names = await _link_processes(doc["id"], ["Purchasing", "Sales"])

    await _sync(mirror)
    real = _doc_dir(mirror, doc["identifier"])
    for pname in names:
        link = mirror / "current" / "by-process" / pname / real.name
        assert link.is_symlink() and link.resolve() == real.resolve()
    assert _all_file_bytes(mirror).count(b"proc-multi") == 1
