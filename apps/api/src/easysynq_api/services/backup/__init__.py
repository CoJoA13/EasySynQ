"""Backup + restore-test drill (slice S8b2, doc 08 §8 / AC#5).

The restore-test **drill** is the heart of setup gate G-C: it produces a real backup (``pg_dump``
custom-format archive + a MinIO blob manifest), restores it into an isolated **scratch DATABASE**,
copies the manifested blobs into a non-WORM scratch bucket, and runs an integrity triad on the
RESTORED copy — per-table row-count parity, the ``document_version → blob`` FK check, and a blob
SHA-256 re-hash. Only a PASS (persisted to ``backup_policy.last_restore_test_result``) satisfies
G-C; finalize reads the persisted result and never runs the drill inline (it is a worker task).

Faithful (owner fork): a real ``pg_dump``/``pg_restore`` artifact round-trip, not a logical copy —
so the drill proves the actual backup mechanism, the thing "configured-but-unverified ≠ backup"
demands. The drill runs as the **OWNER** DB role (``settings.sync_dsn``); the runtime
``easysynq_app`` role can neither dump the whole DB nor ``CREATE DATABASE``.
"""

from __future__ import annotations

from .drill import DrillResult, ScratchHandle, build_durable_backup, run_drill
from .service import (
    configure_backup_destination_check,
    run_restore_test,
    run_scheduled_backups,
)

__all__ = [
    "DrillResult",
    "ScratchHandle",
    "build_durable_backup",
    "configure_backup_destination_check",
    "run_drill",
    "run_restore_test",
    "run_scheduled_backups",
]
