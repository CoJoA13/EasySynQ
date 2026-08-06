from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from easysynq_api.services.backup import archive, drill, restore


def test_restore_pass_preserves_empty_post_cutover_actions_compatibility_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive_path = tmp_path / "backup.tar"
    archive_path.write_bytes(b"archive")
    settings = SimpleNamespace(
        sync_dsn="postgresql://owner@example.invalid/easysynq",
        backup_encryption_key="test-key",
        s3_bucket_restore_scratch="restore-scratch",
        s3_bucket_documents="documents",
    )

    monkeypatch.setattr(archive, "verify_archive", lambda _src: True)
    monkeypatch.setattr(restore.crypto, "is_encrypted_archive", lambda _src: False)
    monkeypatch.setattr(
        archive,
        "read_manifest",
        lambda _src: {
            "blobs": [],
            "config": {"table_counts": {"organization": 1}},
            "legs": {"realm_export": "absent", "config_snapshot": "present"},
        },
    )
    monkeypatch.setattr(archive, "unpack_dump", lambda _src, target: target / "db.dump")
    monkeypatch.setattr(restore, "_sweep_stale_restore", lambda _dsn: None)
    monkeypatch.setattr(drill, "_create_scratch_db", lambda _dsn, _db: None)
    monkeypatch.setattr(drill, "_drop_scratch_db", lambda _dsn, _db: None)
    monkeypatch.setattr(drill, "_delete_scratch_objects", lambda *_args: None)
    monkeypatch.setattr(archive, "restore_database", lambda _dsn, _db, _dump: None)
    monkeypatch.setattr(drill, "_copy_blobs", lambda *_args: None)
    monkeypatch.setattr(
        drill,
        "run_triad",
        lambda _settings, _handle: drill.DrillResult("PASS", "restore verified"),
    )
    monkeypatch.setattr(restore, "_scratch_max_audit_id", lambda _dsn, _db: 0)
    monkeypatch.setattr(restore, "_scratch_max_bundled_checkpoint", lambda _dsn, _db: None)
    monkeypatch.setattr(
        restore,
        "_restored_org_id",
        lambda _dsn, _db: uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )
    monkeypatch.setattr(restore, "_scratch_canonical_version", lambda _dsn, _db: 1)
    monkeypatch.setattr(
        restore,
        "_reverify_chain",
        lambda _dsn, _db, _version: {"verified": True, "attested": False},
    )

    result = restore.run_restore(  # type: ignore[arg-type]
        settings,
        archive_path=str(archive_path),
        fetch_off_host=lambda _settings, _org_id: 0,
    )

    assert result.result == "PASS", result
    assert result.details["post_cutover_actions"] == []
    assert result.details["future_recovery_requirements"]
    assert "reindex" not in str(result.details["post_cutover_actions"]).lower()
    assert "cutover" not in str(result.details["post_cutover_actions"]).lower()
