"""S-drift-2 integration proofs — the D2+D3 scan end-to-end against a real vault.

Tamper the LIVE mirror tree four ways (foreign bytes / an older revision's bytes / extra / missing)
→ the scan classifies (MIRROR_TAMPER vs MIRROR_STALE), QUARANTINES before the rebuild (R11),
audits per anomaly, writes the drift_scan summary, and the scan_and_sync pipeline corrects the
tree (a re-scan is CLEAN). Plus the §11 folds: pointer integrity (a rollback'd / unregistered
`current` is TAMPER, never benign), the foreign-.builds quarantine-by-move, the
current-row-protected keep-last-N prune, the CLEAN/FAILED row-per-scan contract, and the REAL
task-path lock skip-tick. ⚠ Run-scoped/delta assertions only (the shared session DB): every
audit/drift_scan lookup keys on THIS scan's scan_id; SoD-2: releases come from the approver
(subj.b), never the author.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.drift_scan import DriftScan
from easysynq_api.db.models.mirror_build import MirrorBuild
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.common.pg_locks import LOCK_MIRROR_SYNC
from easysynq_api.services.vault import mirror as mirror_mod
from easysynq_api.services.vault.mirror import sync_mirror
from easysynq_api.services.vault.mirror_scan import (
    CLASS_EXTRA,
    CLASS_MISSING,
    CLASS_STALE,
    CLASS_UNEXPECTED,
    ScanReport,
    persist_scan_results,
    scan_and_sync,
    scan_mirror,
)
from easysynq_api.services.vault.render import LoggingRenderSink

from . import s5_helpers as s5
from .test_mirror import _doc_dir, _grant_release_actors, _source_in
from .test_vault import _auth, _checkin, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}")


async def _sync(mirror: Path) -> None:
    await sync_mirror(mirror_path=mirror, render_sink=LoggingRenderSink())


async def _events_for_scan(scan_id: uuid.UUID) -> list[AuditEvent]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(AuditEvent).where(AuditEvent.after["scan_id"].astext == str(scan_id))
                )
            )
            .scalars()
            .all()
        )


async def _scan_row(scan_id: uuid.UUID) -> DriftScan | None:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(DriftScan).where(DriftScan.counts["scan_id"].astext == str(scan_id))
            )
        ).scalar_one_or_none()


async def test_sync_writes_baseline_row(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Every sync persists a mirror_build row keyed by current's actual .builds target, with the
    manifest + the byte digest of the on-disk manifest.json."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"BASE-V1")
    await _sync(mirror)

    build_name = Path(os.readlink(mirror / "current")).name
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(select(MirrorBuild).where(MirrorBuild.build_name == build_name))
        ).scalar_one()
    manifest_bytes = (mirror / "current" / "_meta" / "manifest.json").read_bytes()
    assert row.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    assert any("document_id" in e for e in row.manifest)
    assert row.files == sum(1 for e in row.manifest if "sha256" in e)
    assert row.swapped_at is not None  # the post-swap pointer-integrity stamp (spec §11.1)


async def test_baseline_keep_last_n_prune(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keep-last-N prune holds (N monkeypatched to 1 so the proof needs three syncs, not 23)
    AND it never deletes the row `current` points at (spec §11.4). At each sync's prune, the
    then-current build's row is excluded — so it takes a THIRD sync for the first build's row to
    become prunable. Run-scoped: only THIS test's three build rows are asserted on (pruning other
    tests' stale rows is the prune doing its job — the registry is regenerable)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"PRUNE-V1")
    monkeypatch.setattr(mirror_mod, "_KEEP_BUILD_ROWS", 1)

    await _sync(mirror)
    first_build = Path(os.readlink(mirror / "current")).name
    await _sync(mirror)
    second_build = Path(os.readlink(mirror / "current")).name
    # After sync 2: first's row SURVIVES — it was current's target during sync 2's prune (the
    # §11.4 exclusion: never disarm detection on the still-served tree).
    async with get_sessionmaker()() as s:
        names = set(
            (
                await s.execute(
                    select(MirrorBuild.build_name).where(
                        MirrorBuild.build_name.in_([first_build, second_build])
                    )
                )
            ).scalars()
        )
    assert names == {first_build, second_build}

    await _sync(mirror)
    third_build = Path(os.readlink(mirror / "current")).name
    assert len({first_build, second_build, third_build}) == 3
    # After sync 3 (current was second during its prune): first is finally beyond keep-1 and
    # unprotected → pruned; second (protected) and third (newest) survive.
    async with get_sessionmaker()() as s:
        names = set(
            (
                await s.execute(
                    select(MirrorBuild.build_name).where(
                        MirrorBuild.build_name.in_([first_build, second_build, third_build])
                    )
                )
            ).scalars()
        )
    assert names == {second_build, third_build}


async def test_clean_scan_after_sync(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"CLEAN-V1")
    await _sync(mirror)

    async with get_sessionmaker()() as s:
        report = await scan_mirror(s, mirror_path=mirror)
        persisted = await persist_scan_results(
            s, report, rebuild_triggered=False, triggered_by="cli"
        )
    assert report.status == "CLEAN"
    assert report.baseline == "ok"
    assert report.pointer == "ok"
    assert report.is_current is True
    assert report.findings == []
    assert not (mirror / ".quarantine").exists()
    assert persisted is True
    # The noise posture is meaningful only through persist: NO audit events for a clean scan…
    assert await _events_for_scan(report.scan_id) == []
    # …but EVERY scan gets its drift_scan summary row (the row-per-scan contract, spec §6).
    row = await _scan_row(report.scan_id)
    assert row is not None and row.status.value == "CLEAN"


async def test_tamper_detect_quarantine_audit_correct(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """The headline D2 proof: foreign bytes + extra + missing → MIRROR_TAMPER each, quarantined
    BEFORE the vault-wins rebuild, audited (doc-attributed where possible), summarized; the
    pipeline corrects and a re-scan is CLEAN."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), b"TAMPER-GOOD"
    )
    await _sync(mirror)

    src = _source_in(_doc_dir(mirror, doc["identifier"]))
    src.write_bytes(b"TAMPER-EVIL")
    (mirror / "current" / "STRAY.txt").write_text("not from the vault")
    changelog = _doc_dir(mirror, doc["identifier"]) / "CHANGELOG.md"
    changelog.unlink()

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )

    assert report.status == "DIVERGENT"
    assert result is not None  # the rebuild ran
    by = {f.path: f for f in report.findings}
    rel_src = src.relative_to(mirror / "current").as_posix()
    rel_log = changelog.relative_to(mirror / "current").as_posix()
    assert by[rel_src].classification == CLASS_UNEXPECTED
    assert by["STRAY.txt"].classification == CLASS_EXTRA
    assert by[rel_log].classification == CLASS_MISSING

    # R11: the tampered bytes were quarantined before the rebuild pruned the old tree.
    qdirs = list((mirror / ".quarantine").iterdir())
    assert len(qdirs) == 1
    assert (qdirs[0] / "files" / rel_src).read_bytes() == b"TAMPER-EVIL"
    assert (qdirs[0] / "files" / "STRAY.txt").read_bytes() == b"not from the vault"

    # Audited per anomaly — all MIRROR_TAMPER; the doc-owned ones attributed to the document.
    events = await _events_for_scan(report.scan_id)
    assert len(events) == 3
    assert {e.event_type for e in events} == {EventType.MIRROR_TAMPER}
    doc_events = [e for e in events if str(e.object_id) == doc["id"]]
    assert len(doc_events) == 2  # the source file + the missing CHANGELOG.md
    assert all(e.scope_ref == doc["identifier"] for e in doc_events)

    # The drift_scan summary row.
    row = await _scan_row(report.scan_id)
    assert row is not None
    assert row.status.value == "DIVERGENT"
    assert row.counts["rebuild_triggered"] is True
    assert row.triggered_by == "beat"

    # Corrected: the live tree re-hashes clean.
    assert _source_in(_doc_dir(mirror, doc["identifier"])).read_bytes() == b"TAMPER-GOOD"
    assert not (mirror / "current" / "STRAY.txt").exists()
    async with get_sessionmaker()() as s:
        rescan = await scan_mirror(s, mirror_path=mirror)
    assert rescan.status == "CLEAN"


async def test_stale_revision_classification(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """D3: replacing the mirrored content with an OLDER revision's bytes is MIRROR_STALE
    (STALE_REVISION), not tamper — the bytes are known vault content of the same document."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"STALE-V1")
    did = doc["id"]
    # Revise to v2 (author a) → approve + release (b) — the test_mirror.py supersession recipe.
    await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    sha2 = await _upload(app_client, ha, did, b"STALE-V2")
    await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MINOR")
    await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    task_id = await s5.task_for_doc(did)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    await _sync(mirror)

    src = _source_in(_doc_dir(mirror, doc["identifier"]))
    assert src.read_bytes() == b"STALE-V2"
    src.write_bytes(b"STALE-V1")  # roll the file back to the superseded revision's bytes

    async with get_sessionmaker()() as s:
        report = await scan_mirror(s, mirror_path=mirror)
        await persist_scan_results(s, report, rebuild_triggered=False, triggered_by="cli")
    rel_src = src.relative_to(mirror / "current").as_posix()
    f = next(f for f in report.findings if f.path == rel_src)
    assert f.classification == CLASS_STALE
    events = await _events_for_scan(report.scan_id)
    assert [e.event_type for e in events] == [EventType.MIRROR_STALE]

    # The OLDER-RENDITION leg (spec §11.7): a rollback to a superseded version's cached
    # controlled-copy rendition digest is STALE too (drops if rendition_blob_sha256 ever falls
    # out of _known_digests). Seed a fake rendition digest on the superseded version, then
    # plant bytes with exactly that digest.
    old_rendition = b"OLD-RENDITION-BYTES"
    async with get_sessionmaker()() as s:
        await s.execute(
            text(
                "UPDATE document_version SET rendition_blob_sha256 = :sha "
                "WHERE document_id = :doc AND version_state = 'Superseded'"
            ),
            {"sha": hashlib.sha256(old_rendition).hexdigest(), "doc": did},
        )
        await s.commit()
    src.write_bytes(old_rendition)
    async with get_sessionmaker()() as s:
        report2 = await scan_mirror(s, mirror_path=mirror)
    f2 = next(f for f in report2.findings if f.path == rel_src)
    assert f2.classification == CLASS_STALE


async def test_draft_bytes_are_tamper_not_stale(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Codex P2: the mirror is Effective-only, so planted UNRELEASED (Draft) bytes of the same
    document are alarm-worthy MIRROR_TAMPER, NOT a soft MIRROR_STALE — _known_digests only counts
    versions that were once the controlled copy (Effective/Superseded/Obsolete)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"DRAFT-TEST-V1")
    did = doc["id"]
    # Create a v2 DRAFT (checked-in, NOT submitted/released) with known bytes — never Effective.
    await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    draft_bytes = b"UNRELEASED-DRAFT-V2-BYTES"
    sha2 = await _upload(app_client, ha, did, draft_bytes)
    await _checkin(app_client, ha, did, sha2, change_reason="v2 draft", change_significance="MINOR")
    await _sync(mirror)  # mirror still holds v1 (Effective-only)

    src = _source_in(_doc_dir(mirror, doc["identifier"]))
    assert src.read_bytes() == b"DRAFT-TEST-V1"
    src.write_bytes(draft_bytes)  # plant the unreleased Draft's bytes into the served file

    async with get_sessionmaker()() as s:
        report = await scan_mirror(s, mirror_path=mirror)
        await persist_scan_results(s, report, rebuild_triggered=False, triggered_by="cli")
    rel_src = src.relative_to(mirror / "current").as_posix()
    f = next(f for f in report.findings if f.path == rel_src)
    assert f.classification == CLASS_UNEXPECTED  # Draft bytes are TAMPER, not STALE
    events = await _events_for_scan(report.scan_id)
    assert EventType.MIRROR_TAMPER in {e.event_type for e in events}


async def test_behind_vault_build_is_not_current_no_audit(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """The D3 currency backstop: a release AFTER the last sync makes the build not-current —
    NOT tamper (zero findings, zero audit events), but the if_needed pipeline rebuilds."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"CURRENT-V1")
    await _sync(mirror)
    doc2 = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"CURRENT-V2-DOC")

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.status == "CLEAN"  # the tree matches its baseline — nothing was tampered
    assert report.is_current is False  # but the vault moved on
    assert report.findings == []
    assert await _events_for_scan(report.scan_id) == []  # behind-vault is never audited
    row = await _scan_row(report.scan_id)  # the row-per-scan contract holds for CLEAN scans
    assert row is not None and row.status.value == "CLEAN"
    assert row.counts["rebuild_triggered"] is True
    assert result is not None  # the rebuild caught the mirror up
    assert _doc_dir(mirror, doc2["identifier"]).is_dir()


async def test_unregistered_current_is_foreign_tamper(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Spec §11.1: with a NON-empty registry, a current target with no row is FOREIGN →
    MIRROR_TAMPER (a planted/renamed tree must never pass as the benign no-baseline); the
    pipeline still corrects and the fresh build is registered. (The TRUE empty-registry
    no-baseline — the pre-0046 production upgrade — is the stubbed unit test: the shared
    integration DB always carries other tests' registry rows.)"""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"UPGRADE-V1")
    await _sync(mirror)

    build_name = Path(os.readlink(mirror / "current")).name
    async with get_sessionmaker()() as s:
        await s.execute(text("DELETE FROM mirror_build WHERE build_name = :b"), {"b": build_name})
        await s.commit()

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.pointer == "foreign"
    assert report.status == "DIVERGENT"
    assert [f.classification for f in report.findings if f.path == "current"] == [
        "POINTER_DIVERGENT"
    ]
    events = await _events_for_scan(report.scan_id)
    assert {e.event_type for e in events} == {EventType.MIRROR_TAMPER}
    assert result is not None  # corrected: a fresh, registered build serves
    new_build = Path(os.readlink(mirror / "current")).name
    assert new_build != build_name
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(select(MirrorBuild).where(MirrorBuild.build_name == new_build))
        ).scalar_one_or_none()
    assert row is not None and row.swapped_at is not None  # the post-swap stamp landed


async def test_persist_writes_failed_row(app_under_test: object) -> None:
    """Spec §8/§11.7: a FAILED report still gets its drift_scan summary row — the runbook's
    'persistent FAILED stream' operator signal depends on it. (The report is constructed
    directly; producing a real infra failure is the unit suite's job.)"""
    report = ScanReport(
        scan_id=uuid.uuid4(),
        started_at=datetime.datetime.now(tz=datetime.UTC),
        baseline="ok",
        status="FAILED",
        is_current=False,
        build_name="deadbeef",
        findings=[],
        error="simulated",
    )
    async with get_sessionmaker()() as s:
        persisted = await persist_scan_results(
            s, report, rebuild_triggered=False, triggered_by="cli"
        )
    assert persisted is True
    row = await _scan_row(report.scan_id)
    assert row is not None and row.status.value == "FAILED"
    assert row.counts["error"] == "simulated"


async def test_pointer_rollback_is_tamper(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Spec §11.1: repointing `current` at a restored OLDER swapped build is MIRROR_TAMPER
    (POINTER_DIVERGENT) — whole-tree rollback must never pass as a benign stale mirror."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"PTR-V1")
    await _sync(mirror)
    first_build = Path(os.readlink(mirror / "current")).name
    saved = tmp_path / "saved-old-build"
    shutil.copytree(mirror / ".builds" / first_build, saved, symlinks=True)

    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"PTR-V2")
    await _sync(mirror)  # second build supersedes; _prune_builds removed the first dir

    # The attack: restore the old build dir and repoint current at it.
    shutil.copytree(saved, mirror / ".builds" / first_build, symlinks=True)
    tmp_link = mirror / ".current.attack.tmp"
    os.symlink(os.path.join(".builds", first_build), tmp_link)
    os.replace(tmp_link, mirror / "current")

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.pointer == "rollback"
    assert report.status == "DIVERGENT"
    pointer_findings = [f for f in report.findings if f.path == "current"]
    assert len(pointer_findings) == 1
    events = await _events_for_scan(report.scan_id)
    assert EventType.MIRROR_TAMPER in {e.event_type for e in events}
    assert result is not None  # corrected: current repointed at a fresh build
    rescan_target = Path(os.readlink(mirror / "current")).name
    assert rescan_target != first_build


async def test_selfheal_does_not_stamp_a_missing_build(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Codex P2: the swap-then-crash self-heal stamps swapped_at ONLY when the served build root
    is intact. Simulate the crash window (NULL the stamp) AND delete the served tree → the scan
    must record a POINTER finding and NOT stamp the build as a clean swap (registry integrity)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"SELFHEAL-V1")
    await _sync(mirror)
    build_name = Path(os.readlink(mirror / "current")).name
    async with get_sessionmaker()() as s:  # re-create the swap-then-crash window
        await s.execute(
            text("UPDATE mirror_build SET swapped_at = NULL WHERE build_name = :b"),
            {"b": build_name},
        )
        await s.commit()
    shutil.rmtree(mirror / ".builds" / build_name)  # …and the served tree goes missing

    async with get_sessionmaker()() as s:
        report = await scan_mirror(s, mirror_path=mirror)
        await persist_scan_results(s, report, rebuild_triggered=False, triggered_by="cli")
    assert report.pointer == "selfheal"  # current still names the newest unswapped row
    assert any(f.classification == "POINTER_DIVERGENT" for f in report.findings)
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(select(MirrorBuild).where(MirrorBuild.build_name == build_name))
        ).scalar_one()
    assert row.swapped_at is None  # NOT stamped — a missing tree is never recorded as a clean swap


async def test_foreign_builds_tree_quarantined_by_move(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Spec §11.2: an unregistered .builds/ child is EXTRA→TAMPER and is MOVED to quarantine —
    otherwise the next sync's prune would rmtree the planted bytes unaudited."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"FERAL-V1")
    await _sync(mirror)
    feral = mirror / ".builds" / "feral"
    (feral / "deep").mkdir(parents=True)
    (feral / "deep" / "payload.bin").write_bytes(b"PLANTED")

    async with get_sessionmaker()() as s:
        report = await scan_mirror(s, mirror_path=mirror)
    f = next(f for f in report.findings if f.path == ".builds/feral")
    assert f.classification == CLASS_EXTRA
    assert not feral.exists()  # moved, not copied — out of _prune_builds' reach
    qdirs = list((mirror / ".quarantine").iterdir())
    assert len(qdirs) == 1
    assert (
        qdirs[0] / "files" / ".builds" / "feral" / "deep" / "payload.bin"
    ).read_bytes() == b"PLANTED"


async def test_destroyed_served_tree_is_tamper_not_clean(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """C1 (Task-4 quality fold): destroying the served build tree while `current` stays valid
    must be MIRROR_TAMPER (POINTER) + rebuild — NOT a silent CLEAN (the most basic tamper)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"GONE-V1")
    await _sync(mirror)
    build_name = Path(os.readlink(mirror / "current")).name
    shutil.rmtree(mirror / ".builds" / build_name)  # current symlink intact, target gone

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.status == "DIVERGENT"
    assert any(f.classification == "POINTER_DIVERGENT" for f in report.findings)
    events = await _events_for_scan(report.scan_id)
    assert EventType.MIRROR_TAMPER in {e.event_type for e in events}
    assert result is not None  # the hourly path rebuilt rather than reporting a false CLEAN


async def test_planted_file_at_served_build_root_is_quarantined(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Codex P2: when `current` still names a registered build but `.builds/<name>` has been
    replaced by a regular FILE, the planted bytes are quarantined before the corrective rebuild
    prunes them — the served build root is not a registered-child the .builds sweep would move."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"REPLACED-V1")
    await _sync(mirror)
    build_name = Path(os.readlink(mirror / "current")).name
    shutil.rmtree(mirror / ".builds" / build_name)
    (mirror / ".builds" / build_name).write_bytes(b"PLANTED-AT-BUILD-ROOT")  # a file, not a dir

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.status == "DIVERGENT"
    assert any(f.classification == "POINTER_DIVERGENT" for f in report.findings)
    qdirs = list((mirror / ".quarantine").iterdir())
    assert len(qdirs) == 1
    assert (qdirs[0] / "files" / ".builds" / build_name).read_bytes() == b"PLANTED-AT-BUILD-ROOT"
    assert result is not None  # corrected: a fresh registered build now serves


async def test_scan_task_skips_when_sync_lock_held(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan and sync share LOCK_MIRROR_SYNC — the REAL task path skip-ticks while a holder holds
    the lock (the test_mirror_sync_advisory_lock_serializes convention: drive _run_mirror_scan,
    not the bare primitive — a vacuous two-session lock test passes even if the task never takes
    the lock)."""
    from easysynq_api.config import get_settings
    from easysynq_api.tasks.mirror import _run_mirror_scan

    monkeypatch.setenv("MIRROR_PATH", str(tmp_path / "m"))
    get_settings.cache_clear()
    try:
        async with get_sessionmaker()() as holder:
            held = (
                await holder.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_MIRROR_SYNC}
                )
            ).scalar()
            assert held is True
            assert await _run_mirror_scan() == {"skipped_lock_held": 1}  # contended → skipped
            await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_MIRROR_SYNC})
    finally:
        get_settings.cache_clear()
