"""Generated-object producers preserve the exact staged-version identity.

Pre-migration caller inventory captured at base 1222c6a01f699cacde42fea86e013b4996564429:

* ``vault/service.py`` had six JSON ``put_staging_bytes`` / ``finalize_worm`` pairs: form schema,
  objective commitment, risk register, context register, interested-party register, and management
  review minutes.
* ``packs/build.py`` wrote the generated ZIP with ``put_bytes(bucket=_staging_bucket())`` and passed
  tuple evidence to Records.
* ``ingestion/commit.py`` staged the generated Markdown report but discarded the returned source,
  then passed tuple evidence to Records.
* ``records/service.py`` retained tuple normalization, ``_evidence_source_bucket``, and a
  ``finalize_worm`` compatibility path; ``vault/storage.py`` retained ``finalize_worm`` and
  ``_legacy_finalize_sync``.
"""

from __future__ import annotations

import ast
import datetime
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from easysynq_api.db.models._audit_enums import ActorType, EventType
from easysynq_api.db.models._ingestion_enums import ImportRunStatus
from easysynq_api.problems import ProblemException
from easysynq_api.services.ingestion import commit as commit_service
from easysynq_api.services.records.service import EvidenceInput
from easysynq_api.services.vault import service, storage
from easysynq_api.services.vault.staged_identity import (
    PromotionOutcome,
    PromotionResult,
    StagedObjectRef,
    StagedVersionLocator,
    StagingDomain,
    UploadIdentityMismatch,
)
from easysynq_api.tasks import packs as pack_tasks

pytestmark = pytest.mark.unit

_API_ROOT = Path(__file__).resolve().parents[2]
_PRODUCTION_ROOT = _API_ROOT / "src" / "easysynq_api"


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _contains_call_named(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Call) and _call_name(child) == name for child in ast.walk(node)
    )


def _legacy_source_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(_API_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
                "finalize_worm",
                "_legacy_finalize_sync",
            }:
                violations.append(f"{rel}:{node.lineno}:definition:{node.name}")
            elif isinstance(node, ast.Name) and node.id in {
                "_legacy_finalize_sync",
                "_evidence_source_bucket",
            }:
                violations.append(f"{rel}:{node.lineno}:reference:{node.id}")
            elif isinstance(node, ast.Attribute) and node.attr == "_legacy_finalize_sync":
                violations.append(f"{rel}:{node.lineno}:reference:{node.attr}")
            elif isinstance(node, ast.arg) and node.arg == "_evidence_source_bucket":
                violations.append(f"{rel}:{node.lineno}:argument:{node.arg}")
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name == "finalize_worm":
                violations.append(f"{rel}:{node.lineno}:call:{name}")
            for keyword in node.keywords:
                if keyword.arg == "_evidence_source_bucket":
                    violations.append(f"{rel}:{node.lineno}:keyword:{keyword.arg}")
                if (
                    name == "put_bytes"
                    and keyword.arg == "bucket"
                    and _contains_call_named(keyword.value, "_staging_bucket")
                ):
                    violations.append(f"{rel}:{node.lineno}:call:put_bytes-to-staging")
    return sorted(set(violations))


def test_generated_producers_have_no_key_latest_escape_hatch() -> None:
    assert _legacy_source_violations() == []


def test_tests_have_no_direct_tuple_evidence_callers() -> None:
    violations: list[str] = []
    for path in sorted((_API_ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "evidence" or not isinstance(
                    keyword.value, (ast.List, ast.Tuple)
                ):
                    continue
                if any(isinstance(item, ast.Tuple) for item in keyword.value.elts):
                    violations.append(f"{path.relative_to(_API_ROOT)}:{node.lineno}")
    assert violations == []


def _source(payload: bytes, *, version_id: str = "generated-v1") -> StagedObjectRef:
    sha = hashlib.sha256(payload).hexdigest()
    return StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=sha,
            version_id=version_id,
        ),
        expected_sha256=sha,
        content_type="application/json",
        expected_size=len(payload),
    )


class _OwnerSession:
    def __init__(self, calls: list[str], blob: Any | None = None) -> None:
        self.calls = calls
        self.blob = blob

    async def rollback(self) -> None:
        self.calls.append("owner.rollback")

    async def execute(self, _statement: Any) -> None:
        self.calls.append("owner.insert")

    async def flush(self) -> None:
        self.calls.append("owner.flush")


class _AuditSession:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.added: list[Any] = []

    async def __aenter__(self) -> _AuditSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def add(self, row: Any) -> None:
        self.calls.append("audit.add")
        self.added.append(row)

    async def flush(self) -> None:
        self.added[-1].id = 97

    async def commit(self) -> None:
        self.calls.append("audit.commit")


class _AuditSessionmaker:
    def __init__(self, session: _AuditSession) -> None:
        self.session = session

    def __call__(self) -> _AuditSession:
        return self.session


@pytest.mark.asyncio
async def test_generated_helper_passes_exact_staging_object_identity_to_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b'{"generated":true}'
    source = _source(payload)
    promoted_sources: list[StagedObjectRef] = []
    blob = SimpleNamespace(worm_locked=True, bucket="documents")
    session = _OwnerSession([])
    reads = iter([None, blob])

    async def get_blob(_session: Any, _sha: str) -> Any | None:
        return next(reads)

    async def put_staging_bytes(data: bytes, sha256: str, *, content_type: str) -> StagedObjectRef:
        assert data is payload
        assert sha256 == source.expected_sha256
        assert content_type == "application/json"
        return source

    async def promote_for_owner(
        _session: Any,
        candidate: StagedObjectRef,
        *,
        target_bucket: str,
        context: Any,
        rejection_sessionmaker: Any = None,
    ) -> PromotionResult:
        promoted_sources.append(candidate)
        assert target_bucket == "documents"
        assert context.operation == "server_generated"
        assert context.actor_type is ActorType.system
        assert context.user_correctable is False
        assert rejection_sessionmaker is None
        return PromotionResult(
            outcome=PromotionOutcome.COPIED,
            verified_sha256=source.expected_sha256,
            size=len(payload),
            content_type="application/json",
            retain_until=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1),
            source=source,
            source_etag='"etag"',
            target_bucket="documents",
            target_key=source.expected_sha256,
            target_version_id="worm-v1",
        )

    monkeypatch.setattr(service.repository, "get_blob", get_blob)
    monkeypatch.setattr(service.storage, "put_staging_bytes", put_staging_bytes)
    monkeypatch.setattr(service, "promote_for_owner", promote_for_owner)
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(s3_bucket_documents="documents")
    )

    result = await service._ensure_generated_documents_blob(
        session,  # type: ignore[arg-type]
        SimpleNamespace(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            org_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        ),
        payload=payload,
        content_type="application/json",
        scope_ref="FRM-GEN-0001",
    )

    assert result is blob
    assert promoted_sources == [source]
    assert promoted_sources[0] is source


@pytest.mark.asyncio
async def test_generated_helper_maps_mismatch_to_503_after_audit_commit_and_exact_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b'{"generated":true}'
    source = _source(payload)
    calls: list[str] = []
    owner_session = _OwnerSession(calls)
    audit_session = _AuditSession(calls)

    async def no_blob(_session: Any, _sha: str) -> None:
        return None

    async def put_staging_bytes(
        _data: bytes, _sha256: str, *, content_type: str
    ) -> StagedObjectRef:
        assert content_type == "application/json"
        return source

    async def mismatch(candidate: StagedObjectRef, *, target_bucket: str) -> Any:
        assert candidate is source
        assert target_bucket == "documents"
        raise UploadIdentityMismatch(
            source=source,
            expected_sha256=source.expected_sha256,
            observed_sha256="f" * 64,
            expected_size=len(payload),
            observed_size=len(payload),
            etag='"etag"',
            classification="digest_mismatch",
        )

    async def delete_exact(locator: StagedVersionLocator) -> None:
        assert locator is source.locator
        calls.append("delete.exact")

    monkeypatch.setattr(service.repository, "get_blob", no_blob)
    monkeypatch.setattr(service.storage, "put_staging_bytes", put_staging_bytes)
    monkeypatch.setattr(storage, "promote_worm", mismatch)
    monkeypatch.setattr(storage, "delete_staged_version", delete_exact)
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(s3_bucket_documents="documents")
    )

    with pytest.raises(ProblemException) as caught:
        await service._ensure_generated_documents_blob(
            owner_session,  # type: ignore[arg-type]
            SimpleNamespace(
                id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
                org_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            ),
            payload=payload,
            content_type="application/json",
            scope_ref="FRM-GEN-0001",
            rejection_sessionmaker=_AuditSessionmaker(audit_session),  # type: ignore[arg-type]
        )

    assert (caught.value.status, caught.value.code) == (503, "storage_unavailable")
    assert calls == ["owner.rollback", "audit.add", "audit.commit", "delete.exact"]
    assert len(audit_session.added) == 1
    audit = audit_session.added[0]
    assert audit.event_type is EventType.BLOB_INTEGRITY_FAILED
    assert audit.actor_type is ActorType.system
    assert audit.actor_id is None


@pytest.mark.asyncio
async def test_import_report_passes_exact_staging_object_identity_to_record_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    org_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    policy_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    record_id = uuid.UUID("55555555-5555-5555-5555-555555555555")
    captured_evidence: list[EvidenceInput] = []
    returned_source: StagedObjectRef | None = None

    async def no_rows(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def no_checklist(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def no_deferred(*_args: Any, **_kwargs: Any) -> list[str]:
        return []

    async def put_report(data: bytes, sha256: str, *, content_type: str) -> StagedObjectRef:
        nonlocal returned_source
        assert sha256 == hashlib.sha256(data).hexdigest()
        assert content_type == "text/markdown"
        returned_source = StagedObjectRef(
            locator=StagedVersionLocator(
                domain=StagingDomain.STAGING,
                object_key=sha256,
                version_id="report-v1",
            ),
            expected_sha256=sha256,
            content_type=content_type,
            expected_size=len(data),
        )
        return returned_source

    async def capture_report(*_args: Any, **kwargs: Any) -> Any:
        captured_evidence.extend(kwargs["evidence"])
        assert kwargs.get("rejection_context") is None
        return SimpleNamespace(id=record_id)

    monkeypatch.setattr(commit_service.repo, "included_files_with_context", no_rows)
    monkeypatch.setattr(commit_service.repo, "list_decisions", no_rows)
    monkeypatch.setattr(commit_service, "compute_checklist", no_checklist)
    monkeypatch.setattr(commit_service, "_deferred_chain_families", no_deferred)
    monkeypatch.setattr(commit_service, "render_import_report", lambda _data: "generated report")
    monkeypatch.setattr(commit_service.storage, "put_staging_bytes", put_report)

    async def ensure_policy(*_args: Any) -> Any:
        return SimpleNamespace(id=policy_id)

    monkeypatch.setattr(commit_service.records_repo, "ensure_default_policy", ensure_policy)
    monkeypatch.setattr(commit_service.records_svc, "capture_record", capture_report)

    result = await commit_service._capture_report(
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(
            id=run_id,
            org_id=org_id,
            classifier_version="classifier-v1",
            source_root="approved-source",
            created_by=uuid.uuid4(),
            committed_by=uuid.uuid4(),
            counts={},
        ),
        SimpleNamespace(id=uuid.uuid4(), org_id=org_id),
        [],
        0,
        0,
    )

    assert result == record_id
    assert len(captured_evidence) == 1
    assert captured_evidence[0].source is returned_source


class _NestedSavepoint:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def __aenter__(self) -> _NestedSavepoint:
        self.calls.append("savepoint.enter")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: Any,
    ) -> bool:
        if exc_type is not None:
            self.calls.append("savepoint.rollback")
        self.calls.append("savepoint.exit")
        return False


class _FinalizeSession:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def __aenter__(self) -> _FinalizeSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin_nested(self) -> _NestedSavepoint:
        return _NestedSavepoint(self.calls)

    async def commit(self) -> None:
        self.calls.append("outer.commit")

    async def rollback(self) -> None:
        self.calls.append("outer.rollback")


class _FinalizeSessionmaker:
    def __init__(self, session: _FinalizeSession) -> None:
        self.session = session

    def __call__(self) -> _FinalizeSession:
        return self.session


@pytest.mark.asyncio
async def test_import_report_rejects_only_after_savepoint_rollback_and_commits_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    payload = b"generated report"
    source = _source(payload, version_id="report-v1")
    failure = UploadIdentityMismatch(
        source=source,
        expected_sha256=source.expected_sha256,
        observed_sha256="f" * 64,
        expected_size=len(payload),
        observed_size=len(payload),
        etag='"etag"',
        classification="digest_mismatch",
    )
    run = SimpleNamespace(
        status=ImportRunStatus.COMMITTING,
        counts={},
        report_record_id=None,
        completed_at=None,
    )
    session = _FinalizeSession(calls)
    maker = _FinalizeSessionmaker(session)

    async def no_results(*_args: Any) -> list[Any]:
        return []

    async def get_run(*_args: Any, **_kwargs: Any) -> Any:
        return run

    async def refuse_report(*_args: Any, **_kwargs: Any) -> Any:
        raise failure

    async def reject_after_rollback(
        rejected: Any, *, context: Any, rejection_sessionmaker: Any
    ) -> None:
        assert rejected is failure
        assert context.operation == "server_generated"
        assert context.actor_type is ActorType.system
        assert rejection_sessionmaker is maker
        calls.append("reject")
        raise ProblemException(status=503, code="storage_unavailable", title="unavailable")

    class _MirrorSink:
        def enqueue(self, reason: str) -> None:
            assert reason == "import_commit"
            calls.append("mirror.enqueue")

    monkeypatch.setattr(commit_service.repo, "list_commit_results", no_results)
    monkeypatch.setattr(commit_service.repo, "get_run", get_run)
    monkeypatch.setattr(commit_service, "_capture_report", refuse_report)
    monkeypatch.setattr(
        commit_service.upload_rejection, "reject_after_owner_rollback", reject_after_rollback
    )
    monkeypatch.setattr(commit_service, "emit_import_event_system", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(commit_service, "get_mirror_enqueue_sink", lambda: _MirrorSink())

    await commit_service._finalize(
        maker,  # type: ignore[arg-type]
        uuid.UUID("33333333-3333-3333-3333-333333333333"),
        uuid.UUID("11111111-1111-1111-1111-111111111111"),
        SimpleNamespace(id=uuid.uuid4()),
        uuid.uuid4(),
    )

    assert run.status is ImportRunStatus.COMPLETED
    assert run.report_record_id is None
    assert calls == [
        "savepoint.enter",
        "savepoint.rollback",
        "savepoint.exit",
        "reject",
        "outer.commit",
        "mirror.enqueue",
    ]


@pytest.mark.asyncio
async def test_pack_task_threads_its_loop_local_sessionmaker_to_rejection_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Engine:
        async def dispose(self) -> None:
            calls.append("engine.dispose")

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Maker:
        def __call__(self) -> _Session:
            return _Session()

    engine = _Engine()
    maker = _Maker()

    async def build_pack(
        _session: Any,
        _pack_id: uuid.UUID,
        *,
        authz_sink: Any,
        rejection_sessionmaker: Any,
    ) -> None:
        assert authz_sink.session_factory is maker
        assert rejection_sessionmaker is maker
        calls.append("build")

    async def build_portfolio(_session: Any, _pack_id: uuid.UUID) -> None:
        calls.append("portfolio")

    monkeypatch.setattr(pack_tasks, "create_async_engine", lambda _url: engine)
    monkeypatch.setattr(pack_tasks, "async_sessionmaker", lambda *_args, **_kwargs: maker)
    monkeypatch.setattr(
        pack_tasks,
        "DbAuthzAuditSink",
        lambda session_factory: SimpleNamespace(session_factory=session_factory),
    )
    monkeypatch.setattr(pack_tasks, "build", build_pack)
    monkeypatch.setattr(pack_tasks, "build_and_cache_portfolio", build_portfolio)

    await pack_tasks._run_build("66666666-6666-6666-6666-666666666666")

    assert calls == ["build", "portfolio", "engine.dispose"]
