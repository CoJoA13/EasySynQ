"""Celery/Beat tasks for backup + the restore-test drill (slice S8b2, doc 08 §8 / AC#5).

``backup_run`` writes the nightly durable archive(s); ``backup_restore_test`` runs the gating
backup→restore-into-scratch drill (gate G-C). Both follow the S6/S7 worker idiom — a thin
``@app.task`` over ``asyncio.run`` of an async coroutine with its own disposed engine. The drill
needs the OWNER DB role for pg_dump/CREATE DATABASE; the worker container exposes it via
``DATABASE_URL_SYNC`` (the same owner DSN Alembic uses) — see CLAUDE.md.

The api enqueues ``backup_restore_test`` from ``POST /setup/run-restore-test``; the gate then reads
the persisted ``backup_policy.last_restore_test_result`` — the drill is never run inline.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ..services.backup import run_restore_test, run_scheduled_backups
from .app import app


@app.task(name="easysynq.backup.run")  # type: ignore[untyped-decorator]
def backup_run() -> dict[str, Any]:
    """Nightly durable backup of every configured backup_policy (best-effort + logged)."""
    return asyncio.run(run_scheduled_backups())


@app.task(name="easysynq.backup.restore_test")  # type: ignore[untyped-decorator]
def backup_restore_test(org_id: str, actor_id: str | None = None) -> dict[str, Any]:
    """Run the backup→restore-into-scratch drill for ``org_id`` and persist PASS/FAIL (gate G-C)."""
    return asyncio.run(
        run_restore_test(uuid.UUID(org_id), uuid.UUID(actor_id) if actor_id else None)
    )
