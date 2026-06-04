"""S-ing-1/2 integration: the end-to-end scan->extract->classify pipeline against real PG/MinIO/
Redis, the ``import.*`` execute/review gate split, deny-by-default, the source-root lock (dup-active
409), the setup latch (423), and org isolation (404, never a leak). The stages are driven in-process
(no Celery worker in tests) with a mocked Tika sidecar, the ``services.packs`` build precedent."""

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
from easysynq_api.services.ingestion.extract import run_extract
from easysynq_api.services.ingestion.service import run_scan

from .test_authz import _assign_role, _auth
from .test_records import _grant, _subject


@pytest.fixture(autouse=True)
def _stub_pipeline_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Celery app binds its broker to the default localhost Redis at import time (not the
    testcontainer), and the shared-DB contract is "never trigger real Celery/Beat" — so stub the
    auto-chain enqueues (scan->extract->classify); every test drives the stage bodies directly (the
    packs ``build`` precedent). All THREE ``.delay`` chains are stubbed (a missed one would publish
    to the localhost broker and hang)."""
    from easysynq_api.tasks.ingestion import classify_source, extract_source, scan_source

    for task in (scan_source, extract_source, classify_source):
        monkeypatch.setattr(task, "delay", lambda *a, **k: None)


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
    # Drive the three stages in sequence (the auto-chain enqueues are stubbed).
    async with get_sessionmaker()() as s:
        await run_scan(s, rid)
    async with get_sessionmaker()() as s:
        await run_extract(s, rid)
    async with get_sessionmaker()() as s:
        await run_classify(s, rid)

    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Classified"
    counts = got["counts"]
    assert counts["classified"] == 2  # the SOP + the audit report (both included)
    assert counts["by_kind"]["DOCUMENT"] == 1 and counts["by_kind"]["RECORD"] == 1
    assert "HIGH" in counts["by_band"] and "extract" in counts

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
    async with get_sessionmaker()() as s:
        await run_scan(s, rid)
    async with get_sessionmaker()() as s:
        await run_extract(s, rid)
    async with get_sessionmaker()() as s:
        await run_classify(s, rid)

    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Classified"  # the run completed despite every extract failing
    detail_files = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)
    ).json()["files"]
    sop = next(f for f in detail_files if f["filename"] == "SOP-PUR-002 Purchasing.docx")
    fid = sop["id"]
    full = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{fid}", headers=h)).json()
    assert full["extract"]["status"] == "failed"  # extraction recorded as failed
    assert sop["classification"]["type_code"] == "SOP"  # still typed from the filename doc-code


async def test_reaper_fails_run_with_dead_lock(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # The lock-liveness reaper: an in-progress run whose lock has lapsed (dead worker) is FAILED.
    # Drive a run to Scanned (lock held), advance to Extracting, force-free the lock, then reap.
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
            sa.text("UPDATE import_run SET status='Extracting' WHERE id = :i"), {"i": rid}
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
