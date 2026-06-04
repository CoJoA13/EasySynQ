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
        # S8b2: nightly durable backup of every configured backup_policy (doc 08 §8 / D-6 MVP =
        # nightly pg_dump). The gating restore-test drill is run on demand (setup / CLI), not here.
        "backup-nightly": {
            "task": "easysynq.backup.run",
            "schedule": 86400.0,  # daily
        },
        # S-rec-2: daily records retention sweep (doc 06 §5.3) — flips due ACTIVE records to
        # DUE_FOR_REVIEW + auto-disposes low-risk (review_required=false) policies once WORM allows.
        "records-retention-sweep": {
            "task": "easysynq.records.retention_sweep",
            "schedule": 86400.0,  # daily
        },
        # S-pack-1: daily reaper for evidence-pack builds stuck in BUILDING (a hard worker kill
        # between the BUILDING commit and the build's error handler strands them) → FAILED.
        "packs-reap-stalled-builds": {
            "task": "easysynq.packs.reap_stalled_builds",
            "schedule": 86400.0,  # daily
        },
        # S-ing-1: recover ingestion scans wedged in SCANNING (a worker kill strands them) → FAILED
        # +
        # free the source-root lock. Tighter cadence than the daily packs reaper because a stuck
        # scan
        # holds the source root's lock (blocking re-scan) until it is reaped (doc 09 §3.3).
        "ingestion-reap-stalled-scans": {
            "task": "easysynq.ingestion.reap_stalled_scans",
            "schedule": 600.0,  # every 10 minutes
        },
    },
)
