"""Unit proofs for the S-ing-5 commit slice — the pure/in-memory bits (the DB-bound per-item commit
flow is the integration suite). Covers: the identifier parse helper (area derivation), the
state-machine membership guards (the #1 reaper trap), the fold's identifier-collidable rule (the
sentinel false-collision fix), decided_by, the commit-result enum, and the Import Report.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from easysynq_api.db.models._ingestion_enums import (
    IMPORT_COMMIT_RESULT_STATUS_VALUES,
    ImportCommitResultStatus,
    ImportDecisionAction,
    ImportRunStatus,
)
from easysynq_api.db.models.import_decision import ImportDecision
from easysynq_api.db.models.import_proposal_node import ImportProposalNode
from easysynq_api.domain.ingestion.import_report import (
    CommittedItem,
    FailedItem,
    ImportReportData,
    render_import_report,
)
from easysynq_api.domain.vault.identifier import format_identifier, parse_identifier
from easysynq_api.services.ingestion import commit as commit_svc
from easysynq_api.services.ingestion import repository as repo
from easysynq_api.services.ingestion import service as svc
from easysynq_api.services.ingestion.commit import _decided_by
from easysynq_api.services.ingestion.review import fold_file_decisions
from easysynq_api.services.vault.staged_identity import (
    StagedObjectRef,
    StagedSourceChanged,
    StagedSourceUnavailable,
    StagedVersionLocator,
    StagingDomain,
    StagingVersionRequired,
    StorageStage,
    StorageUnavailable,
    TargetIdentityConflict,
    UploadIdentityMismatch,
    WormNotApplied,
)

pytestmark = pytest.mark.unit


def _source(
    *, sha256: str = "a" * 64, version_id: str = "version/one", size: int = 7
) -> StagedObjectRef:
    return StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.IMPORT_STAGING,
            object_key=sha256,
            version_id=version_id,
        ),
        expected_sha256=sha256,
        content_type="text/plain",
        expected_size=size,
    )


# --------------------------------------------------------------------------- Task 7 identities


def test_import_failure_reason_maps_complete_typed_vocabulary_without_identity_leakage() -> None:
    source = _source()
    cases = [
        (StagingVersionRequired(), "staging_version_required"),
        (
            UploadIdentityMismatch(
                source=source,
                expected_sha256="a" * 64,
                observed_sha256="b" * 64,
                expected_size=7,
                observed_size=7,
                etag="secret-etag",
                classification="digest_mismatch",
            ),
            "upload_identity_digest_mismatch",
        ),
        (
            UploadIdentityMismatch(
                source=source,
                expected_sha256="a" * 64,
                observed_sha256="a" * 64,
                expected_size=7,
                observed_size=8,
                etag="secret-etag",
                classification="size_mismatch",
            ),
            "upload_identity_size_mismatch",
        ),
        (StagedSourceUnavailable(source), "staged_source_missing"),
        (StagedSourceChanged(source), "staged_source_changed"),
        (StorageUnavailable(StorageStage.STAGING_PUT), "storage_unavailable_staging_put"),
        (StorageUnavailable(StorageStage.VERSIONING), "storage_unavailable_versioning"),
        (StorageUnavailable(StorageStage.SOURCE_GET), "storage_unavailable_source_get"),
        (StorageUnavailable(StorageStage.SOURCE_READ), "storage_unavailable_source_read"),
        (StorageUnavailable(StorageStage.TARGET_HEAD), "storage_unavailable_target_head"),
        (StorageUnavailable(StorageStage.TARGET_GET), "storage_unavailable_target_get"),
        (StorageUnavailable(StorageStage.TARGET_READ), "storage_unavailable_target_read"),
        (StorageUnavailable(StorageStage.COPY), "storage_unavailable_copy"),
        (StorageUnavailable(StorageStage.RETENTION), "storage_unavailable_retention"),
        (
            WormNotApplied(
                target_bucket="documents-secret",
                target_key="a" * 64,
                target_version_id="target-version-secret",
            ),
            "worm_not_applied",
        ),
        (
            TargetIdentityConflict(
                source=source,
                target_bucket="documents-secret",
                target_key="a" * 64,
                target_version_id="target-version-secret",
                observed_sha256="b" * 64,
                observed_size=7,
            ),
            "target_identity_conflict",
        ),
        (commit_svc._ItemCommitError("restage_source_changed"), "restage_source_changed"),
        (commit_svc._ItemCommitError("restage_source_unavailable"), "restage_source_unavailable"),
    ]

    reasons = [commit_svc._failure_reason(exc) for exc, _expected in cases]
    assert reasons == [expected for _exc, expected in cases]
    forbidden = (
        "secret-etag",
        "documents-secret",
        "target-version-secret",
        "a" * 64,
        "b" * 64,
    )
    assert all(token not in reason for reason in reasons for token in forbidden)


def test_import_failure_reason_preserves_existing_allow_list_and_fixes_unexpected_errors() -> None:
    allowed = (
        "blank_identifier",
        "context_register_import_unsupported",
        "evidence_bytes_already_vaulted",
        "form_template_import_unsupported",
        "interested_parties_register_import_unsupported",
        "no_staged_bytes",
        "owner_ambiguous",
        "owner_not_found",
        "risk_register_import_unsupported",
        "source_bytes_in_foreign_bucket",
        "staged_object_not_found",
        "unknown_document_type",
    )
    mapped = [commit_svc._failure_reason(commit_svc._ItemCommitError(reason)) for reason in allowed]
    assert mapped == [*allowed]
    raw = "endpoint=https://storage.internal bucket=secret repr-leak-marker"
    assert commit_svc._failure_reason(RuntimeError(raw)) == "internal_error"
    assert raw not in commit_svc._failure_reason(RuntimeError(raw))


def test_restageable_prior_reason_set_is_exact() -> None:
    assert commit_svc._RESTAGEABLE_REASONS == frozenset(
        {
            "upload_identity_digest_mismatch",
            "upload_identity_size_mismatch",
            "staged_source_missing",
            "staged_source_changed",
        }
    )
    assert "staging_version_required" not in commit_svc._RESTAGEABLE_REASONS


async def test_restage_source_updates_only_locator_after_exact_digest_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    payload = b"approved bytes"
    rel_path = "reviewed.txt"
    (source_root / rel_path).write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    file = commit_svc.ImportFile(
        rel_path=rel_path,
        filename=rel_path,
        size_bytes=len(payload),
        mime_type="text/plain",
        sha256=sha,
        staged_blob_uri=f"s3://import-staging/{sha}?versionId=old",
        scan_flags={"disposition": "included", "review_marker": "keep"},
        included_candidate=True,
    )
    before = {
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
        "mime_type": file.mime_type,
        "rel_path": file.rel_path,
        "scan_flags": file.scan_flags,
    }
    new_source = _source(sha256=sha, version_id="new-version", size=len(payload))

    async def stage_stream(handle: object, *, content_type: str) -> object:
        assert content_type == "text/plain"
        assert handle.read() == payload  # type: ignore[union-attr]
        return commit_svc.ingestion_storage.StagedResult(
            sha256=sha,
            staged_blob_uri=commit_svc.ingestion_storage.format_staged_uri(new_source),
            version_id="new-version",
            size_bytes=len(payload),
            source=new_source,
        )

    monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", stage_stream)
    result = await commit_svc._restage_source(file, str(source_root))

    assert result == new_source
    assert file.staged_blob_uri.endswith("?versionId=new-version")
    assert {
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
        "mime_type": file.mime_type,
        "rel_path": file.rel_path,
        "scan_flags": file.scan_flags,
    } == before


@pytest.mark.parametrize(
    ("rel_path", "staged_sha", "staged_size", "expected_reason"),
    [
        ("../escape.txt", None, None, "restage_source_unavailable"),
        ("reviewed.txt", "b" * 64, 14, "restage_source_changed"),
        ("reviewed.txt", "a" * 64, 99, "restage_source_changed"),
    ],
)
async def test_restage_source_fails_closed_on_confinement_or_changed_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rel_path: str,
    staged_sha: str | None,
    staged_size: int | None,
    expected_reason: str,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "reviewed.txt").write_bytes(b"approved bytes")
    file = commit_svc.ImportFile(
        rel_path=rel_path,
        filename="reviewed.txt",
        size_bytes=14,
        mime_type=None,
        sha256="a" * 64,
        staged_blob_uri="s3://import-staging/old?versionId=old",
        scan_flags={"disposition": "included"},
        included_candidate=True,
    )
    old_locator = file.staged_blob_uri

    async def stage_stream(_handle: object, *, content_type: str) -> object:
        assert content_type == "application/octet-stream"
        assert staged_sha is not None and staged_size is not None
        source = _source(sha256=staged_sha, version_id="restaged", size=staged_size)
        return commit_svc.ingestion_storage.StagedResult(
            sha256=staged_sha,
            staged_blob_uri=commit_svc.ingestion_storage.format_staged_uri(source),
            version_id="restaged",
            size_bytes=staged_size,
            source=source,
        )

    monkeypatch.setattr(commit_svc.ingestion_storage, "stage_stream", stage_stream)
    with pytest.raises(commit_svc._ItemCommitError) as caught:
        await commit_svc._restage_source(file, str(source_root))
    assert caught.value.reason == expected_reason
    assert file.staged_blob_uri == old_locator


# --------------------------------------------------------------------------- parse_identifier


def test_parse_identifier_round_trips_format() -> None:
    assert parse_identifier("SOP-PUR-014") == ("SOP", "PUR", 14)
    assert parse_identifier(format_identifier("SOP", 14, "PUR")) == ("SOP", "PUR", 14)


def test_parse_identifier_area_omitted() -> None:
    assert parse_identifier("SOP-014") == ("SOP", None, 14)
    assert parse_identifier(format_identifier("POL", 3)) == ("POL", None, 3)


def test_parse_identifier_non_conforming_has_no_seq() -> None:
    # A preserved code with no trailing numeric segment → area/seq are None (caller defaults GEN).
    parsed = parse_identifier("QM-MANUAL")
    assert parsed.type_code == "QM"
    assert parsed.area_code is None
    assert parsed.seq is None


def test_parse_identifier_multi_segment_area() -> None:
    assert parse_identifier("SOP-PUR-A-002") == ("SOP", "PUR-A", 2)


# --------------------------------------------------------------------------- state-machine guards


def test_committing_excluded_from_lock_liveness_and_active_sets() -> None:
    # The #1 trap: a lock-free commit state must NOT be in the lock-liveness reaper's set nor the
    # active-run set (commit holds no source-root lock → the reaper would instantly FAIL it).
    for st in (ImportRunStatus.COMMITTING, ImportRunStatus.PARTIALLY_COMMITTED):
        assert st not in svc._IN_PROGRESS
        assert st not in repo._ACTIVE_STATES
        assert st not in svc._TERMINAL  # in-flight / resumable — not "done"


def test_completed_is_terminal_but_not_active() -> None:
    assert ImportRunStatus.COMPLETED in svc._TERMINAL
    assert ImportRunStatus.COMPLETED not in svc._IN_PROGRESS
    assert ImportRunStatus.COMPLETED not in repo._ACTIVE_STATES


def test_cancel_blocked_covers_commit_region() -> None:
    # Cancel must 409 once a vault write has happened (committing/committed) — §11.4 no-rollback.
    for st in (
        ImportRunStatus.COMMITTING,
        ImportRunStatus.PARTIALLY_COMMITTED,
        ImportRunStatus.COMPLETED,
    ):
        assert st in svc._CANCEL_BLOCKED
    # but a human review rest-state stays cancellable.
    assert ImportRunStatus.REVIEWING not in svc._CANCEL_BLOCKED


def test_commit_start_and_resume_sets() -> None:
    assert svc._COMMIT_START == (ImportRunStatus.PROPOSED, ImportRunStatus.REVIEWING)
    assert svc._COMMIT_RESUME == (ImportRunStatus.PARTIALLY_COMMITTED,)


# --------------------------------------------------------------------------- the fold (collidable)


def _node(identifier: str | None, source: str | None) -> ImportProposalNode:
    return ImportProposalNode(proposed_identifier=identifier, identifier_source=source)


def test_fold_preserved_code_is_collidable() -> None:
    st = fold_file_decisions([], _node("SOP-PUR-014", "preserved_doc_code"), None)
    assert st.identifier == "SOP-PUR-014"
    assert st.identifier_source == "preserved_doc_code"
    assert st.identifier_collidable is True


def test_fold_sentinel_is_not_collidable() -> None:
    # The "{type}-<new>" suggested default must NOT collide (it is allocated fresh at commit).
    st = fold_file_decisions([], _node("SOP-<new>", "suggested_default"), None)
    assert st.identifier_collidable is False


def test_fold_human_corrected_identifier_is_collidable() -> None:
    dec = ImportDecision(
        action=ImportDecisionAction.CORRECT, after={"kind": "DOCUMENT", "identifier": "SOP-QA-009"}
    )
    st = fold_file_decisions([dec], _node("SOP-<new>", "suggested_default"), None)
    assert st.identifier == "SOP-QA-009"
    assert st.identifier_source == "human"
    assert st.identifier_collidable is True
    assert st.commit_ready is True  # included (correct) + kind-confirmed


# --------------------------------------------------------------------------- decided_by


def test_decided_by_engine_vs_human() -> None:
    accept = ImportDecision(action=ImportDecisionAction.ACCEPT, after={"kind": "DOCUMENT"})
    correct = ImportDecision(action=ImportDecisionAction.CORRECT, after={"type_code": "WI"})
    assert _decided_by([accept]) == "engine_confirmed"
    assert _decided_by([correct, accept]) == "human_corrected"
    assert _decided_by([]) == "engine_confirmed"


# --------------------------------------------------------------------------- commit-result enum


def test_commit_result_status_values() -> None:
    assert IMPORT_COMMIT_RESULT_STATUS_VALUES == ("success", "failed", "noop")
    assert ImportCommitResultStatus.SUCCESS.value == "success"


# --------------------------------------------------------------------------- Import Report renderer


def _report_data(**over: object) -> ImportReportData:
    base: dict[str, object] = dict(
        run_id="run-1",
        source_root="/srv/import/source/qms",
        created_by="u-create",
        committed_by="u-commit",
        classifier_version="rule-heuristic-1",
        final_status="Completed",
        counts={"included": 3, "commit": {"committed": 2, "failed": 1}},
        committed=[
            CommittedItem("SOP-PUR-014", "DOCUMENT", "purchasing/sop.docx", "engine_confirmed"),
            CommittedItem("REC-GEN-001", "RECORD", "records/audit.pdf", "human_corrected"),
        ],
        failed=[FailedItem("forms/qm.docx", "form_template_import_unsupported")],
        star_coverage={"covered": 4, "total": 20},
    )
    base.update(over)
    return ImportReportData(**base)  # type: ignore[arg-type]


def test_import_report_render_contains_sections_and_items() -> None:
    md = render_import_report(_report_data())
    assert md.startswith("# Import Report — /srv/import/source/qms")
    for header in ("## Run", "## Counts", "## Committed items", "## Failed items"):
        assert header in md
    assert "SOP-PUR-014" in md and "REC-GEN-001" in md
    assert "form_template_import_unsupported" in md
    assert "rule-heuristic-1" in md


def test_import_report_declares_deferred_revision_chain_reconstruction() -> None:
    """[Batch 10] The R10 ``reconstruct_revision_chain`` opt-in is recorded but NOT materialized in
    v1. The report must say so plainly — silently dropping it let an operator believe an approved
    revision history had been imported when every member landed as its own Effective document."""
    md = render_import_report(_report_data(deferred_revision_chain_families=["SOP-PUR-014", "QM"]))
    assert "## Deferred — revision-chain reconstruction (R10)" in md
    assert "**not** materialize" in md
    # It must state the ACTUAL outcome: only the effective member is imported, the rest excluded
    # (rebuild_proposals drops every non-effective member from the keep set).
    assert "effective member" in md and "**excluded**" in md
    assert "SOP-PUR-014" in md and "QM" in md


def test_import_report_omits_the_deferral_section_when_nothing_opted_in() -> None:
    md = render_import_report(_report_data())
    assert "Deferred — revision-chain reconstruction" not in md


def test_import_report_render_handles_empty_sets() -> None:
    md = render_import_report(_report_data(committed=[], failed=[], star_coverage=None))
    assert "_(nothing committed)_" in md
    assert "_(no failures)_" in md
    # a pipe in a value must not break the table (escaped)
    md2 = render_import_report(
        _report_data(failed=[FailedItem("a|b.docx", "err|or")], committed=[])
    )
    assert "a\\|b.docx" in md2
