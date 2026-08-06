"""Backup + restore-test drill (slice S8b2, doc 08 §8 / AC#5).

The restore-test **drill** is the heart of setup gate G-C: it writes a ``pg_dump`` custom-format
archive plus a MinIO blob manifest, restores the database into an isolated **scratch DATABASE**,
copies the manifested bytes from the configured source object store under a unique prefix in the
configured shared scratch bucket,
and runs an integrity triad on the scratch copy — per-table row-count parity, the
``document_version → blob`` FK check, and a blob SHA-256 re-hash. Only a PASS (persisted to
``backup_policy.last_restore_test_result``) satisfies G-C; finalize reads the persisted result and
never runs the drill inline (it is a worker task).

Faithful (owner fork): a real ``pg_dump``/``pg_restore`` archive round-trip, not a logical copy. The
drill proves the database/manifest mechanism and source-store object reads; because the archive has
no object bytes, it does not prove source-independent recovery. The drill runs as the **OWNER** DB
role (``settings.sync_dsn``); the runtime ``easysynq_app`` role can neither dump the whole DB nor
``CREATE DATABASE``.
"""

from __future__ import annotations

from .drill import DrillResult, ScratchHandle, build_durable_backup, run_drill
from .restore import RestoreResult
from .service import (
    configure_backup_destination_check,
    run_restore,
    run_restore_test,
    run_scheduled_backups,
    run_scheduled_restore_tests,
    verify_latest_retained_backup,
)

__all__ = [
    "DrillResult",
    "RestoreResult",
    "ScratchHandle",
    "build_durable_backup",
    "configure_backup_destination_check",
    "run_drill",
    "run_restore",
    "run_restore_test",
    "run_scheduled_backups",
    "run_scheduled_restore_tests",
    "verify_latest_retained_backup",
]
