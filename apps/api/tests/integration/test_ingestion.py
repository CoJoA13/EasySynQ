"""S-ing-1/2/3 integration: the end-to-end scan->extract->classify->dedup->propose pipeline against
real PG/MinIO/Redis, the ``import.*`` execute/review gate split, deny-by-default, the source-root
lock (dup-active 409), the setup latch (423), and org isolation (404, never a leak). The stages are
driven in-process (no Celery worker in tests) with a mocked Tika sidecar, the ``services.packs``
build precedent."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.config import get_settings
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.domain.ingestion.extractor import ExtractInput, ExtractResult
from easysynq_api.services.ingestion.classify import run_classify
from easysynq_api.services.ingestion.dedup import run_dedup
from easysynq_api.services.ingestion.extract import run_extract
from easysynq_api.services.ingestion.propose import run_propose
from easysynq_api.services.ingestion.service import run_scan

from .test_authz import _assign_role, _auth
from .test_records import _grant, _subject


@pytest.fixture(autouse=True)
def _stub_pipeline_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Celery app binds its broker to the default localhost Redis at import time (not the
    testcontainer), and the shared-DB contract is "never trigger real Celery/Beat" — so stub the
    auto-chain enqueues (scan->extract->classify->dedup->propose); every test drives the stages
    directly (the packs ``build`` precedent). ALL FIVE ``.delay`` chains are stubbed (a missed one
    would publish to the localhost broker and hang)."""
    from easysynq_api.tasks.ingestion import (
        classify_source,
        dedup_source,
        extract_source,
        propose_source,
        scan_source,
    )

    for task in (scan_source, extract_source, classify_source, dedup_source, propose_source):
        monkeypatch.setattr(task, "delay", lambda *a, **k: None)


async def _drive(rid: uuid.UUID) -> None:
    """Drive the full in-process pipeline scan→extract→classify→dedup→propose (each stage on its own
    session, mirroring the worker's per-task session)."""
    for stage in (run_scan, run_extract, run_classify, run_dedup, run_propose):
        async with get_sessionmaker()() as s:
            await stage(s, rid)


def test_autouse_stub_covers_all_ingestion_source_tasks() -> None:
    """A future ``*_source`` chain task added without extending the autouse stub would hang the
    suite — assert the stub covers every registered ingestion ``*_source`` task (the hang guard)."""
    from easysynq_api.tasks import ingestion as ing
    from easysynq_api.tasks.app import app

    registered = {
        n for n in app.tasks if n.startswith("easysynq.ingestion.") and n.endswith("_source")
    }
    stubbed = {
        ing.scan_source.name,
        ing.extract_source.name,
        ing.classify_source.name,
        ing.dedup_source.name,
        ing.propose_source.name,
    }
    assert registered <= stubbed, f"unstubbed ingestion *_source tasks: {registered - stubbed}"


class _FakeTika:
    """A mock Tika extractor for integration (no real sidecar in CI): it decodes the staged bytes
    (the seed files are plain text) as the extracted text. The §5.2 ladder + the real HTTP path are
    unit-tested (``test_ingestion_extractor.py``) + validated on the Docker stack."""

    def __init__(self, **_kw: object) -> None:
        pass

    async def extract(
        self, data: bytes, meta: ExtractInput, *, ocr_enabled: bool, ocr_language: str
    ) -> ExtractResult:
        text = data.decode("utf-8", "ignore").strip()
        return ExtractResult(
            full_text=text or None,
            header_block=text[:1500] or None,
            char_count=len(text),
            extractor_version="fake-tika",
        )


@pytest.fixture
def _stub_tika(monkeypatch: pytest.MonkeyPatch) -> None:
    from easysynq_api.services.ingestion import extract as extract_mod

    monkeypatch.setattr(extract_mod, "TikaExtractorProvider", _FakeTika)


def _seed_source() -> Path:
    """Seed the per-test read-only source root with a messy mix; return its path."""
    root = Path(get_settings().import_source_root)
    (root / "Procedure.docx").write_text("a controlled purchasing procedure")
    (root / "Thumbs.db").write_text("junk")  # excluded (junk)
    (root / "draft.tmp").write_text("scratch")  # quarantined (temp_backup)
    (root / "empty.txt").write_text("")  # excluded (empty)
    sub = root / "sub"
    sub.mkdir(exist_ok=True)
    (sub / "copy1.txt").write_text("identical evidence bytes")
    (sub / "copy2.txt").write_text("identical evidence bytes")  # exact dup of copy1
    return root


async def test_scan_happy_path(app_client: AsyncClient, token_factory: Callable[..., str]) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()

    created = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert created.json()["status"] == "Created"

    # Drive the scan directly (no Celery worker runs in the test).
    async with get_sessionmaker()() as session:
        await run_scan(session, uuid.UUID(run_id))

    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Scanned"
    counts = got["counts"]
    assert counts["total_files"] == 6
    assert counts["included"] == 3  # Procedure + copy1 + copy2
    assert counts["excluded"] == 2  # Thumbs.db (junk) + empty.txt (empty)
    assert counts["quarantine"] == 1  # draft.tmp (temp_backup)
    assert counts["exact_dup_clusters"] == 1  # copy1 == copy2
    assert counts["exact_dup_files"] == 2

    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)).json()[
        "files"
    ]
    by_name = {f["filename"]: f for f in files}
    assert by_name["Thumbs.db"]["scan_flags"]["disposition"] == "excluded"
    assert by_name["draft.tmp"]["scan_flags"]["reason"] == "temp_backup"
    assert by_name["empty.txt"]["scan_flags"]["reason"] == "empty"
    assert by_name["Procedure.docx"]["included_candidate"] is True
    assert by_name["Procedure.docx"]["sha256"]  # included → content-addressed + staged
    assert by_name["Procedure.docx"]["staged_blob_uri"].startswith("s3://import-staging/")
    # the two identical copies dedup to one content address
    assert by_name["copy1.txt"]["sha256"] == by_name["copy2.txt"]["sha256"]
    assert by_name["Thumbs.db"]["sha256"] is None  # excluded → never hashed

    # S-ing-2: Scanned is NO LONGER terminal (the pipeline auto-chains scan->extract->classify), so
    # a Scanned (in-progress) run is cancellable → 200. The lock frees; a 2nd cancel is then 409.
    cancel = await app_client.post(f"/api/v1/admin/imports/{run_id}/cancel", headers=h)
    assert cancel.status_code == 200 and cancel.json()["status"] == "Cancelled"
    again = await app_client.post(f"/api/v1/admin/imports/{run_id}/cancel", headers=h)
    assert again.status_code == 409


def _seed_classifiable() -> None:
    """Seed a clear SOP (DOCUMENT) + audit report (RECORD) under IA folders for the classifier."""
    root = Path(get_settings().import_source_root)
    proc = root / "Procedures"
    proc.mkdir(exist_ok=True)
    (proc / "SOP-PUR-002 Purchasing.docx").write_text(
        "Standard Operating Procedure Purchasing. supplier and purchasing process steps and "
        "responsibilities. Revision History. Approved by J Smith"
    )
    audits = root / "Records" / "Audits"
    audits.mkdir(parents=True, exist_ok=True)
    (audits / "Internal Audit Report Q2 2023.pdf").write_text(
        "Internal Audit Report. audit findings and audit criteria. Lead auditor signed 2023-06-30"
    )


async def test_pipeline_extract_classify(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_classifiable()

    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    rid = uuid.UUID(run_id)
    # Drive the full pipeline to the Proposed terminal (the auto-chain enqueues are stubbed).
    await _drive(rid)

    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Proposed"
    counts = got["counts"]
    assert counts["classified"] == 2  # the SOP + the audit report (both included)
    assert counts["by_kind"]["DOCUMENT"] == 1 and counts["by_kind"]["RECORD"] == 1
    assert "HIGH" in counts["by_band"] and "extract" in counts
    # S-ing-3 namespaced count blocks coexist with the scan/classify keys (no clobber).
    assert "dedup" in counts and "proposal" in counts
    assert counts["proposal"]["keep_items"] == 2  # both are keep-items (no dups/families here)

    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)).json()[
        "files"
    ]
    by_name = {f["filename"]: f for f in files}
    sop = by_name["SOP-PUR-002 Purchasing.docx"]
    assert sop["classification"]["kind"] == "DOCUMENT"
    assert sop["classification"]["type_code"] == "SOP"
    assert sop["classification"]["band"] == "HIGH"
    assert "8.4" in sop["classification"]["clause_numbers"]
    assert sop["classification"]["pdca_phase"] == "DO"
    audit = by_name["Internal Audit Report Q2 2023.pdf"]
    assert audit["classification"]["kind"] == "RECORD"
    assert audit["classification"]["type_code"] == "AUDIT"

    # the per-file detail carries the extract text + the classification evidence
    detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{sop['id']}", headers=h)
    ).json()
    assert detail["extract"]["full_text"] and detail["extract"]["status"] == "extracted"
    assert any(e["dimension"] == "type" for e in detail["classification"]["evidence"])
    # S-ing-3: the keep-item proposal preserves the recognized doc-code verbatim + an IA path; the
    # dedup sub-object shows it is a standalone keep (not in any cluster/family).
    assert detail["proposal"]["proposed_identifier"] == "SOP-PUR-002"
    assert detail["proposal"]["identifier_source"] == "preserved_doc_code"
    assert detail["proposal"]["target_ia_path"]  # a clause-tree placement (DOCUMENT, mapped 8.4)
    assert detail["dedup"]["in_exact_cluster"] is False
    assert detail["dedup"]["in_version_family"] is False

    # the ?kind= / ?band= filters
    docs = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files?kind=DOCUMENT", headers=h)
    ).json()["files"]
    assert len(docs) == 1 and docs[0]["filename"] == "SOP-PUR-002 Purchasing.docx"
    highs = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files?band=HIGH", headers=h)
    ).json()["files"]
    assert {f["filename"] for f in highs} == {
        "SOP-PUR-002 Purchasing.docx",
        "Internal Audit Report Q2 2023.pdf",
    }


class _FailingTika:
    """A mock that always fails extraction (corrupt/unknown sub-format) — never raises (§5.3)."""

    def __init__(self, **_kw: object) -> None:
        pass

    async def extract(
        self, data: bytes, meta: ExtractInput, *, ocr_enabled: bool, ocr_language: str
    ) -> ExtractResult:
        return ExtractResult(failed=True, error="corrupt", extractor_version="fake-tika")


async def test_failed_extract_still_classifies_on_filename(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # §5.3: a failed extract NEVER fails the run; the file still classifies on filename/path.
    from easysynq_api.services.ingestion import extract as extract_mod

    monkeypatch.setattr(extract_mod, "TikaExtractorProvider", _FailingTika)
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_classifiable()

    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    rid = uuid.UUID(run_id)
    await _drive(rid)

    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Proposed"  # the run completed despite every extract failing
    detail_files = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)
    ).json()["files"]
    sop = next(f for f in detail_files if f["filename"] == "SOP-PUR-002 Purchasing.docx")
    fid = sop["id"]
    full = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{fid}", headers=h)).json()
    assert full["extract"]["status"] == "failed"  # extraction recorded as failed
    assert sop["classification"]["type_code"] == "SOP"  # still typed from the filename doc-code


def _seed_dedup_corpus() -> None:
    """A corpus exercising all three §7 detectors: an exact-dup pair, a near-dup pair, and a
    version family (distinct content, same doc-code)."""
    root = Path(get_settings().import_source_root)
    # exact dup (byte-identical .txt) → 1 EXACT cluster.
    (root / "copyA.txt").write_text("identical retained evidence content alpha bravo charlie delta")
    (root / "copyB.txt").write_text("identical retained evidence content alpha bravo charlie delta")
    # near dup: a long shared body + one appended token → Jaccard ~0.98 → 1 NEAR cluster.
    base = "purchasing procedure body " + " ".join(f"step{i}" for i in range(60))
    (root / "manual_base.txt").write_text(base)
    (root / "manual_near.txt").write_text(base + " appendedfinalstep")
    # version family: distinct content (no near-dup), same doc-code → 1 family of 3.
    fam = root / "Procedures"
    fam.mkdir(exist_ok=True)
    (fam / "SOP-FAM-001_v1.docx").write_text(" ".join(f"alpha{i}" for i in range(40)))
    (fam / "SOP-FAM-001_v2.docx").write_text(" ".join(f"beta{i}" for i in range(40)))
    (fam / "SOP-FAM-001_v3 FINAL.docx").write_text(" ".join(f"gamma{i}" for i in range(40)))


async def test_dedup_and_propose_pipeline(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_dedup_corpus()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    await _drive(uuid.UUID(run_id))

    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Proposed"
    counts = got["counts"]
    # the scan-stage keys survive the namespaced dedup/proposal merge (no clobber).
    assert counts["exact_dup_clusters"] == 1 and counts["exact_dup_files"] == 2
    assert counts["dedup"]["by_method"] == {"exact": 1, "near": 1}
    assert counts["dedup"]["redundant_files"] == 2  # one non-canonical per dup cluster
    assert counts["dedup"]["version_families"] == 1
    assert counts["dedup"]["superseded_files"] == 2  # 3-member family → 2 non-effective
    # included = 2 exact + 2 near + 3 family = 7; keep = 7 - 1 - 1 - 2 = 3 canonicals/effective.
    assert counts["proposal"]["keep_items"] == 3

    clusters = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/dupe-clusters", headers=h)
    ).json()["clusters"]
    assert {c["method"] for c in clusters} == {"exact", "near"}
    exact = next(c for c in clusters if c["method"] == "exact")
    assert len(exact["member_file_ids"]) == 2 and exact["jaccard"] == 1.0
    near = next(c for c in clusters if c["method"] == "near")
    assert near["jaccard"] >= 0.85

    families = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/version-families", headers=h)
    ).json()["families"]
    assert len(families) == 1
    fam = families[0]
    assert fam["doc_code"] == "SOP-FAM-001"
    assert len(fam["ordered_member_file_ids"]) == 3
    assert fam["effective_file_id"] == fam["ordered_member_file_ids"][0]  # newest-first

    # per-file roles: a redundant exact member has no proposal + points at its canonical.
    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)).json()[
        "files"
    ]
    by_name = {f["filename"]: f for f in files}
    redundant_id = next(m for m in exact["member_file_ids"] if m != exact["canonical_file_id"])
    rd = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{redundant_id}", headers=h)
    ).json()
    assert rd["dedup"]["in_exact_cluster"] is True and rd["dedup"]["is_canonical"] is False
    assert rd["dedup"]["redundant_of_file_id"] == exact["canonical_file_id"]
    assert rd["proposal"] is None  # a redundant copy is NOT a keep-item

    # the family's effective member (the v3 FINAL) is the keep-item with the preserved doc-code.
    eff = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{fam['effective_file_id']}", headers=h
        )
    ).json()
    assert eff["filename"] == "SOP-FAM-001_v3 FINAL.docx"
    assert eff["dedup"]["is_effective"] is True
    assert eff["proposal"]["proposed_identifier"] == "SOP-FAM-001"
    superseded_id = fam["ordered_member_file_ids"][1]
    sup = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{superseded_id}", headers=h)
    ).json()
    assert sup["dedup"]["superseded_by_file_id"] == fam["effective_file_id"]
    assert sup["proposal"] is None
    assert by_name  # sanity: the listing is populated


async def test_active_run_guard_covers_deduping(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # S-ing-3: the one-run-per-root 409 guard keeps firing while a run is mid-pipeline (Deduping/
    # Proposing), since the source-root lock is held continuously through those stages.
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()
    first = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert first.status_code == 202
    rid = uuid.UUID(first.json()["id"])
    async with get_sessionmaker()() as s:  # advance into Deduping (lock still held from create)
        await s.execute(
            sa.text("UPDATE import_run SET status='Deduping' WHERE id = :i"), {"i": rid}
        )
        await s.commit()
    second = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert second.status_code == 409
    assert (
        second.json()["active_run_id"] == first.json()["id"]
    )  # the Deduping run is still "active"


@pytest.mark.parametrize("stalled_state", ["Extracting", "Deduping", "Proposing"])
async def test_reaper_fails_run_with_dead_lock(
    app_client: AsyncClient, token_factory: Callable[..., str], stalled_state: str
) -> None:
    # The lock-liveness reaper: an in-progress run whose lock has lapsed (dead worker) is FAILED —
    # for ANY in-progress stage (S-ing-3 adds Deduping/Proposing to the reaped set). Drive to
    # (lock held), force the run into the stalled stage, free the lock, then reap.
    from easysynq_api.services.ingestion import locks
    from easysynq_api.services.ingestion.service import reap_stalled_runs

    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_classifiable()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    rid = uuid.UUID(run_id)
    async with get_sessionmaker()() as s:
        await run_scan(s, rid)  # → Scanned, lock held

    async with get_sessionmaker()() as s:
        run = (
            await s.execute(
                sa.text("SELECT source_root_hash FROM import_run WHERE id = :i"), {"i": rid}
            )
        ).scalar_one()
        await s.execute(
            sa.text("UPDATE import_run SET status=:st WHERE id = :i"),
            {"i": rid, "st": stalled_state},
        )
        await s.commit()
    await locks.force_release(run)  # simulate the worker dying (lock lapses)

    async with get_sessionmaker()() as s:
        summary = await reap_stalled_runs(s)
    assert summary["reaped"] >= 1
    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Failed" and got["error"] == "stage_timeout"


async def test_gate_split_execute_vs_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # review-only → can read, cannot create/cancel
    reviewer = _subject("mara")
    await _grant(reviewer, ("import.review",))
    hr = _auth(token_factory, reviewer)
    assert (await app_client.get("/api/v1/admin/imports", headers=hr)).status_code == 200
    denied = await app_client.post("/api/v1/admin/imports", headers=hr, json={"source_root": "."})
    assert denied.status_code == 403

    # execute-only → can reach create/cancel, cannot read
    operator = _subject("priya")
    await _grant(operator, ("import.execute",))
    he = _auth(token_factory, operator)
    assert (await app_client.get("/api/v1/admin/imports", headers=he)).status_code == 403
    # execute passes the gate → a missing run is 404 (not 403), proving the gate is satisfied
    missing = await app_client.post(f"/api/v1/admin/imports/{uuid.uuid4()}/cancel", headers=he)
    assert missing.status_code == 404


async def test_deny_by_default(app_client: AsyncClient, token_factory: Callable[..., str]) -> None:
    h = _auth(token_factory, _subject("nobody"))
    assert (await app_client.get("/api/v1/admin/imports", headers=h)).status_code == 403
    post = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert post.status_code == 403


async def test_duplicate_active_run_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()
    first = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert first.status_code == 202
    # a 2nd run for the same root while the first holds the lock → 409 + the active run id
    second = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"
    assert second.json()["active_run_id"] == first.json()["id"]


async def test_latch_blocks_until_operational(
    app_client: AsyncClient, token_factory: Callable[..., str], dsns: dict[str, str]
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    engine = sa.create_engine(dsns["owner"])
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE system_config SET setup_state='UNINITIALIZED'"))
        blocked = await app_client.get("/api/v1/admin/imports", headers=h)
        assert blocked.status_code == 423
        assert blocked.json()["code"] == "setup_incomplete"
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE system_config SET setup_state='OPERATIONAL'"))
        engine.dispose()


async def test_org_isolation_returns_404(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]

    # a reviewer in a DIFFERENT org must not see org A's run — 404, never a 403/leak. The 2nd org is
    # created AND torn down within this test: a lingering 2nd Organization would break the many
    # shared-DB tests that do ``select(Organization).scalar_one()`` (the single-org test contract).
    other = _subject("orgb-reviewer")
    async with get_sessionmaker()() as session:
        org_b = Organization(
            legal_name="Org B Ltd", short_code=f"ORGB{uuid.uuid4().hex[:6].upper()}"
        )
        session.add(org_b)
        await session.flush()
        org_b_id = org_b.id
        user_b = AppUser(
            org_id=org_b_id,
            keycloak_subject=other,
            display_name=other,
            status=UserStatus.ACTIVE,
        )
        session.add(user_b)
        await session.flush()
        perm = (
            await session.execute(select(Permission).where(Permission.key == "import.review"))
        ).scalar_one()
        scope = Scope(org_id=org_b_id, level=ScopeLevel.SYSTEM)
        session.add(scope)
        await session.flush()
        session.add(
            PermissionOverride(
                org_id=org_b_id,
                user_id=user_b.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
            )
        )
        await session.commit()

    try:
        hb = _auth(token_factory, other)
        cross = await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=hb)
        assert cross.status_code == 404
    finally:
        # Tear down org_b in FK-RESTRICT order so the single-org contract is restored even if the
        # assertion fails.
        async with get_sessionmaker()() as session:
            await session.execute(
                sa.text("DELETE FROM permission_override WHERE org_id = :o"), {"o": org_b_id}
            )
            await session.execute(sa.text("DELETE FROM scope WHERE org_id = :o"), {"o": org_b_id})
            await session.execute(
                sa.text("DELETE FROM app_user WHERE org_id = :o"), {"o": org_b_id}
            )
            await session.execute(
                sa.text("DELETE FROM organization WHERE id = :o"), {"o": org_b_id}
            )
            await session.commit()

    # the single-org contract is restored (a cleanup regression fails HERE, not in the many
    # downstream shared-DB tests that do select(Organization).scalar_one()).
    async with get_sessionmaker()() as session:
        remaining = (
            await session.execute(sa.text("SELECT count(*) FROM organization"))
        ).scalar_one()
        assert remaining == 1


# --------------------------------------------------------------------------- S-ing-4 review


async def _proposed_classifiable(
    app_client: AsyncClient, h: dict[str, str], _stub_tika: None
) -> tuple[str, dict[str, dict]]:
    """Drive a classifiable corpus to Proposed; return (run_id, {filename: file_row})."""
    _seed_classifiable()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    await _drive(uuid.UUID(run_id))
    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)).json()[
        "files"
    ]
    return run_id, {f["filename"]: f for f in files}


async def test_review_decisions_fold_and_reviewing_transition(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]

    # the freshly-Proposed run has each file's review folded as undecided/UNCONFIRMED.
    assert by_name["SOP-PUR-002 Purchasing.docx"]["review"]["disposition"] == "undecided"
    assert by_name["SOP-PUR-002 Purchasing.docx"]["review"]["kind"] == "UNCONFIRMED"

    # accept the SOP + confirm kind=DOCUMENT (R10 kind-confirm rides after.kind).
    res = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=h,
        json={"action": "accept", "after": {"kind": "DOCUMENT"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["review"]["disposition"] == "included"
    assert res.json()["review"]["kind"] == "DOCUMENT"
    assert res.json()["review"]["commit_ready"] is True

    # the first decision flips the run Proposed → Reviewing (a USER stage-change).
    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run["status"] == "Reviewing"

    # the per-file detail carries the folded effective state + the decision history.
    detail = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{sop}", headers=h)).json()
    assert detail["review"]["effective"]["kind"] == "DOCUMENT"
    assert len(detail["review"]["decision_history"]) == 1

    # the decision log lists it (newest-first).
    log = (await app_client.get(f"/api/v1/admin/imports/{run_id}/decisions", headers=h)).json()
    assert len(log["decisions"]) == 1 and log["decisions"][0]["action"] == "accept"

    # correct overrides the engine identifier; exclude/defer set the disposition.
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    corr = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{audit}/decision",
        headers=h,
        json={"action": "correct", "after": {"identifier": "REC-AUD-007", "kind": "RECORD"}},
    )
    assert corr.json()["review"]["identifier"] == "REC-AUD-007"
    assert corr.json()["review"]["kind"] == "RECORD"
    excl = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{audit}/decision",
        headers=h,
        json={"action": "exclude", "reason": "out of scope"},
    )
    assert excl.json()["review"]["disposition"] == "excluded"  # latest wins

    # cancel is still allowed while Reviewing (Reviewing is not terminal).
    cancel = await app_client.post(f"/api/v1/admin/imports/{run_id}/cancel", headers=h)
    assert cancel.status_code == 200 and cancel.json()["status"] == "Cancelled"


async def test_per_file_endpoint_rejects_merge_split_and_guards_state(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]

    bad = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=h,
        json={"action": "merge"},
    )
    assert bad.status_code == 422  # merge/split are structural — dedicated endpoints only

    # a non-reviewable run (still Created, not driven) refuses decisions with 409.
    _seed_source()
    fresh = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "sub"})
    ).json()["id"]
    blocked = await app_client.post(
        f"/api/v1/admin/imports/{fresh}/files/{uuid.uuid4()}/decision",
        headers=h,
        json={"action": "accept"},
    )
    assert blocked.status_code == 409  # status=Created ∉ {Proposed, Reviewing}


async def test_bulk_decision_over_filter(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, _ = await _proposed_classifiable(app_client, h, _stub_tika)

    # bulk-confirm kind=DOCUMENT across the engine's DOCUMENT-classified selection (explicit act).
    res = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/decisions",
        headers=h,
        json={"action": "accept", "selector": {"kind": "DOCUMENT"}, "after": {"kind": "DOCUMENT"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] == 1  # only the SOP is classified DOCUMENT

    docs = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files?review_status=included", headers=h
        )
    ).json()["files"]
    assert {f["filename"] for f in docs} == {"SOP-PUR-002 Purchasing.docx"}
    assert docs[0]["review"]["kind"] == "DOCUMENT" and docs[0]["review"]["commit_ready"] is True


async def test_merge_forces_version_family_with_revision_chain(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]

    # merge the two standalone keep-items into one version family + opt into revision-chain.
    res = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/merge",
        headers=h,
        json={
            "file_ids": [sop, audit],
            "effective_file_id": sop,
            "reconstruct_revision_chain": True,
        },
    )
    assert res.status_code == 200, res.text

    families = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/version-families", headers=h)
    ).json()["families"]
    assert len(families) == 1
    fam = families[0]
    assert set(fam["ordered_member_file_ids"]) == {sop, audit}
    assert fam["effective_file_id"] == sop
    assert fam["reconstruct_revision_chain"] is True  # the per-family R10 opt-in is set

    # the keep-set re-derived: only the effective member is now a keep-item.
    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run["counts"]["proposal"]["keep_items"] == 1
    audit_detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{audit}", headers=h)
    ).json()
    assert audit_detail["proposal"] is None  # the non-effective member is no longer a keep-item
    assert audit_detail["dedup"]["in_version_family"] is True


async def test_split_deletes_group_below_two_members(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_dedup_corpus()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    await _drive(uuid.UUID(run_id))

    clusters = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/dupe-clusters", headers=h)
    ).json()["clusters"]
    exact = next(c for c in clusters if c["method"] == "exact")
    before = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    keep_before = before["counts"]["proposal"]["keep_items"]

    # split off one of the 2 exact-dup members → the cluster drops to 1 → it is DELETED; both files
    # become standalone keep-items (the survivor is not silently dropped).
    member = exact["member_file_ids"][1]
    res = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/split",
        headers=h,
        json={
            "target_kind": "dupe_cluster",
            "target_id": exact["id"],
            "separate_file_ids": [member],
        },
    )
    assert res.status_code == 200, res.text

    after_clusters = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/dupe-clusters", headers=h)
    ).json()["clusters"]
    assert {c["method"] for c in after_clusters} == {"near"}  # the exact cluster is gone
    after = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert (
        after["counts"]["proposal"]["keep_items"] == keep_before + 1
    )  # the survivor became a keep


async def test_exclude_then_merge_keeps_exclude(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    # The design-critic consistency case: a file excluded by decision, then merged structurally into
    # a family, must STAY excluded — the exclude fold wins over the structural family membership.
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]

    await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{audit}/decision",
        headers=h,
        json={"action": "exclude", "reason": "not in scope"},
    )
    await app_client.post(
        f"/api/v1/admin/imports/{run_id}/merge",
        headers=h,
        json={"file_ids": [sop, audit], "effective_file_id": audit},
    )
    detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{audit}", headers=h)
    ).json()
    assert detail["dedup"]["in_version_family"] is True  # structurally merged
    assert detail["review"]["effective"]["disposition"] == "excluded"  # but the exclude still wins
    assert detail["review"]["effective"]["commit_ready"] is False


async def test_checklist_conflict_blocks_then_resolves_and_projection(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]

    # The checklist always carries the ★-coverage projection. NB: the integration DB is shared
    # across the suite, so a prior test's vault doc may collide with the SOP's identifier — assert
    # the SPECIFIC duplicate conflict we introduce, not the global `ready` (the shared-DB rule).
    chk0 = (await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)).json()
    assert "star_coverage" in chk0["advisory"]
    assert "projected_rollup" in chk0["advisory"]["star_coverage"]

    def _dups(chk: dict) -> list:
        return [b for b in chk["blocking"] if b["type"] == "duplicate_identifier_within_import"]

    assert not _dups(chk0)  # no within-import duplicate yet (two distinct identifiers)

    # correct the audit identifier to COLLIDE with the SOP WITHIN the import → a duplicate conflict.
    await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{audit}/decision",
        headers=h,
        json={"action": "correct", "after": {"identifier": "SOP-PUR-002", "kind": "DOCUMENT"}},
    )
    chk1 = (await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)).json()
    assert chk1["ready"] is False
    dup = _dups(chk1)
    assert len(dup) == 1 and dup[0]["identifier"] == "SOP-PUR-002"
    assert {sop, audit} == set(dup[0]["file_ids"])

    # resolve by excluding the colliding file → the within-import duplicate is gone.
    await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{audit}/decision",
        headers=h,
        json={"action": "exclude", "reason": "duplicate of the SOP"},
    )
    chk2 = (await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)).json()
    assert not _dups(chk2)  # the duplicate conflict is resolved

    # the ★-coverage projection never demotes: projected covered ≥ live covered (imports only add).
    rollup = chk2["advisory"]["star_coverage"]["rollup"]
    projected = chk2["advisory"]["star_coverage"]["projected_rollup"]
    assert projected["covered"] >= rollup["covered"]
    assert all("projected_status" in r for r in chk2["advisory"]["star_coverage"]["rows"])


async def test_idempotency_key_replays_one_decision(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    hk = {**h, "Idempotency-Key": "decide-sop-once"}

    first = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=hk,
        json={"action": "accept", "after": {"kind": "DOCUMENT"}},
    )
    assert first.status_code == 200
    replay = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=hk,
        json={"action": "accept", "after": {"kind": "DOCUMENT"}},
    )
    assert replay.status_code == 200 and replay.json().get("replayed") is True

    log = (await app_client.get(f"/api/v1/admin/imports/{run_id}/decisions", headers=h)).json()
    assert len(log["decisions"]) == 1  # the replay created NO duplicate row


async def test_review_writes_nothing_to_the_vault(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]

    # The integration DB is shared across the suite (prior tests created vault docs), so assert the
    # vault row counts are UNCHANGED by the review (delta 0), not absolute-zero (shared-DB rule).
    async def _vault_counts() -> tuple[int, int]:
        async with get_sessionmaker()() as session:
            d = (
                await session.execute(sa.text("SELECT count(*) FROM documented_information"))
            ).scalar_one()
            v = (
                await session.execute(sa.text("SELECT count(*) FROM document_version"))
            ).scalar_one()
        return int(d), int(v)

    before = await _vault_counts()
    await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=h,
        json={"action": "accept", "after": {"kind": "DOCUMENT"}},
    )
    await app_client.post(
        f"/api/v1/admin/imports/{run_id}/merge",
        headers=h,
        json={"file_ids": [sop, audit], "effective_file_id": sop},
    )
    after = await _vault_counts()
    assert after == before  # review commits NOTHING to the vault (commit is S-ing-5)


def _seed_merge_cluster() -> None:
    """3 byte-identical files (an exact cluster of 3) + 1 distinct standalone — a merge that pulls
    ONE member out of the cluster leaves ≥2 members whose canonical must be recomputed."""
    root = Path(get_settings().import_source_root)
    for n in ("d1.txt", "d2.txt", "d3.txt"):
        (root / n).write_text("triple identical retained evidence body content xyz delta echo")
    (root / "lonely.txt").write_text(
        "a wholly distinct standalone purchasing document foxtrot golf hotel"
    )


async def test_merge_recomputes_canonical_of_remaining_cluster_members(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    # Regression (diff-review major): merging a member OUT of a 3-member cluster leaves 2 members
    # whose canonical is recomputed via ctx — those kept members are outside the merge set, so their
    # context must be loaded first (else a KeyError 500). Assert the merge succeeds + cluster → 2.
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_merge_cluster()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    await _drive(uuid.UUID(run_id))

    clusters = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/dupe-clusters", headers=h)
    ).json()["clusters"]
    exact = next(c for c in clusters if c["method"] == "exact")
    assert len(exact["member_file_ids"]) == 3
    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)).json()[
        "files"
    ]
    lonely = next(f["id"] for f in files if f["filename"] == "lonely.txt")

    res = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/merge",
        headers=h,
        json={"file_ids": [exact["member_file_ids"][0], lonely]},
    )
    assert res.status_code == 200, res.text  # no KeyError 500

    after = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/dupe-clusters", headers=h)
    ).json()["clusters"]
    exact_after = next(c for c in after if c["method"] == "exact")
    assert len(exact_after["member_file_ids"]) == 2  # the pulled member is gone
    assert exact_after["canonical_file_id"] in exact_after["member_file_ids"]  # recomputed, valid


async def test_merge_effective_file_id_from_consolidated_family(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    # Regression (diff-review major): effective_file_id may be a member of a TOUCHED family, not in
    # the explicit file_ids — it must validate against the FINAL consolidated set, not the file_ids.
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_dedup_corpus()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    await _drive(uuid.UUID(run_id))

    families = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/version-families", headers=h)
    ).json()["families"]
    fam_members = families[0]["ordered_member_file_ids"]  # the 3-member SOP-FAM family
    a, b = fam_members[0], fam_members[1]
    clusters = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/dupe-clusters", headers=h)
    ).json()["clusters"]
    standalone = next(c for c in clusters if c["method"] == "exact")["canonical_file_id"]

    # merge family-member A + a standalone, choosing effective = family-member B (NOT in file_ids).
    res = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/merge",
        headers=h,
        json={"file_ids": [a, standalone], "effective_file_id": b},
    )
    assert res.status_code == 200, res.text  # B is valid after consolidation (not a 422)

    fams = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/version-families", headers=h)
    ).json()["families"]
    merged = next(f for f in fams if standalone in f["ordered_member_file_ids"])
    assert merged["effective_file_id"] == b  # the human-chosen consolidated member


async def test_decision_rejected_on_non_included_file(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # Regression (diff-review minor): a decision on an excluded/quarantined scan file is meaningless
    # (no proposal node, never commits) → 422.
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()  # Thumbs.db (excluded), draft.tmp (quarantine)
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    await _drive(uuid.UUID(run_id))
    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)).json()[
        "files"
    ]
    excluded = next(f["id"] for f in files if f["filename"] == "Thumbs.db")
    res = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{excluded}/decision",
        headers=h,
        json={"action": "accept", "after": {"kind": "DOCUMENT"}},
    )
    assert res.status_code == 422
