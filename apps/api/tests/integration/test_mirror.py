"""S7 integration proofs — the read-only, Effective-only filesystem mirror (AC#2).

Headline proof: ``test_ro_mirror_autocorrect`` — an edited mirror file is overwritten from the vault
on the next sync (and an out-of-band stray file is removed), because the whole tree is rebuilt and
the ``current`` symlink atomically repointed. Supporting proofs: only Effective versions reach the
mirror (drafts excluded); supersession/obsolescence prune the prior doc; release enqueues the
rebuild post-commit; the render deferral marks ``render_status:"pending"`` (NOT R26's
``no_controlled_rendition``); metadata/INDEX/manifest are written; the rebuild is byte-idempotent;
the advisory lock serializes overlapping syncs.

The multi-actor setup reuses the S5 helpers: author ``a`` checks in + submits; approver/releaser
``b`` approves + releases (``allow_approver_release`` on). The mirror writer reads as the non-owner
``easysynq_app`` role (SELECT on document_version/blob) per the S6 role separation.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.common.pg_locks import LOCK_MIRROR_SYNC
from easysynq_api.services.vault.mirror import sync_mirror
from easysynq_api.services.vault.mirror_sink import (
    CapturingMirrorEnqueueSink,
    set_mirror_enqueue_sink,
)
from easysynq_api.services.vault.render import LoggingRenderSink

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _upload

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
    """The single mirror directory for ``identifier`` (the session-scoped DB accumulates other
    tests' Effective docs, so scope every assertion to this test's own document)."""
    current = mirror / "current"
    matches = [p for p in current.iterdir() if p.is_dir() and p.name.startswith(f"{identifier}_")]
    assert len(matches) == 1, f"expected one dir for {identifier}, got {[m.name for m in matches]}"
    return matches[0]


def _has_doc_dir(mirror: Path, identifier: str) -> bool:
    current = mirror / "current"
    return any(p.is_dir() and p.name.startswith(f"{identifier}_") for p in current.iterdir())


def _source_in(doc_dir: Path) -> Path:
    sources = [f for f in doc_dir.iterdir() if f.name not in _META_NAMES]
    assert len(sources) == 1, f"expected one source file, got {[f.name for f in sources]}"
    return sources[0]


def _all_file_bytes(mirror: Path) -> list[bytes]:
    return [f.read_bytes() for f in (mirror / "current").rglob("*") if f.is_file()]


def _content_snapshot(root: Path) -> dict[str, str]:
    """{relpath: sha256} for every file under ``root`` EXCEPT ``_meta/manifest.json`` (whose
    generated-at timestamp legitimately varies between runs). Pass a single doc dir to keep the
    idempotency check scoped to this test's own document (the DB accumulates other tests' docs)."""
    snapshot: dict[str, str] = {}
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if f.is_file() and rel != Path("_meta/manifest.json"):
            snapshot[str(rel)] = hashlib.sha256(f.read_bytes()).hexdigest()
    return snapshot


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

    index = (mirror / "current" / "INDEX.md").read_text()
    assert meta["identifier"] in index

    manifest = json.loads((mirror / "current" / "_meta" / "manifest.json").read_text())
    assert manifest["files"]
    assert all("sha256" in f and "path" in f for f in manifest["files"])


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
