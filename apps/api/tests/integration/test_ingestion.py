"""S-ing-1/2/3 integration: the end-to-end scan->extract->classify->dedup->propose pipeline against
real PG/MinIO/Redis, the ``import.*`` execute/review gate split, deny-by-default, the source-root
lock (dup-active 409), the setup latch (423), and org isolation (404, never a leak). The stages are
driven in-process (no Celery worker in tests) with a mocked Tika sidecar, the ``services.packs``
build precedent."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import ActorType, EventType
from easysynq_api.db.models._ingestion_enums import (
    ImportCommitResultStatus,
    ImportDecisionAction,
    ImportRunStatus,
)
from easysynq_api.db.models._signature_enums import SignatureMeaning, SignedObjectType
from easysynq_api.db.models._vault_enums import DocumentCurrentState, DocumentKind, VersionState
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.clause_mapping import ClauseMapping
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.import_file import ImportFile
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.domain.ingestion.extractor import ExtractInput, ExtractResult
from easysynq_api.services.ingestion import commit as commit_svc
from easysynq_api.services.ingestion import repository as ingestion_repo
from easysynq_api.services.ingestion import review as review_svc
from easysynq_api.services.ingestion import storage as ingestion_storage
from easysynq_api.services.ingestion.classify import run_classify
from easysynq_api.services.ingestion.commit import run_commit
from easysynq_api.services.ingestion.dedup import run_dedup
from easysynq_api.services.ingestion.extract import run_extract
from easysynq_api.services.ingestion.propose import run_propose
from easysynq_api.services.ingestion.service import run_scan
from easysynq_api.services.vault.staged_identity import (
    StagedObjectRef,
    StagedSourceUnavailable,
    StagedVersionLocator,
    StorageStage,
    StorageUnavailable,
    UploadIdentityMismatch,
)

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
        commit_source,
        dedup_source,
        extract_source,
        propose_source,
        scan_source,
    )

    for task in (
        scan_source,
        extract_source,
        classify_source,
        dedup_source,
        propose_source,
        commit_source,
    ):
        monkeypatch.setattr(task, "delay", lambda *a, **k: None)


async def _drive(rid: uuid.UUID) -> None:
    """Drive the full in-process pipeline scan→extract→classify→dedup→propose (each stage on its own
    session, mirroring the worker's per-task session). Stops at Proposed — commit is operator-driven
    (``_drive_commit``)."""
    for stage in (run_scan, run_extract, run_classify, run_dedup, run_propose):
        async with get_sessionmaker()() as s:
            await stage(s, rid)


async def _drive_commit(rid: uuid.UUID) -> None:
    """Drive the S-ing-5 commit body in-process on a fresh session (the run must already be in
    ``Committing`` — the POST /commit endpoint flips it; the ``.delay`` enqueue is stubbed here)."""
    await run_commit(get_sessionmaker(), rid)


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
        ing.commit_source.name,
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


def _seed_classifiable(*, content_suffix: str = "") -> None:
    """Seed a clear SOP (DOCUMENT) + audit report (RECORD) under IA folders for the classifier."""
    root = Path(get_settings().import_source_root)
    proc = root / "Procedures"
    proc.mkdir(exist_ok=True)
    (proc / "SOP-PUR-002 Purchasing.docx").write_text(
        "Standard Operating Procedure Purchasing. supplier and purchasing process steps and "
        f"responsibilities. Revision History. Approved by J Smith{content_suffix}"
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


async def test_extract_pinned_versioned_locator_survives_canonical_overwrite(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
) -> None:
    """Extraction reads the scan-pinned version, not a later latest-version overwrite."""
    import boto3

    admin = _subject("avery-version-pin")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    subdir = f"version-pin-{uuid.uuid4().hex[:8]}"
    source = Path(get_settings().import_source_root) / subdir
    source.mkdir(parents=True)
    original = "the scan-pinned approved procedure"
    (source / "Pinned Procedure.txt").write_text(original)
    run_id = (
        await app_client.post(
            "/api/v1/admin/imports", headers=headers, json={"source_root": subdir}
        )
    ).json()["id"]
    rid = uuid.UUID(run_id)

    async with get_sessionmaker()() as session:
        await run_scan(session, rid)
    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=headers)).json()[
        "files"
    ]
    scanned = files[0]
    uri = scanned["staged_blob_uri"]
    parsed = urlsplit(uri)
    assert parsed.scheme == "s3"
    query = parse_qs(parsed.query, strict_parsing=True)
    assert set(query) == {"versionId"}
    assert len(query["versionId"]) == 1
    assert query["versionId"][0] not in {"", "null"}

    settings = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    client.put_object(
        Bucket=settings.s3_bucket_import_staging,
        Key=scanned["sha256"],
        Body=b"later malicious overwrite",
        ContentType="text/plain",
    )

    async with get_sessionmaker()() as session:
        await run_extract(session, rid)
    detail = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{scanned['id']}", headers=headers
        )
    ).json()
    assert detail["extract"]["full_text"] == original


async def test_prechange_staged_blob_uri_fails_closed_and_preserves_review_state(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
) -> None:
    """A legacy unversioned locator becomes a per-file failure without losing reviewability."""
    admin = _subject("avery-legacy-locator")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    subdir = f"legacy-locator-{uuid.uuid4().hex[:8]}"
    source = Path(get_settings().import_source_root) / subdir
    source.mkdir(parents=True)
    (source / "Legacy Procedure.txt").write_text("legacy approved procedure content")
    run_id = (
        await app_client.post(
            "/api/v1/admin/imports", headers=headers, json={"source_root": subdir}
        )
    ).json()["id"]
    rid = uuid.UUID(run_id)

    async with get_sessionmaker()() as session:
        await run_scan(session, rid)
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                sa.text("SELECT id, sha256 FROM import_file WHERE run_id = :run"), {"run": rid}
            )
        ).one()
        file_id, sha = row
        await session.execute(
            sa.text("UPDATE import_file SET staged_blob_uri = :uri WHERE id = :file"),
            {
                "file": file_id,
                "uri": f"s3://{get_settings().s3_bucket_import_staging}/{sha}",
            },
        )
        await session.commit()

    for stage in (run_extract, run_classify, run_dedup, run_propose):
        async with get_sessionmaker()() as session:
            await stage(session, rid)

    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)).json()
    detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{file_id}", headers=headers)
    ).json()
    assert run["status"] == "Proposed"
    assert detail["extract"]["status"] == "failed"
    assert detail["extract"]["error"] == "staging_version_required"
    assert detail["review"]["effective"]["disposition"] == "undecided"
    assert detail["review"]["effective"]["kind"] == "UNCONFIRMED"


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


async def test_commit_never_persists_the_new_identifier_sentinel_as_legacy(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    """[Batch 10] A freshly-ALLOCATED imported document must not carry the ``"{TYPE}-<new>"``
    sentinel as ``legacy_identifier``. The sentinel is not a real legacy code: it was indexed into
    search (weight C), polluted provenance, and could collide across imports via
    ``vault_identifier_collisions``. Only a REAL source code is preserved.
    Mutation-distinguishing: on the old code legacy_identifier == the sentinel."""
    admin = _subject("avery-legacy")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)

    # An OWN source subtree (never the shared corpus — an extra file there would shift the exact
    # commit counts other tests assert) holding ONE procedure with NO doc code in its name/header,
    # so propose suggests the "{TYPE}-<new>" sentinel and commit must allocate a fresh identifier.
    sub = f"batch10-{uuid.uuid4().hex[:8]}"
    folder = Path(get_settings().import_source_root) / sub / "Procedures"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "purchasing procedure.docx").write_text(
        "Standard Operating Procedure Purchasing. supplier and purchasing process steps and "
        "responsibilities. Revision History. Approved by J Smith"
    )
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": sub})
    ).json()["id"]
    await _drive(uuid.UUID(run_id))
    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)).json()[
        "files"
    ]
    target = next(f for f in files if f["filename"] == "purchasing procedure.docx")
    proposed = target["review"]["identifier"]
    assert proposed is not None and proposed.endswith("-<new>"), (
        f"precondition: expected the suggested_default sentinel, got {proposed!r}"
    )
    assert target["review"]["identifier_source"] == "suggested_default"

    # Confirm it as a DOCUMENT *without* overriding the identifier, so identifier_source stays
    # suggested_default and commit takes the ALLOCATE branch (the one that wrote the sentinel).
    assert (
        await app_client.post(
            f"/api/v1/admin/imports/{run_id}/files/{target['id']}/decision",
            headers=h,
            json={"action": "correct", "after": {"kind": "DOCUMENT", "clause_numbers": ["8.4"]}},
        )
    ).status_code == 200
    assert (
        await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    ).status_code == 202
    await _drive_commit(uuid.UUID(run_id))

    async with get_sessionmaker()() as s:
        allocated = (
            await s.execute(
                select(DocumentedInformation).where(
                    DocumentedInformation.import_provenance["run_id"].astext == run_id
                )
            )
        ).scalar_one()
        assert allocated.legacy_identifier is None, (
            f"the {proposed!r} sentinel must never be persisted as legacy_identifier"
        )
        assert "<new>" not in allocated.identifier


async def test_reaper_spares_a_long_but_progressing_run(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """[Batch 10] The absolute backstop must be anchored on STAGE PROGRESS, not the pipeline-start
    ``scan_started_at``. A large OCR import legitimately runs past the stall window: with a live
    lock AND a fresh ``import_extract`` row it is progressing and must be SPARED. Anchoring on the
    start time FAILed it mid-flight (and force-freed its lock), so such a run could never complete.
    Mutation-distinguishing: on the old code `too_old` is True and the run is reaped → FAILED."""
    from easysynq_api.services.ingestion.service import reap_stalled_runs

    admin = _subject("avery-progress")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_classifiable()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    rid = uuid.UUID(run_id)
    async with get_sessionmaker()() as s:
        await run_scan(s, rid)  # → Scanned, lock HELD (a live worker)

    async with get_sessionmaker()() as s:
        # Backdate the pipeline start well past the window, then record FRESH stage progress — the
        # exact shape of a long-running OCR import that is still working through its files.
        await s.execute(
            sa.text(
                "UPDATE import_run SET status='Extracting', "
                "scan_started_at = now() - interval '10 hours' WHERE id = :i"
            ),
            {"i": rid},
        )
        file_id = (
            await s.execute(
                sa.text("SELECT id FROM import_file WHERE run_id = :i LIMIT 1"), {"i": rid}
            )
        ).scalar_one()
        org_id = (
            await s.execute(sa.text("SELECT org_id FROM import_run WHERE id = :i"), {"i": rid})
        ).scalar_one()
        await s.execute(
            sa.text(
                "INSERT INTO import_extract (id, org_id, run_id, file_id, status, created_at) "
                "VALUES (:eid, :org, :run, :fid, 'extracted', now())"
            ),
            {"eid": uuid.uuid4(), "org": org_id, "run": rid, "fid": file_id},
        )
        await s.commit()

    async with get_sessionmaker()() as s:
        await reap_stalled_runs(s, max_age_seconds=3600)  # a 1h window vs a 10h-old start
    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Extracting", "a progressing run must not be reaped"
    assert got["error"] is None


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
    app_client: AsyncClient,
    h: dict[str, str],
    _stub_tika: None,
    *,
    content_suffix: str = "",
) -> tuple[str, dict[str, dict]]:
    """Drive a classifiable corpus to Proposed; return (run_id, {filename: file_row})."""
    _seed_classifiable(content_suffix=content_suffix)
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


async def test_bulk_accept_all_high_preserves_prior_file_decision(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]

    excluded = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=h,
        json={"action": "exclude", "reason": "explicitly out of scope"},
    )
    assert excluded.status_code == 200, excluded.text

    # Both files are HIGH, but a broad selector must not supersede the more-specific human choice.
    accepted = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/decisions",
        headers=h,
        json={"action": "accept", "selector": {"band": "HIGH"}},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["applied"] == 1
    assert accepted.json()["skipped_prior_decisions"] == 1

    sop_detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{sop}", headers=h)
    ).json()
    assert sop_detail["review"]["effective"]["disposition"] == "excluded"
    assert [d["action"] for d in sop_detail["review"]["decision_history"]] == ["exclude"]

    audit_detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{audit}", headers=h)
    ).json()
    assert audit_detail["review"]["effective"]["disposition"] == "included"
    assert [d["action"] for d in audit_detail["review"]["decision_history"]] == ["accept"]

    # A selector whose complete match set is already decided is a calm no-op, not a fabricated
    # decision/event. Explicit file_ids remain available when a reviewer truly wants to overwrite.
    async with get_sessionmaker()() as session:
        audit_before = (
            await session.execute(
                select(sa.func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == uuid.UUID(run_id),
                    AuditEvent.event_type == EventType.IMPORT_DECISION_RECORDED,
                )
            )
        ).scalar_one()
    no_op = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/decisions",
        headers=h,
        json={"action": "accept", "selector": {"kind": "DOCUMENT"}},
    )
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["applied"] == 0
    assert no_op.json()["skipped_prior_decisions"] == 1
    decisions = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/decisions", headers=h)
    ).json()["decisions"]
    assert len(decisions) == 2
    async with get_sessionmaker()() as session:
        audit_after = (
            await session.execute(
                select(sa.func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == uuid.UUID(run_id),
                    AuditEvent.event_type == EventType.IMPORT_DECISION_RECORDED,
                )
            )
        ).scalar_one()
    assert audit_after == audit_before

    overwritten = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/decisions",
        headers=h,
        json={"action": "accept", "file_ids": [sop]},
    )
    assert overwritten.status_code == 200, overwritten.text
    assert overwritten.json()["applied"] == 1
    assert overwritten.json()["skipped_prior_decisions"] == 0
    sop_after_explicit = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{sop}", headers=h)
    ).json()
    assert sop_after_explicit["review"]["effective"]["disposition"] == "included"


async def test_bulk_selector_rejects_non_candidate_matches(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()  # disposition=excluded rows are deliberately not import candidates.
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]
    await _drive(uuid.UUID(run_id))

    rejected = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/decisions",
        headers=h,
        json={"action": "accept", "selector": {"disposition": "excluded"}},
    )
    assert rejected.status_code == 422, rejected.text
    assert "not an included candidate" in rejected.json()["title"]

    decisions = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/decisions", headers=h)
    ).json()["decisions"]
    assert decisions == []  # validation is atomic; no matched junk row receives a decision.
    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run["status"] == "Proposed"


async def test_bulk_selector_rejects_oversized_match_set(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, _ = await _proposed_classifiable(app_client, h, _stub_tika)
    settings = get_settings().model_copy(update={"import_bulk_decision_max": 1})
    monkeypatch.setattr(review_svc, "get_settings", lambda: settings)

    # The empty selector matches both included files. The old limit=1 query silently mutated one.
    rejected = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/decisions",
        headers=h,
        json={"action": "accept", "selector": {}},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["title"] == "bulk selection exceeds max 1"

    decisions = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/decisions", headers=h)
    ).json()["decisions"]
    assert decisions == []
    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run["status"] == "Proposed"


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


async def test_split_version_family_preserves_surviving_human_effective_member(
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

    families = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/version-families", headers=h)
    ).json()["families"]
    first, chosen, separated = families[0]["ordered_member_file_ids"]

    merged = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/merge",
        headers=h,
        json={
            "file_ids": [first, chosen],
            "effective_file_id": chosen,
            "reconstruct_revision_chain": True,
        },
    )
    assert merged.status_code == 200, merged.text
    family_id = merged.json()["family_id"]

    split = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/split",
        headers=h,
        json={
            "target_kind": "version_family",
            "target_id": family_id,
            "separate_file_ids": [separated],
        },
    )
    assert split.status_code == 200, split.text

    after = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/version-families", headers=h)
    ).json()["families"]
    survivor = next(f for f in after if f["id"] == family_id)
    assert survivor["ordered_member_file_ids"] == [first, chosen]
    assert survivor["effective_file_id"] == chosen  # not reset to total-order member `first`
    assert survivor["reconstruct_revision_chain"] is True


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


# --------------------------------------------------------------------------- S-ing-5 commit


async def _confirm_for_commit(
    app_client: AsyncClient,
    h: dict[str, str],
    run_id: str,
    sop_id: str,
    audit_id: str,
    *,
    doc_identifier: str,
    doc_owner: str | None = None,
    audit_kind: str = "RECORD",
    audit_after: dict[str, object] | None = None,
) -> None:
    """Confirm the SOP as a DOCUMENT with a per-test-UNIQUE identifier (the shared-DB collision
    guard) + the audit per ``audit_kind``/``audit_after`` — making the run commit-ready."""
    doc_after = {"kind": "DOCUMENT", "identifier": doc_identifier, "clause_numbers": ["8.4"]}
    if doc_owner is not None:
        doc_after["owner"] = doc_owner
    r1 = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop_id}/decision",
        headers=h,
        json={
            "action": "correct",
            "after": doc_after,
        },
    )
    assert r1.status_code == 200, r1.text
    r2 = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{audit_id}/decision",
        headers=h,
        json={
            "action": "accept" if audit_after is None else "correct",
            "after": audit_after or {"kind": audit_kind},
        },
    )
    assert r2.status_code == 200, r2.text


async def _seed_deleted_restage_source(run_id: uuid.UUID, file_id: uuid.UUID) -> StagedObjectRef:
    """Model the durable state after a prior exact-version refusal and cleanup."""
    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, run_id)
        file = await session.get(ImportFile, file_id)
        assert run is not None and file is not None
        assert file.sha256 is not None and file.staged_blob_uri is not None
        source = ingestion_storage.parse_staged_uri(
            file.staged_blob_uri,
            expected_sha256=file.sha256,
            content_type=file.mime_type or "application/octet-stream",
            expected_size=file.size_bytes,
        )
        won = await ingestion_repo.record_failed_result(
            session,
            org_id=run.org_id,
            run_id=run_id,
            file_id=file_id,
            error="upload_identity_digest_mismatch",
            expected_committed_at=None,
        )
        assert won is True
        await session.commit()
    await commit_svc.storage.delete_staged_version(source.locator)
    return source


async def test_commit_document_revalidates_blob_after_insert_conflict(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An import first writer must validate the global-sha conflict winner.

    Force the first lookup stale to model a records-domain insert committing after both
    transactions observed no Blob. Without the post-insert re-read, import creates an Effective
    document version whose source FK resolves to records-retention bytes.
    """
    admin = _subject("avery-blob-race")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        h,
        _stub_tika,
        content_suffix=f" blob-race-{uuid.uuid4().hex}",
    )
    sop_row = by_name["SOP-PUR-002 Purchasing.docx"]
    sop = sop_row["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    sha = sop_row["sha256"]
    assert isinstance(sha, str)
    identifier = f"SOP-{uuid.uuid4().hex[:6].upper()}-001"
    await _confirm_for_commit(
        app_client,
        h,
        run_id,
        sop,
        audit,
        doc_identifier=identifier,
    )

    async with get_sessionmaker()() as s:
        committer = (
            await s.execute(select(AppUser).where(AppUser.keycloak_subject == admin))
        ).scalar_one()
        s.add(
            Blob(
                sha256=sha,
                org_id=committer.org_id,
                size_bytes=10,
                mime_type="application/pdf",
                bucket=get_settings().s3_bucket_records,
                object_key=sha,
                worm_locked=True,
            )
        )
        await s.commit()

    original_get_blob = commit_svc.vault_repo.get_blob
    target_reads = 0

    async def stale_then_authoritative(
        session: AsyncSession,
        requested_sha: str,
    ) -> Blob | None:
        nonlocal target_reads
        if requested_sha == sha:
            target_reads += 1
            if target_reads == 1:
                return None
        return await original_get_blob(session, requested_sha)

    monkeypatch.setattr(commit_svc.vault_repo, "get_blob", stale_then_authoritative)
    try:
        started = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
        assert started.status_code == 202, started.text
        await _drive_commit(uuid.UUID(run_id))

        run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
        assert run["status"] == "PartiallyCommitted"
        assert run["counts"]["commit"] == {"committed": 1, "failed": 1}
        sop_detail = (
            await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{sop}", headers=h)
        ).json()
        assert sop_detail["commit"]["result"] == "failed"
        assert "source_bytes_in_foreign_bucket" in sop_detail["commit"]["error"]
        assert target_reads == 2

        async with get_sessionmaker()() as s:
            blob = await s.get(Blob, sha)
            assert blob is not None and blob.bucket == get_settings().s3_bucket_records
            versions = (
                await s.execute(
                    select(sa.func.count())
                    .select_from(DocumentVersion)
                    .where(DocumentVersion.source_blob_sha256 == sha)
                )
            ).scalar_one()
            assert versions == 0
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(sa.delete(Blob).where(Blob.sha256 == sha))
            await s.commit()


async def test_commit_writes_documents_records_and_generated_report_to_vault(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    tag = uuid.uuid4().hex[:6].upper()
    doc_ident = f"SOP-{tag}-001"
    # Directory subjects can themselves be UUID-shaped and need not equal the app_user primary key.
    owner_subject = str(uuid.uuid4())
    owner_id = await _grant(owner_subject, ())
    assert owner_id != uuid.UUID(owner_subject)
    await _confirm_for_commit(
        app_client,
        h,
        run_id,
        sop,
        audit,
        doc_identifier=doc_ident,
        doc_owner=str(owner_id),
        audit_after={"kind": "RECORD", "owner": owner_subject},
    )

    chk = (await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)).json()
    assert chk["ready"] is True, chk["blocking"]

    original_put = commit_svc.storage.put_staging_bytes
    original_promote = commit_svc.storage.promote_worm
    report_source: object | None = None
    report_promotions = 0

    async def capture_report_source(data: bytes, sha256: str, *, content_type: str) -> object:
        nonlocal report_source
        source = await original_put(data, sha256, content_type=content_type)
        if content_type == "text/markdown":
            report_source = source
        return source

    async def require_exact_report_source(source: object, *, target_bucket: str) -> object:
        nonlocal report_promotions
        if getattr(source, "content_type", None) == "text/markdown":
            assert source is report_source
            report_promotions += 1
        return await original_promote(source, target_bucket=target_bucket)  # type: ignore[arg-type]

    monkeypatch.setattr(commit_svc.storage, "put_staging_bytes", capture_report_source)
    monkeypatch.setattr(commit_svc.storage, "promote_worm", require_exact_report_source)
    commit = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert commit.status_code == 202, commit.text
    assert commit.json()["status"] == "Committing"
    await _drive_commit(uuid.UUID(run_id))

    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run["status"] == "Completed"
    assert run["counts"]["commit"] == {"committed": 2, "failed": 0}
    assert run["report_record_id"]
    assert report_promotions == 1

    async with get_sessionmaker()() as s:
        doc = (
            await s.execute(
                select(DocumentedInformation).where(DocumentedInformation.identifier == doc_ident)
            )
        ).scalar_one()
        assert doc.current_state == DocumentCurrentState.Effective
        assert doc.kind == DocumentKind.DOCUMENT
        assert doc.owner_user_id == owner_id
        assert doc.import_provenance and doc.import_provenance["run_id"] == run_id
        assert doc.import_provenance["source_sha256"]
        assert doc.current_effective_version_id is not None
        # S-drift-1: import-baseline opt-out — imported docs skip the create-default so the
        # owner can choose whether to enrol them in the re-review schedule post-import.
        assert doc.review_period_months is None
        assert doc.next_review_due is None
        ver = (
            await s.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id))
        ).scalar_one()
        assert ver.version_state == VersionState.Effective
        assert ver.imported is True and ver.revision_label == "Rev A"
        # the import_baseline signature (R2) on the version, signed by the committer, bound to
        # bytes.
        committer_id = (
            await s.execute(select(AppUser.id).where(AppUser.keycloak_subject == admin))
        ).scalar_one()
        sig = (
            await s.execute(
                select(SignatureEvent).where(
                    SignatureEvent.signed_object_id == ver.id,
                    SignatureEvent.meaning == SignatureMeaning.import_baseline,
                )
            )
        ).scalar_one()
        assert sig.signer_user_id == committer_id
        assert sig.content_digest == ver.source_blob_sha256
        assert sig.signed_object_type == SignedObjectType.document_version
        # the per-doc audit (AC#6): IMPORT_ITEM_COMMITTED keyed to the doc + scope_ref=identifier.
        ev = (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.object_id == doc.id,
                    AuditEvent.event_type == EventType.IMPORT_ITEM_COMMITTED,
                )
            )
        ).scalar_one()
        assert ev.scope_ref == doc_ident
        # the folded clause mapping (8.4) was materialized.
        mappings = (
            (
                await s.execute(
                    select(ClauseMapping).where(ClauseMapping.documented_information_id == doc.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(mappings) >= 1
        # the §12.1 Import Report record is a RETAIN_PERMANENT EVIDENCE record.
        report = await s.get(DocumentedInformation, uuid.UUID(run["report_record_id"]))
        assert report is not None and report.kind == DocumentKind.RECORD
        assert report.title.startswith("Import Report")
        # the mirror enumeration finds it (drives current/_ImportReport/).
        from easysynq_api.services.vault.mirror import fetch_import_reports

        reports = await fetch_import_reports(s)
        assert any(uuid.UUID(run_id).hex[:8] in r.label for r in reports)

    # the RECORD was captured (via the file-detail commit sub-object → its vault_document_id).
    audit_detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{audit}", headers=h)
    ).json()
    assert audit_detail["commit"]["result"] == "success"
    rec_id = audit_detail["commit"]["vault_document_id"]
    assert audit_detail["commit"]["vault_version_id"] is None  # records have no document_version
    async with get_sessionmaker()() as s:
        rec = await s.get(DocumentedInformation, uuid.UUID(rec_id))
        assert rec is not None and rec.kind == DocumentKind.RECORD
        assert rec.owner_user_id == owner_id
        assert rec.import_provenance and rec.import_provenance["run_id"] == run_id
        # R2: a RECORD is captured, NOT released — NO import_baseline signature (the asymmetry).
        rec_sigs = (
            (
                await s.execute(
                    select(SignatureEvent).where(
                        SignatureEvent.signed_object_id == rec.id,
                        SignatureEvent.meaning == SignatureMeaning.import_baseline,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rec_sigs == []

    sop_detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{sop}", headers=h)
    ).json()
    assert sop_detail["commit"]["result"] == "success"
    assert sop_detail["commit"]["vault_version_id"]

    # idempotent: re-running the worker is a no-op (run no longer Committing); re-POST → 409.
    await _drive_commit(uuid.UUID(run_id))
    async with get_sessionmaker()() as s:
        n = (
            await s.execute(
                select(sa.func.count())
                .select_from(DocumentedInformation)
                .where(DocumentedInformation.identifier == doc_ident)
            )
        ).scalar_one()
        assert n == 1  # still exactly one — no duplicate document
    recommit = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert recommit.status_code == 409  # already completed


async def test_generated_report_source_mismatch_preserves_terminal_commit_and_audits_once(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _subject("generated-report-refusal")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        headers,
        _stub_tika,
        content_suffix=f" generated-report-{uuid.uuid4().hex}",
    )
    await _confirm_for_commit(
        app_client,
        headers,
        run_id,
        by_name["SOP-PUR-002 Purchasing.docx"]["id"],
        by_name["Internal Audit Report Q2 2023.pdf"]["id"],
        doc_identifier=f"SOP-{uuid.uuid4().hex[:6].upper()}-001",
    )

    original_put = commit_svc.storage.put_staging_bytes
    original_promote = commit_svc.storage.promote_worm
    original_delete = commit_svc.storage.delete_staged_version
    report_source: StagedObjectRef | None = None
    deleted: list[StagedVersionLocator] = []

    async def capture_report_source(
        data: bytes, sha256: str, *, content_type: str
    ) -> StagedObjectRef:
        nonlocal report_source
        source = await original_put(data, sha256, content_type=content_type)
        if content_type == "text/markdown":
            report_source = source
        return source

    async def refuse_report(source: StagedObjectRef, *, target_bucket: str) -> object:
        if source.content_type == "text/markdown":
            assert source is report_source
            raise UploadIdentityMismatch(
                source=source,
                expected_sha256=source.expected_sha256,
                observed_sha256="f" * 64,
                expected_size=source.expected_size,
                observed_size=source.expected_size,
                etag='"etag"',
                classification="digest_mismatch",
            )
        return await original_promote(source, target_bucket=target_bucket)

    async def capture_exact_delete(locator: StagedVersionLocator) -> None:
        assert report_source is not None
        assert locator is report_source.locator
        deleted.append(locator)
        await original_delete(locator)

    monkeypatch.setattr(commit_svc.storage, "put_staging_bytes", capture_report_source)
    monkeypatch.setattr(commit_svc.storage, "promote_worm", refuse_report)
    monkeypatch.setattr(commit_svc.storage, "delete_staged_version", capture_exact_delete)

    started = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    assert started.status_code == 202, started.text
    await _drive_commit(uuid.UUID(run_id))

    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)).json()
    assert run["status"] == "Completed"
    assert run["counts"]["commit"] == {"committed": 2, "failed": 0}
    assert run["report_record_id"] is None
    assert report_source is not None
    assert deleted == [report_source.locator]
    with pytest.raises(StagedSourceUnavailable):
        await commit_svc.storage.verify_staged(report_source)

    async with get_sessionmaker()() as session:
        report_owner_count = await session.scalar(
            select(sa.func.count())
            .select_from(DocumentedInformation)
            .where(DocumentedInformation.title.contains(run_id))
        )
        integrity_events = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED,
                        AuditEvent.scope_ref == run_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert report_owner_count == 0
        assert len(integrity_events) == 1
        assert integrity_events[0].actor_type is ActorType.system
        assert integrity_events[0].actor_id is None


async def test_precommit_blocks_legacy_role_label_owner_until_corrected(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    """A persisted pre-directory-picker owner stays reviewable instead of becoming stuck partial."""
    admin = _subject("legacy-owner")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    doc_ident = f"SOP-{uuid.uuid4().hex[:6].upper()}-001"
    await _confirm_for_commit(app_client, h, run_id, sop, audit, doc_identifier=doc_ident)

    # Model an append-only decision written by the old stub menu, whose sole choice was the
    # persona/role label "Quality Manager" rather than a concrete directory identity.
    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, uuid.UUID(run_id))
        reviewer = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == admin))
        ).scalar_one()
        assert run is not None
        await ingestion_repo.insert_decision(
            session,
            org_id=run.org_id,
            run_id=run.id,
            action=ImportDecisionAction.CORRECT,
            decided_by=reviewer.id,
            file_id=uuid.UUID(sop),
            target_kind="file",
            after={"owner": "Quality Manager"},
        )
        await session.commit()

    checklist = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)
    ).json()
    owner_blocks = [item for item in checklist["blocking"] if item["type"] == "owner_not_found"]
    assert checklist["ready"] is False
    assert owner_blocks == [
        {
            "type": "owner_not_found",
            "owner": "Quality Manager",
            "file_id": sop,
            "resolved": False,
        }
    ]

    blocked = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["code"] == "commit_blocked"
    still_reviewing = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert still_reviewing["status"] == "Reviewing"

    # The new directory picker can append a concrete correction while the run is still reviewable.
    owner_id = await _grant(_subject("replacement-owner"), ())
    corrected = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=h,
        json={"action": "correct", "after": {"owner": str(owner_id)}},
    )
    assert corrected.status_code == 200, corrected.text
    repaired = (await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)).json()
    assert repaired["ready"] is True, repaired["blocking"]
    decisions_before_commit = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/decisions", headers=h)
    ).json()["decisions"]

    commit = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert commit.status_code == 202, commit.text
    decisions_after_commit = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/decisions", headers=h)
    ).json()["decisions"]
    checklist_after_commit = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)
    ).json()
    assert decisions_after_commit == decisions_before_commit
    assert checklist_after_commit["review"] == repaired["review"]

    # The commit boundary snapshots the reviewed ID. A lifecycle change after the API transaction
    # but before the detached worker starts must not turn the non-reviewable run into a permanently
    # stuck PartiallyCommitted run.
    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, uuid.UUID(run_id))
        assert run is not None
        assert run.commit_owner_snapshot == {sop: str(owner_id)}
        owner = await session.get(AppUser, owner_id)
        assert owner is not None
        owner.status = UserStatus.DISABLED
        await session.commit()

    await _drive_commit(uuid.UUID(run_id))
    completed = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert completed["status"] == "Completed"
    async with get_sessionmaker()() as session:
        doc = (
            await session.execute(
                select(DocumentedInformation).where(DocumentedInformation.identifier == doc_ident)
            )
        ).scalar_one()
        assert doc.owner_user_id == owner_id


async def test_partial_resume_allows_uncommitted_owner_correction(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    """A migrated partial run with a legacy owner label can be repaired and resumed."""
    admin = _subject("partial-owner-correction")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    doc_ident = f"SOP-{uuid.uuid4().hex[:6].upper()}-001"
    await _confirm_for_commit(app_client, h, run_id, sop, audit, doc_identifier=doc_ident)

    # Model an old deployment: the invalid legacy decision made it past review, the worker imported
    # the other file, and this owner failed live resolution before run-level snapshots existed.
    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, uuid.UUID(run_id))
        reviewer = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == admin))
        ).scalar_one()
        assert run is not None
        await ingestion_repo.insert_decision(
            session,
            org_id=run.org_id,
            run_id=run.id,
            action=ImportDecisionAction.CORRECT,
            decided_by=reviewer.id,
            file_id=uuid.UUID(sop),
            target_kind="file",
            after={"owner": "Quality Manager"},
        )
        run.status = ImportRunStatus.COMMITTING
        run.committed_by = reviewer.id
        run.commit_owner_snapshot = None
        await session.commit()
    await _drive_commit(uuid.UUID(run_id))

    partial = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert partial["status"] == "PartiallyCommitted"
    assert partial["counts"]["commit"] == {"committed": 1, "failed": 1}

    blocked = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["blocking"] == [
        {
            "type": "owner_not_found",
            "owner": "Quality Manager",
            "file_id": sop,
            "resolved": False,
        }
    ]
    assert (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()[
        "status"
    ] == "PartiallyCommitted"

    owner_id = await _grant(_subject("partial-replacement-owner"), ())
    corrected = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=h,
        json={"action": "correct", "after": {"owner": str(owner_id)}},
    )
    assert corrected.status_code == 200, corrected.text
    assert (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()[
        "status"
    ] == "PartiallyCommitted"

    immutable = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{audit}/decision",
        headers=h,
        json={"action": "correct", "after": {"owner": str(owner_id)}},
    )
    assert immutable.status_code == 409, immutable.text
    assert immutable.json()["title"] == "This import item is already committed"

    resumed = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert resumed.status_code == 202, resumed.text
    await _drive_commit(uuid.UUID(run_id))

    completed = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert completed["status"] == "Completed"
    assert completed["counts"]["commit"] == {"committed": 2, "failed": 0}
    async with get_sessionmaker()() as session:
        doc = (
            await session.execute(
                select(DocumentedInformation).where(DocumentedInformation.identifier == doc_ident)
            )
        ).scalar_one()
        assert doc.owner_user_id == owner_id


async def test_partial_resume_snapshots_a_disabled_stable_owner_id(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    """Pre-snapshot partial runs preserve an exact reviewed ID across lifecycle changes."""
    admin = _subject("partial-disabled-owner")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    doc_ident = f"SOP-{uuid.uuid4().hex[:6].upper()}-001"
    owner_id = await _grant(_subject("partial-disabled-identity"), ())
    await _confirm_for_commit(
        app_client,
        h,
        run_id,
        sop,
        audit,
        doc_identifier=doc_ident,
        doc_owner=str(owner_id),
    )

    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, uuid.UUID(run_id))
        owner = await session.get(AppUser, owner_id)
        assert run is not None and owner is not None
        run.status = ImportRunStatus.PARTIALLY_COMMITTED
        run.commit_owner_snapshot = None
        owner.status = UserStatus.DISABLED
        await session.commit()

    resumed = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert resumed.status_code == 202, resumed.text
    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, uuid.UUID(run_id))
        assert run is not None
        assert run.commit_owner_snapshot == {sop: str(owner_id)}

    await _drive_commit(uuid.UUID(run_id))
    completed = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert completed["status"] == "Completed"
    async with get_sessionmaker()() as session:
        doc = (
            await session.execute(
                select(DocumentedInformation).where(DocumentedInformation.identifier == doc_ident)
            )
        ).scalar_one()
        assert doc.owner_user_id == owner_id


async def test_blank_identifier_decision_is_rejected_before_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]

    response = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=h,
        json={"action": "correct", "after": {"kind": "DOCUMENT", "identifier": " \t "}},
    )

    assert response.status_code == 422, response.text
    assert response.json()["title"] == "identifier must not be blank"


async def test_precommit_and_boundary_block_legacy_persisted_blank_identifier(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    """Legacy blanks block review exit and remain guarded if preflight is bypassed."""
    admin = _subject("legacy-blank")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]

    # Confirm the other item through today's API, then insert a pre-validation decision directly
    # through the append-only repository to model data written by an older deployment.
    audit_decision = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{audit}/decision",
        headers=h,
        json={"action": "accept", "after": {"kind": "RECORD"}},
    )
    assert audit_decision.status_code == 200, audit_decision.text
    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, uuid.UUID(run_id))
        reviewer = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == admin))
        ).scalar_one()
        assert run is not None
        await ingestion_repo.insert_decision(
            session,
            org_id=run.org_id,
            run_id=run.id,
            action=ImportDecisionAction.CORRECT,
            decided_by=reviewer.id,
            file_id=uuid.UUID(sop),
            target_kind="file",
            after={"kind": "DOCUMENT", "identifier": " \t "},
        )
        await session.commit()

    checklist = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)
    ).json()
    assert checklist["ready"] is False
    assert [item for item in checklist["blocking"] if item["type"] == "blank_identifier"] == [
        {
            "type": "blank_identifier",
            "identifier": " \t ",
            "file_id": sop,
            "resolved": False,
        }
    ]
    commit = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert commit.status_code == 422, commit.text
    assert commit.json()["code"] == "commit_blocked"
    assert (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()[
        "status"
    ] == "Reviewing"

    # Defense in depth: model an old/in-flight deployment that bypassed the new checklist. The
    # write boundary still refuses the blank identifier and records an isolated failed item.
    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, uuid.UUID(run_id))
        reviewer = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == admin))
        ).scalar_one()
        assert run is not None
        run.status = ImportRunStatus.COMMITTING
        run.committed_by = reviewer.id
        await session.commit()
    await _drive_commit(uuid.UUID(run_id))

    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run["status"] == "PartiallyCommitted"
    assert run["counts"]["commit"] == {"committed": 1, "failed": 1}
    sop_detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{sop}", headers=h)
    ).json()
    assert sop_detail["commit"]["result"] == "failed"
    assert "blank_identifier" in sop_detail["commit"]["error"]
    async with get_sessionmaker()() as session:
        persisted = (
            await session.execute(
                select(sa.func.count())
                .select_from(DocumentedInformation)
                .where(DocumentedInformation.identifier == " \t ")
            )
        ).scalar_one()
        assert persisted == 0

    repaired_ident = f"SOP-{uuid.uuid4().hex[:6].upper()}-001"
    corrected = await app_client.post(
        f"/api/v1/admin/imports/{run_id}/files/{sop}/decision",
        headers=h,
        json={"action": "correct", "after": {"identifier": repaired_ident}},
    )
    assert corrected.status_code == 200, corrected.text
    resumed = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert resumed.status_code == 202, resumed.text
    await _drive_commit(uuid.UUID(run_id))

    completed = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert completed["status"] == "Completed"
    async with get_sessionmaker()() as session:
        repaired = (
            await session.execute(
                select(DocumentedInformation).where(
                    DocumentedInformation.identifier == repaired_ident
                )
            )
        ).scalar_one()
        assert repaired.identifier == repaired_ident


async def test_record_failed_does_not_audit_after_peer_success(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A success committed after the failure path's read but before its conditional UPSERT wins
    atomically; the losing failure writer must not append a false IMPORT_ITEM_FAILED event."""
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    file_id = uuid.UUID(by_name["SOP-PUR-002 Purchasing.docx"]["id"])
    run_uuid = uuid.UUID(run_id)

    original_record_failed = ingestion_repo.record_failed_result
    peer_inserted = False

    async def _peer_wins_before_failed_upsert(
        session: object,
        *,
        org_id: uuid.UUID,
        run_id: uuid.UUID,
        file_id: uuid.UUID,
        error: str,
        expected_committed_at: object,
    ) -> bool:
        nonlocal peer_inserted
        if not peer_inserted:
            async with get_sessionmaker()() as peer:
                won = await ingestion_repo.claim_commit_result(
                    peer,
                    org_id=org_id,
                    run_id=run_id,
                    file_id=file_id,
                    vault_document_id=None,
                    vault_version_id=None,
                )
                assert won is True
                await peer.commit()
            peer_inserted = True
        return await original_record_failed(
            session,
            org_id=org_id,
            run_id=run_id,
            file_id=file_id,
            error=error,
            expected_committed_at=expected_committed_at,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(commit_svc.repo, "record_failed_result", _peer_wins_before_failed_upsert)

    async with get_sessionmaker()() as session:
        run = await ingestion_repo.get_run(session, run_uuid)
        assert run is not None
        await commit_svc._record_failed(
            session,
            run_uuid,
            run.org_id,
            file_id,
            "simulated_worker_failure",
            expected_committed_at=None,
        )

    async with get_sessionmaker()() as session:
        result = await ingestion_repo.get_commit_result(session, run_uuid, file_id)
        assert result is not None
        assert result.result is ImportCommitResultStatus.SUCCESS
        failure_events = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == run_uuid,
                        AuditEvent.event_type == EventType.IMPORT_ITEM_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert not any(
            (event.after or {}).get("file_id") == str(file_id) for event in failure_events
        )


async def test_commit_partial_then_resume_keeps_committed_item(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    tag = uuid.uuid4().hex[:6].upper()
    good = f"SOP-{tag}-001"
    # The SOP commits; the audit is corrected to a DOCUMENT with a BOGUS type_code resolving to no
    # DocumentType → an isolated per-item failure (unknown_document_type), NOT pre-blocked by the
    # checklist (a bogus type is not a singleton; the identifier is unique).
    await _confirm_for_commit(
        app_client,
        h,
        run_id,
        sop,
        audit,
        doc_identifier=good,
        audit_after={"kind": "DOCUMENT", "type_code": f"ZZ{tag}", "identifier": f"ZZ-{tag}-001"},
    )
    chk = (await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)).json()
    assert chk["ready"] is True, chk["blocking"]

    assert (
        await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    ).status_code == 202
    await _drive_commit(uuid.UUID(run_id))
    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run["status"] == "PartiallyCommitted"
    assert run["counts"]["commit"] == {"committed": 1, "failed": 1}

    audit_detail = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{audit}", headers=h)
    ).json()
    assert audit_detail["commit"]["result"] == "failed"
    assert "unknown_document_type" in audit_detail["commit"]["error"]

    # resume: re-POST is accepted (PartiallyCommitted → Committing) and re-runs idempotently — the
    # already-committed SOP is skipped (no duplicate), the still-bogus audit fails again.
    resume = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert resume.status_code == 202 and resume.json()["status"] == "Committing"
    await _drive_commit(uuid.UUID(run_id))
    run2 = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run2["status"] == "PartiallyCommitted"
    assert run2["counts"]["commit"] == {"committed": 1, "failed": 1}
    async with get_sessionmaker()() as s:
        n = (
            await s.execute(
                select(sa.func.count())
                .select_from(DocumentedInformation)
                .where(DocumentedInformation.identifier == good)
            )
        ).scalar_one()
        assert n == 1  # the committed SOP is not duplicated across the resume


async def test_upload_identity_mismatch_partial_resume_audits_then_deletes_only_rejected_version(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One rejected exact import version cannot taint its honest peer or resume evidence."""
    import boto3
    from botocore.exceptions import ClientError

    admin = _subject("avery-import-identity")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        headers,
        _stub_tika,
        content_suffix=f" identity-{uuid.uuid4().hex}",
    )
    run_uuid = uuid.UUID(run_id)
    bad = by_name["SOP-PUR-002 Purchasing.docx"]
    peer = by_name["Internal Audit Report Q2 2023.pdf"]
    bad_id = uuid.UUID(bad["id"])
    sha = bad["sha256"]
    assert isinstance(sha, str)
    identifier = f"SOP-{uuid.uuid4().hex[:6].upper()}-001"
    await _confirm_for_commit(
        app_client,
        headers,
        run_id,
        bad["id"],
        peer["id"],
        doc_identifier=identifier,
    )

    before_bad = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{bad['id']}", headers=headers)
    ).json()
    settings = get_settings()
    source_path = Path(settings.import_source_root) / bad["rel_path"]
    honest_bytes = source_path.read_bytes()
    false_bytes = b"X" * len(honest_bytes)
    assert false_bytes != honest_bytes
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    refused_put = client.put_object(
        Bucket=settings.s3_bucket_import_staging,
        Key=sha,
        Body=false_bytes,
        ContentType=bad["mime_type"],
    )
    refused_version = refused_put["VersionId"]
    newer_put = client.put_object(
        Bucket=settings.s3_bucket_import_staging,
        Key=sha,
        Body=honest_bytes,
        ContentType=bad["mime_type"],
    )
    newer_version = newer_put["VersionId"]
    refused_uri = (
        f"s3://{settings.s3_bucket_import_staging}/{sha}"
        f"?versionId={quote(refused_version, safe='')}"
    )
    source_path.write_bytes(false_bytes)
    async with get_sessionmaker()() as session:
        await session.execute(
            sa.text("UPDATE import_file SET staged_blob_uri = :uri WHERE id = :file"),
            {"uri": refused_uri, "file": bad_id},
        )
        await session.commit()

    original_delete = commit_svc.storage.delete_staged_version
    delete_observed_committed_audit = False

    async def observe_then_delete(locator: object) -> None:
        nonlocal delete_observed_committed_audit
        assert locator.version_id == refused_version  # type: ignore[union-attr]
        async with get_sessionmaker()() as observer:
            events = (
                (
                    await observer.execute(
                        select(AuditEvent).where(
                            AuditEvent.object_id == run_uuid,
                            AuditEvent.event_type == EventType.IMPORT_ITEM_FAILED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            delete_observed_committed_audit = any(
                (event.after or {}).get("file_id") == str(bad_id) for event in events
            )
        await original_delete(locator)  # type: ignore[arg-type]

    monkeypatch.setattr(commit_svc.storage, "delete_staged_version", observe_then_delete)
    started = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    assert started.status_code == 202, started.text
    await _drive_commit(run_uuid)

    partial = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)).json()
    assert partial["status"] == "PartiallyCommitted"
    assert partial["counts"]["commit"] == {"committed": 1, "failed": 1}
    bad_partial = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{bad['id']}", headers=headers)
    ).json()
    peer_partial = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{peer['id']}", headers=headers)
    ).json()
    assert bad_partial["commit"]["result"] == "failed"
    assert bad_partial["commit"]["error"] == "upload_identity_digest_mismatch"
    assert bad_partial["commit"]["vault_document_id"] is None
    assert bad_partial["commit"]["vault_version_id"] is None
    assert peer_partial["commit"]["result"] == "success"
    assert delete_observed_committed_audit is True
    with pytest.raises(ClientError) as deleted:
        client.get_object(
            Bucket=settings.s3_bucket_import_staging,
            Key=sha,
            VersionId=refused_version,
        )
    assert deleted.value.response["Error"]["Code"] in {"NoSuchKey", "NoSuchVersion"}
    newer = client.get_object(
        Bucket=settings.s3_bucket_import_staging,
        Key=sha,
        VersionId=newer_version,
    )
    assert newer["Body"].read() == honest_bytes

    async with get_sessionmaker()() as session:
        failure_events = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == run_uuid,
                        AuditEvent.event_type == EventType.IMPORT_ITEM_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
        item_failure_events = [
            event for event in failure_events if (event.after or {}).get("file_id") == str(bad_id)
        ]
        assert len(item_failure_events) == 1
        failure_event = item_failure_events[0]
        assert failure_event.actor_type is ActorType.system and failure_event.actor_id is None
        assert failure_event.after == {
            "file_id": str(bad_id),
            "error": "upload_identity_digest_mismatch",
            "operation": "import_commit",
            "classification": "digest_mismatch",
            "source": {
                "bucket": "import-staging",
                "object_key": sha,
                "version_id": refused_version,
                "etag": failure_event.after["source"]["etag"],
            },
            "expected": {"sha256": sha, "size_bytes": len(honest_bytes)},
            "observed": {
                "sha256": hashlib.sha256(false_bytes).hexdigest(),
                "size_bytes": len(false_bytes),
            },
            "cleanup": {"policy": "delete_exact_version_after_audit"},
        }
        generic_integrity = (
            await session.execute(
                select(sa.func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == EventType.BLOB_INTEGRITY_FAILED,
                    AuditEvent.scope_ref == str(run_uuid),
                )
            )
        ).scalar_one()
        assert generic_integrity == 0
        assert await session.get(Blob, sha) is None
        assert (
            await session.execute(
                select(sa.func.count())
                .select_from(DocumentedInformation)
                .where(DocumentedInformation.identifier == identifier)
            )
        ).scalar_one() == 0
        peer_success_events = (
            await session.execute(
                select(sa.func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == EventType.IMPORT_ITEM_COMMITTED,
                    AuditEvent.object_id == uuid.UUID(peer_partial["commit"]["vault_document_id"]),
                )
            )
        ).scalar_one()
        assert peer_success_events == 1

    assert bad_partial["classification"] == before_bad["classification"]
    assert bad_partial["review"] == before_bad["review"]
    source_path.write_bytes(honest_bytes)
    resumed = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    assert resumed.status_code == 202, resumed.text
    await _drive_commit(run_uuid)

    complete = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)).json()
    assert complete["status"] == "Completed"
    assert complete["counts"]["commit"] == {"committed": 2, "failed": 0}
    bad_complete = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{bad['id']}", headers=headers)
    ).json()
    peer_complete = (
        await app_client.get(f"/api/v1/admin/imports/{run_id}/files/{peer['id']}", headers=headers)
    ).json()
    assert bad_complete["commit"]["result"] == "success"
    assert bad_complete["staged_blob_uri"] != refused_uri
    assert parse_qs(urlsplit(bad_complete["staged_blob_uri"]).query)["versionId"] == [newer_version]
    assert bad_complete["classification"] == before_bad["classification"]
    assert bad_complete["review"] == before_bad["review"]
    assert peer_complete["commit"] == peer_partial["commit"]
    async with get_sessionmaker()() as session:
        assert (
            await session.execute(
                select(sa.func.count())
                .select_from(DocumentedInformation)
                .where(DocumentedInformation.identifier == identifier)
            )
        ).scalar_one() == 1
        assert (
            await session.execute(
                select(sa.func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == EventType.IMPORT_ITEM_COMMITTED,
                    AuditEvent.object_id == uuid.UUID(peer_partial["commit"]["vault_document_id"]),
                )
            )
        ).scalar_one() == 1
        assert (
            await session.execute(
                select(sa.func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == EventType.IMPORT_ITEM_FAILED,
                    AuditEvent.object_id == run_uuid,
                )
            )
        ).scalar_one() == 1


async def test_restage_storage_outage_preserves_restage_intent_for_next_resume(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient stage-stream outage must not make resume reuse a deleted refused locator."""
    admin = _subject("restage-storage-outage")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        headers,
        _stub_tika,
        content_suffix=f" restage-storage-{uuid.uuid4().hex}",
    )
    run_uuid = uuid.UUID(run_id)
    document = by_name["SOP-PUR-002 Purchasing.docx"]
    peer = by_name["Internal Audit Report Q2 2023.pdf"]
    file_id = uuid.UUID(document["id"])
    await _confirm_for_commit(
        app_client,
        headers,
        run_id,
        document["id"],
        peer["id"],
        doc_identifier=f"SOP-{uuid.uuid4().hex[:6].upper()}-001",
    )
    deleted_source = await _seed_deleted_restage_source(run_uuid, file_id)
    original_stage = commit_svc.ingestion_storage.stage_stream
    stage_calls = 0

    async def _fail_restage(_handle: object, *, content_type: str) -> object:
        nonlocal stage_calls
        stage_calls += 1
        assert content_type == document["mime_type"]
        raise StorageUnavailable(StorageStage.STAGING_PUT)

    monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", _fail_restage)
    started = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    assert started.status_code == 202, started.text
    await _drive_commit(run_uuid)

    partial = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
        )
    ).json()
    assert partial["commit"]["error"] == "restage_source_unavailable"
    assert partial["staged_blob_uri"] == ingestion_storage.format_staged_uri(deleted_source)
    assert stage_calls == 1
    async with get_sessionmaker()() as session:
        failure_events = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == run_uuid,
                        AuditEvent.event_type == EventType.IMPORT_ITEM_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
        item_events = [
            event for event in failure_events if (event.after or {}).get("file_id") == str(file_id)
        ]
        assert [event.after for event in item_events] == [
            {"file_id": str(file_id), "error": "restage_source_unavailable"}
        ]

    async def _observe_restage(handle: object, *, content_type: str) -> object:
        nonlocal stage_calls
        stage_calls += 1
        return await original_stage(handle, content_type=content_type)  # type: ignore[arg-type]

    monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", _observe_restage)
    resumed = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    assert resumed.status_code == 202, resumed.text
    await _drive_commit(run_uuid)

    complete = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)).json()
    detail = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
        )
    ).json()
    assert complete["status"] == "Completed"
    assert detail["commit"]["result"] == "success"
    assert detail["staged_blob_uri"] != ingestion_storage.format_staged_uri(deleted_source)
    assert stage_calls == 2


async def test_restage_identity_refusal_audits_cleans_and_restages_again(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal raised by stage_stream keeps its typed audit/cleanup ownership across resume."""
    admin = _subject("restage-identity-refusal")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        headers,
        _stub_tika,
        content_suffix=f" restage-refusal-{uuid.uuid4().hex}",
    )
    run_uuid = uuid.UUID(run_id)
    document = by_name["SOP-PUR-002 Purchasing.docx"]
    peer = by_name["Internal Audit Report Q2 2023.pdf"]
    file_id = uuid.UUID(document["id"])
    await _confirm_for_commit(
        app_client,
        headers,
        run_id,
        document["id"],
        peer["id"],
        doc_identifier=f"SOP-{uuid.uuid4().hex[:6].upper()}-001",
    )
    deleted_source = await _seed_deleted_restage_source(run_uuid, file_id)
    original_stage = commit_svc.ingestion_storage.stage_stream
    original_delete = commit_svc.storage.delete_staged_version
    refused_source: StagedObjectRef | None = None
    cleanup_versions: list[str] = []

    async def _refuse_restage(handle: object, *, content_type: str) -> object:
        nonlocal refused_source
        staged = await original_stage(handle, content_type=content_type)  # type: ignore[arg-type]
        refused_source = staged.source
        raise UploadIdentityMismatch(
            source=staged.source,
            expected_sha256=staged.source.expected_sha256,
            observed_sha256="f" * 64,
            expected_size=staged.source.expected_size,
            observed_size=staged.source.expected_size or 0,
            etag="restage-refusal-etag",
            classification="digest_mismatch",
        )

    async def _observe_cleanup(locator: StagedVersionLocator) -> None:
        assert refused_source is not None
        assert locator == refused_source.locator
        async with get_sessionmaker()() as observer:
            events = (
                (
                    await observer.execute(
                        select(AuditEvent).where(
                            AuditEvent.object_id == run_uuid,
                            AuditEvent.event_type == EventType.IMPORT_ITEM_FAILED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert any(
                (event.after or {}).get("source", {}).get("version_id") == locator.version_id
                for event in events
            )
        cleanup_versions.append(locator.version_id)
        await original_delete(locator)

    monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", _refuse_restage)
    monkeypatch.setattr(commit_svc.storage, "delete_staged_version", _observe_cleanup)
    started = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    assert started.status_code == 202, started.text
    await _drive_commit(run_uuid)

    assert refused_source is not None
    partial = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
        )
    ).json()
    assert partial["commit"]["error"] == "upload_identity_digest_mismatch"
    assert partial["staged_blob_uri"] == ingestion_storage.format_staged_uri(deleted_source)
    assert cleanup_versions == [refused_source.locator.version_id]
    async with get_sessionmaker()() as session:
        failure_events = (
            (
                await session.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == run_uuid,
                        AuditEvent.event_type == EventType.IMPORT_ITEM_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
        item_events = [
            event for event in failure_events if (event.after or {}).get("file_id") == str(file_id)
        ]
        assert len(item_events) == 1
        assert item_events[0].after["classification"] == "digest_mismatch"
        assert item_events[0].after["source"]["version_id"] == refused_source.locator.version_id
        assert item_events[0].after["cleanup"] == {"policy": "delete_exact_version_after_audit"}

    restage_calls = 0

    async def _observe_restage(handle: object, *, content_type: str) -> object:
        nonlocal restage_calls
        restage_calls += 1
        return await original_stage(handle, content_type=content_type)  # type: ignore[arg-type]

    monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", _observe_restage)
    resumed = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    assert resumed.status_code == 202, resumed.text
    await _drive_commit(run_uuid)

    complete = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)).json()
    detail = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
        )
    ).json()
    assert complete["status"] == "Completed"
    assert detail["commit"]["result"] == "success"
    assert detail["staged_blob_uri"] != ingestion_storage.format_staged_uri(deleted_source)
    assert restage_calls == 1


async def test_upload_identity_legacy_locator_requires_restart_and_never_restages(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _subject("avery-import-legacy")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        headers,
        _stub_tika,
        content_suffix=f" legacy-{uuid.uuid4().hex}",
    )
    run_uuid = uuid.UUID(run_id)
    legacy = by_name["SOP-PUR-002 Purchasing.docx"]
    peer = by_name["Internal Audit Report Q2 2023.pdf"]
    identifier = f"SOP-{uuid.uuid4().hex[:6].upper()}-001"
    await _confirm_for_commit(
        app_client,
        headers,
        run_id,
        legacy["id"],
        peer["id"],
        doc_identifier=identifier,
    )
    before = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{legacy['id']}", headers=headers
        )
    ).json()
    legacy_uri = f"s3://{get_settings().s3_bucket_import_staging}/{legacy['sha256']}"
    async with get_sessionmaker()() as session:
        await session.execute(
            sa.text("UPDATE import_file SET staged_blob_uri = :uri WHERE id = :file"),
            {"uri": legacy_uri, "file": uuid.UUID(legacy["id"])},
        )
        await session.commit()

    async def _forbid_restage(_handle: object, *, content_type: str) -> object:
        raise AssertionError(f"legacy/no-version row must not restage ({content_type})")

    monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", _forbid_restage)

    assert (
        await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    ).status_code == 202
    await _drive_commit(run_uuid)
    first = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{legacy['id']}", headers=headers
        )
    ).json()
    assert first["commit"]["error"] == "staging_version_required"
    assert first["staged_blob_uri"] == legacy_uri
    assert first["classification"] == before["classification"]
    assert first["review"] == before["review"]

    assert (
        await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    ).status_code == 202
    await _drive_commit(run_uuid)
    second = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{legacy['id']}", headers=headers
        )
    ).json()
    assert second["commit"]["error"] == "staging_version_required"
    assert second["staged_blob_uri"] == legacy_uri
    assert second["classification"] == before["classification"]
    assert second["review"] == before["review"]


async def test_upload_identity_legacy_locator_deduplicates_correct_domain_worm_blob(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
) -> None:
    admin = _subject("avery-import-legacy-dedup")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        headers,
        _stub_tika,
        content_suffix=f" legacy-dedup-{uuid.uuid4().hex}",
    )
    document = by_name["SOP-PUR-002 Purchasing.docx"]
    peer = by_name["Internal Audit Report Q2 2023.pdf"]
    sha = document["sha256"]
    assert isinstance(sha, str)
    identifier = f"SOP-{uuid.uuid4().hex[:6].upper()}-001"
    await _confirm_for_commit(
        app_client,
        headers,
        run_id,
        document["id"],
        peer["id"],
        doc_identifier=identifier,
    )
    settings = get_settings()
    source = ingestion_storage.parse_staged_uri(
        document["staged_blob_uri"],
        expected_sha256=sha,
        content_type=document["mime_type"],
        expected_size=document["size_bytes"],
    )
    promoted = await commit_svc.storage.promote_worm(
        source, target_bucket=settings.s3_bucket_documents
    )
    async with get_sessionmaker()() as session:
        actor = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == admin))
        ).scalar_one()
        session.add(
            Blob(
                sha256=sha,
                org_id=actor.org_id,
                size_bytes=promoted.size,
                mime_type=promoted.content_type or document["mime_type"],
                bucket=promoted.target_bucket,
                object_key=promoted.target_key,
                worm_locked=True,
                worm_retain_until=promoted.retain_until,
            )
        )
        await session.execute(
            sa.text("UPDATE import_file SET staged_blob_uri = :uri WHERE id = :file"),
            {
                "uri": f"s3://{settings.s3_bucket_import_staging}/{sha}",
                "file": uuid.UUID(document["id"]),
            },
        )
        await session.commit()

    assert (
        await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
    ).status_code == 202
    await _drive_commit(uuid.UUID(run_id))
    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)).json()
    detail = (
        await app_client.get(
            f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
        )
    ).json()
    assert run["status"] == "Completed"
    assert detail["commit"]["result"] == "success"
    assert detail["staged_blob_uri"] == f"s3://{settings.s3_bucket_import_staging}/{sha}"


async def test_upload_identity_legacy_locator_keeps_foreign_domain_blob_failure(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
) -> None:
    admin = _subject("avery-import-legacy-foreign")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        headers,
        _stub_tika,
        content_suffix=f" legacy-foreign-{uuid.uuid4().hex}",
    )
    document = by_name["SOP-PUR-002 Purchasing.docx"]
    peer = by_name["Internal Audit Report Q2 2023.pdf"]
    sha = document["sha256"]
    assert isinstance(sha, str)
    await _confirm_for_commit(
        app_client,
        headers,
        run_id,
        document["id"],
        peer["id"],
        doc_identifier=f"SOP-{uuid.uuid4().hex[:6].upper()}-001",
    )
    settings = get_settings()
    legacy_uri = f"s3://{settings.s3_bucket_import_staging}/{sha}"
    async with get_sessionmaker()() as session:
        actor = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == admin))
        ).scalar_one()
        session.add(
            Blob(
                sha256=sha,
                org_id=actor.org_id,
                size_bytes=document["size_bytes"],
                mime_type=document["mime_type"],
                bucket=settings.s3_bucket_records,
                object_key=sha,
                worm_locked=True,
            )
        )
        await session.execute(
            sa.text("UPDATE import_file SET staged_blob_uri = :uri WHERE id = :file"),
            {"uri": legacy_uri, "file": uuid.UUID(document["id"])},
        )
        await session.commit()

    try:
        assert (
            await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
        ).status_code == 202
        await _drive_commit(uuid.UUID(run_id))
        run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)).json()
        detail = (
            await app_client.get(
                f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
            )
        ).json()
        assert run["status"] == "PartiallyCommitted"
        assert detail["commit"]["error"] == "source_bytes_in_foreign_bucket"
        assert detail["staged_blob_uri"] == legacy_uri
    finally:
        async with get_sessionmaker()() as session:
            await session.execute(sa.delete(Blob).where(Blob.sha256 == sha))
            await session.commit()


async def test_commit_concurrent_run_commit_is_single_flight(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    tag = uuid.uuid4().hex[:6].upper()
    doc_ident = f"SOP-{tag}-001"
    await _confirm_for_commit(app_client, h, run_id, sop, audit, doc_identifier=doc_ident)
    assert (
        await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    ).status_code == 202

    # Two concurrent commit workers on one Committing run — the per-item ledger CLAIM
    # (UNIQUE(run,file)) makes it exactly-once (one wins the claim, the other rolls its item back).
    async def _one() -> None:
        await run_commit(get_sessionmaker(), uuid.UUID(run_id))

    await asyncio.gather(_one(), _one())

    run = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert run["status"] == "Completed"
    assert run["counts"]["commit"] == {"committed": 2, "failed": 0}
    async with get_sessionmaker()() as s:
        n = (
            await s.execute(
                select(sa.func.count())
                .select_from(DocumentedInformation)
                .where(DocumentedInformation.identifier == doc_ident)
            )
        ).scalar_one()
        assert n == 1  # exactly one document despite two concurrent workers


async def test_commit_blocked_by_conflict_and_gated_on_import_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], _stub_tika: None
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, h, _stub_tika)
    sop = by_name["SOP-PUR-002 Purchasing.docx"]["id"]
    audit = by_name["Internal Audit Report Q2 2023.pdf"]["id"]
    tag = uuid.uuid4().hex[:6].upper()
    clash = f"SOP-{tag}-001"
    # two DOCUMENT keep-items corrected to the SAME identifier → a blocking duplicate-within-import.
    for fid in (sop, audit):
        await app_client.post(
            f"/api/v1/admin/imports/{run_id}/files/{fid}/decision",
            headers=h,
            json={"action": "correct", "after": {"kind": "DOCUMENT", "identifier": clash}},
        )
    chk = (await app_client.get(f"/api/v1/admin/imports/{run_id}/checklist", headers=h)).json()
    assert chk["ready"] is False
    assert any(b["type"] == "duplicate_identifier_within_import" for b in chk["blocking"])

    blocked = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=h)
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "commit_blocked"

    # SoD: a reviewer-only principal (import.review, NOT import.commit) is 403 at the commit gate.
    reviewer = _subject("mara")
    await _grant(reviewer, ("import.review",))
    hr = _auth(token_factory, reviewer)
    denied = await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=hr)
    assert denied.status_code == 403


async def test_concurrent_failure_writers_share_one_audit_and_cleanup_authorization(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the observed-generation CAS lets both stale failure writers audit and clean."""
    admin = _subject("failure-generation-race")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(app_client, headers, _stub_tika)
    run_uuid = uuid.UUID(run_id)
    file_id = uuid.UUID(by_name["SOP-PUR-002 Purchasing.docx"]["id"])

    async with get_sessionmaker()() as setup:
        run = await ingestion_repo.get_run(setup, run_uuid)
        file = await setup.get(ImportFile, file_id)
        assert run is not None and file is not None
        assert file.sha256 is not None and file.staged_blob_uri is not None
        source = ingestion_storage.parse_staged_uri(
            file.staged_blob_uri,
            expected_sha256=file.sha256,
            content_type=file.mime_type or "application/octet-stream",
            expected_size=file.size_bytes,
        )
        org_id = run.org_id
        context = commit_svc._import_rejection_context(org_id, file)

    failure = UploadIdentityMismatch(
        source=source,
        expected_sha256=source.expected_sha256,
        observed_sha256="f" * 64,
        expected_size=source.expected_size,
        observed_size=source.expected_size or 0,
        etag="controlled-overlap",
        classification="digest_mismatch",
    )
    arrived = 0
    arrived_lock = asyncio.Lock()
    both_observed = asyncio.Event()
    cleanup_calls: list[str] = []

    async def _delete_once(locator: object) -> None:
        cleanup_calls.append(locator.version_id)  # type: ignore[union-attr]

    monkeypatch.setattr(commit_svc.storage, "delete_staged_version", _delete_once)

    async def _worker() -> bool:
        nonlocal arrived
        async with get_sessionmaker()() as session:
            observed = await ingestion_repo.get_commit_result(session, run_uuid, file_id)
            assert observed is None
            async with arrived_lock:
                arrived += 1
                if arrived == 2:
                    both_observed.set()
            await both_observed.wait()
            recorded = await commit_svc._record_failed(
                session,
                run_uuid,
                org_id,
                file_id,
                "upload_identity_digest_mismatch",
                expected_committed_at=None,
                rejection=(failure, context),
            )
        if recorded.won and recorded.audit_ref is not None:
            await commit_svc._cleanup_rejected_import_source(failure, recorded.audit_ref)
        return recorded.won

    winners = await asyncio.gather(_worker(), _worker())

    assert sorted(winners) == [False, True]
    assert cleanup_calls == [source.locator.version_id]
    async with get_sessionmaker()() as check:
        result = await ingestion_repo.get_commit_result(check, run_uuid, file_id)
        assert result is not None
        assert result.result is ImportCommitResultStatus.FAILED
        events = (
            (
                await check.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == run_uuid,
                        AuditEvent.event_type == EventType.IMPORT_ITEM_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
        item_events = [e for e in events if (e.after or {}).get("file_id") == str(file_id)]
        assert len(item_events) == 1


async def test_restaged_locator_survives_retained_storage_failure_and_resumes_exactly(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    _stub_tika: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the retained-locator write strands a non-restageable failed resume on v1."""
    import boto3

    admin = _subject("retained-restage-storage-failure")
    await _assign_role(admin, "System Administrator")
    headers = _auth(token_factory, admin)
    run_id, by_name = await _proposed_classifiable(
        app_client,
        headers,
        _stub_tika,
        content_suffix=f" retained-restage-{uuid.uuid4().hex}",
    )
    run_uuid = uuid.UUID(run_id)
    document = by_name["SOP-PUR-002 Purchasing.docx"]
    peer = by_name["Internal Audit Report Q2 2023.pdf"]
    file_id = uuid.UUID(document["id"])
    sha = document["sha256"]
    assert isinstance(sha, str)
    await _confirm_for_commit(
        app_client,
        headers,
        run_id,
        document["id"],
        peer["id"],
        doc_identifier=f"SOP-{uuid.uuid4().hex[:6].upper()}-001",
    )

    settings = get_settings()
    source_path = Path(settings.import_source_root) / document["rel_path"]
    honest_bytes = source_path.read_bytes()
    false_bytes = b"!" * len(honest_bytes)
    assert false_bytes != honest_bytes
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    refused = client.put_object(
        Bucket=settings.s3_bucket_import_staging,
        Key=sha,
        Body=false_bytes,
        ContentType=document["mime_type"],
    )
    refused_uri = (
        f"s3://{settings.s3_bucket_import_staging}/{sha}"
        f"?versionId={quote(refused['VersionId'], safe='')}"
    )
    source_path.write_bytes(false_bytes)
    try:
        async with get_sessionmaker()() as session:
            await session.execute(
                sa.text("UPDATE import_file SET staged_blob_uri = :uri WHERE id = :file"),
                {"uri": refused_uri, "file": file_id},
            )
            await session.commit()

        assert (
            await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
        ).status_code == 202
        await _drive_commit(run_uuid)
        refused_detail = (
            await app_client.get(
                f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
            )
        ).json()
        assert refused_detail["commit"]["error"] == "upload_identity_digest_mismatch"

        source_path.write_bytes(honest_bytes)
        original_stage = commit_svc.ingestion_storage.stage_stream
        original_promote = commit_svc.storage.promote_worm
        restaged_uri: str | None = None

        async def _capture_restage(handle: object, *, content_type: str) -> object:
            nonlocal restaged_uri
            staged = await original_stage(handle, content_type=content_type)  # type: ignore[arg-type]
            restaged_uri = staged.staged_blob_uri
            return staged

        async def _fail_after_restage(source: object, *, target_bucket: str) -> object:
            if source.locator.object_key == sha:  # type: ignore[union-attr]
                raise StorageUnavailable(StorageStage.COPY)
            return await original_promote(source, target_bucket=target_bucket)  # type: ignore[arg-type]

        monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", _capture_restage)
        monkeypatch.setattr(commit_svc.storage, "promote_worm", _fail_after_restage)
        assert (
            await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
        ).status_code == 202
        await _drive_commit(run_uuid)

        retained = (
            await app_client.get(
                f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
            )
        ).json()
        assert restaged_uri is not None and restaged_uri != refused_uri
        assert retained["commit"]["error"] == "storage_unavailable_copy"
        assert retained["staged_blob_uri"] == restaged_uri
        retained_source = ingestion_storage.parse_staged_uri(
            restaged_uri,
            expected_sha256=sha,
            content_type=document["mime_type"],
            expected_size=document["size_bytes"],
        )
        retained_object = client.get_object(
            Bucket=settings.s3_bucket_import_staging,
            Key=sha,
            VersionId=retained_source.locator.version_id,
        )
        assert retained_object["Body"].read() == honest_bytes

        async def _forbid_restage(_handle: object, *, content_type: str) -> object:
            raise AssertionError(f"unexpected restage with {content_type}")

        monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", _forbid_restage)
        monkeypatch.setattr(commit_svc.storage, "promote_worm", original_promote)
        assert (
            await app_client.post(f"/api/v1/admin/imports/{run_id}/commit", headers=headers)
        ).status_code == 202
        await _drive_commit(run_uuid)
        completed = (
            await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=headers)
        ).json()
        final_detail = (
            await app_client.get(
                f"/api/v1/admin/imports/{run_id}/files/{document['id']}", headers=headers
            )
        ).json()
        assert completed["status"] == "Completed"
        assert final_detail["commit"]["result"] == "success"
        assert final_detail["staged_blob_uri"] == restaged_uri
    finally:
        source_path.write_bytes(honest_bytes)
