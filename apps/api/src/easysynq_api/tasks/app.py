"""Celery application. Redis is the broker and result backend (D4)."""

from __future__ import annotations

from celery import Celery

from ..config import get_settings

_settings = get_settings()

app = Celery(
    "easysynq",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
)
app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # S4: future-dated release sweep — activate Approved versions whose effective_from arrived.
        "release-due-versions": {
            "task": "easysynq.release_due_versions",
            "schedule": 300.0,  # every 5 minutes
        },
        # S6: the decoupled chain-linker — bounded written-but-not-yet-chained lag (target ≤5 s).
        "audit-chain-link": {
            "task": "easysynq.audit.chain_link",
            "schedule": 30.0,  # continuous, ~30 s
        },
        # S6: nightly chain verification (the API also runs it on demand).
        "audit-verify-chain": {
            "task": "easysynq.audit.verify_chain",
            "schedule": 86400.0,  # daily
        },
        # S6: signed checkpoint anchor — primary cadence (on-shutdown is best-effort only).
        "audit-checkpoint-anchor": {
            "task": "easysynq.audit.checkpoint_anchor",
            "schedule": 900.0,  # every 15 minutes
        },
        # S6: keep the rolling monthly-partition runway ≥2 months ahead (idempotent).
        "audit-roll-partitions": {
            "task": "easysynq.audit.roll_partitions",
            "schedule": 86400.0,  # daily
        },
        # S7: nightly full reconcile of the read-only Effective-only filesystem mirror (doc 04
        # §10.4). Release/obsolete also enqueue this incrementally; both share one idempotent task.
        "mirror-sync-nightly": {
            "task": "easysynq.mirror.sync",
            "schedule": 86400.0,  # daily
        },
    },
)
