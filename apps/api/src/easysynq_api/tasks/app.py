"""Celery application. Redis is the broker and result backend (D4)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ParamSpec, Protocol, TypeVar, cast

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
        # Phase-1 (I-7): scheduled retained-backup verify — decrypt + restore-into-scratch +
        # integrity triad over the NEWEST RETAINED durable archive for every backup_policy, so a
        # silently-rotting REAL backup is caught between the manual G-C drills (the nightly job
        # above only WRITES archives; this proves the stored, encrypted ones still restore). Weekly
        # default (RESTORE_TEST_INTERVAL_SECONDS); heavy (scratch DB + full pg_restore + blob
        # re-hash), runs as the OWNER role, never raises (an honest FAIL is persisted + audited).
        "backup-restore-test-weekly": {
            "task": "easysynq.backup.scheduled_restore_test",
            "schedule": float(_settings.restore_test_interval_seconds),
        },
        # S-rec-2: daily records retention sweep (doc 06 §5.3) — flips due ACTIVE records to
        # DUE_FOR_REVIEW + auto-disposes low-risk (review_required=false) policies once WORM allows.
        "records-retention-sweep": {
            "task": "easysynq.records.retention_sweep",
            "schedule": 86400.0,  # daily
        },
        # S-drift-1: daily D5 periodic re-review sweep (doc 04 §9.1)
        "documents-review-sweep": {
            "task": "easysynq.documents.review_sweep",
            "schedule": 86400.0,  # daily
        },
        # S-mr-1: daily clause-9.3 management-review cadence sweep — mints the next Scheduled
        # review when the org's cadence horizon (mgmt_review_cadence_months) is reached.
        "documents-mgmt-review-sweep": {
            "task": "easysynq.documents.mgmt_review_sweep",
            "schedule": 86400.0,  # daily
        },
        # S-ack-1: daily acknowledgement sweep (doc 04 §8.3 / R15 target-entry catch-up + the
        # self-heal for lost doc-scoped enqueues).
        "ack-sweep": {
            "task": "easysynq.ack.sweep",
            "schedule": 86400.0,  # daily
        },
        # S-drift-2: the D2+D3 mirror integrity scan (doc 05 §9.2.1 / R11 — the accepted drift
        # window equals this interval; default hourly, configurable via
        # MIRROR_SCAN_INTERVAL_SECONDS). The nightly mirror-sync also scans (scan-first pipeline).
        "mirror-scan": {
            "task": "easysynq.mirror.scan",
            "schedule": float(_settings.mirror_scan_interval_seconds),
        },
        # S-drift-3: the D1 blob integrity verify (doc 03 §8.2 / doc 05 §9.1 D1) — a daily
        # rolling re-hash of the least-recently-verified blobs (BLOB_VERIFY_INTERVAL_SECONDS,
        # default daily; sample size BLOB_VERIFY_SAMPLE_SIZE, default 500 → full coverage every
        # ⌈N/500⌉ days by rotation).
        "blob-verify": {
            "task": "easysynq.blob.verify",
            "schedule": float(_settings.blob_verify_interval_seconds),
        },
        # S-pack-1: daily reaper for evidence-pack builds stuck in BUILDING (a hard worker kill
        # between the BUILDING commit and the build's error handler strands them) → FAILED.
        "packs-reap-stalled-builds": {
            "task": "easysynq.packs.reap_stalled_builds",
            "schedule": 86400.0,  # daily
        },
        # S-ing-1/2: recover ingestion runs wedged in any in-progress stage (Scanning/Scanned/
        # Extracting/Classifying — a worker kill strands them) → FAILED + free the source-root lock.
        # Tighter cadence than the daily packs reaper because a stuck run holds the source root's
        # lock (blocking re-import) continuously until reaped (doc 09 §3.3).
        "ingestion-reap-stalled-runs": {
            "task": "easysynq.ingestion.reap_stalled_runs",
            "schedule": 600.0,  # every 10 minutes
        },
        # S-ing-5: re-enqueue a Committing run wedged by a crashed commit worker (progress-liveness,
        # never fails it — committed WORM items are permanent). Same 10-min cadence as the runs job.
        "ingestion-reap-stalled-commits": {
            "task": "easysynq.ingestion.reap_stalled_commits",
            "schedule": 600.0,  # every 10 minutes
        },
    },
)

_P = ParamSpec("_P")
# Covariant: ``_R_co`` only ever appears in return position in the Protocol (the task's call
# result), which mypy --strict requires a covariant TypeVar for.
_R_co = TypeVar("_R_co", covariant=True)


class CeleryTask(Protocol[_P, _R_co]):
    """The typed surface of a registered Celery task we actually use.

    Celery ships no type stubs (it is in mypy's untyped-module override), so ``app.task`` is an
    untyped decorator (hence the former per-task ``# type: ignore[untyped-decorator]``). This
    Protocol restores call-site type-checking: ``delay`` carries the SAME ParamSpec as the task, so
    a wrong-arity ``foo.delay(...)`` is now a type error."""

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _R_co: ...
    def delay(self, *args: _P.args, **kwargs: _P.kwargs) -> Any: ...
    def apply_async(self, *args: Any, **kwargs: Any) -> Any: ...


def task(*, name: str, **options: Any) -> Callable[[Callable[_P, _R_co]], CeleryTask[_P, _R_co]]:
    """Typed replacement for ``@app.task(name=…)`` — removes the per-site untyped-decorator ignore
    while preserving registration (same ``name=``) and ``.delay()`` ergonomics."""

    def deco(fn: Callable[_P, _R_co]) -> CeleryTask[_P, _R_co]:
        return cast("CeleryTask[_P, _R_co]", app.task(name=name, **options)(fn))

    return deco
