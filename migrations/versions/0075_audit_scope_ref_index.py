"""Batch 12 (review 2026-07-22): index the per-document audit-history access path.

``GET /documents/{id}/audit-events`` (api/audit.py::document_audit_events) is the surface an auditor
uses to read one document's full trail, and it filters on a column that carried no index at all::

    WHERE org_id = ? AND scope_ref = ? AND id < :cursor  ORDER BY id DESC  LIMIT ?

``scope_ref`` is the controlled identifier, so this is a highly selective predicate over a table
that only ever grows — but with nothing to serve it, every page fell back to scanning each monthly
partition and sorting. ``(org_id, scope_ref, id)`` puts the two equality columns first and ``id``
last, where it serves BOTH the keyset range and the ``ORDER BY``; the plan becomes
``Limit → Merge Append → Index Scan Backward`` with no Sort node.

⚠ The name follows ``NAMING_CONVENTION["ix"]`` (db/base.py) — ``ix_<table>_<all columns>``. It is
NOT ``ix_audit_event_scope_ref``: that would read as a single-column index and mislead a later
reader into thinking a bare ``WHERE scope_ref = ?`` is served. It is not — ``org_id`` leads.

Created on the PARTITIONED PARENT rather than per-partition: PostgreSQL propagates a parent index
to every existing partition, and ``CREATE TABLE … PARTITION OF`` (what the SECURITY DEFINER
``easysynq_create_audit_partition`` function from 0010 runs, driven daily by the ``roll_partitions``
Beat) gives each FUTURE month its matching child index automatically. So this stays correct without
touching the partition-rotation code.

⚠ OPERATOR NOTE — locking. Measured on PG16 from inside the building transaction, this takes
``ShareLock`` on the parent and on every partition; only the brand-new, not-yet-visible index
relations take ``AccessExclusiveLock``. The practical consequence:

* **reads are NOT blocked** — ``AccessShareLock`` does not conflict with ``ShareLock``, so the
  auditor read path, ``/healthz`` and every GET keep serving for the whole build;
* **writes ARE blocked.** Essentially every mutating request writes an ``audit_event`` in the same
  transaction, so the write path convoys behind the build. ``CREATE TABLE … PARTITION OF`` blocks
  too, so ``roll_partitions``/``ensure_partitions`` stall (both are best-effort with a daily retry,
  so they self-heal).

That is harmless on the FRESH-boot path — the compose ``migrate`` service runs ``alembic upgrade
head`` to completion and exits before api/worker/beat start, so there are no concurrent writers.
It matters for the IN-PLACE path (``easysynq upgrade`` → ``compose run --rm worker … cli.upgrade``),
which runs against a LIVE stack. There is no ``lock_timeout`` configured anywhere, and env.py wraps
the whole run in ONE transaction, so a build queued behind an open writer holds everything until the
entire ``upgrade head`` commits. Cost scales with history (~50 MB of index per million audit rows).
**Stop api/worker/beat, or take a maintenance window, before running an in-place upgrade through
this revision** — the exact steps are in docs/runbooks/backup-restore.md § Upgrade.

The DOWNGRADE is the opposite trade: ``DROP INDEX`` takes a true ``AccessExclusiveLock`` on the
parent and all partitions (so it blocks reads too), but it is catalog-only and effectively instant.

Two things deliberately NOT done:

* no ``CONCURRENTLY`` — PostgreSQL rejects it on a partitioned table, and Alembic runs this inside
  the migration transaction anyway;
* not partial (``WHERE scope_ref IS NOT NULL``) — a partial index would be smaller, since only
  vault/lifecycle rows populate ``scope_ref``, but it would then have to be registered in
  ``env.py::_MIGRATION_MANAGED_INDEXES`` and kept OUT of the ORM (``alembic check`` reflects
  predicate indexes). A plain btree stays modelled in ``AuditEvent.__table_args__`` next to the four
  existing ones, which is the cheaper invariant to keep true.

The auto-named child indexes (``audit_event_YYYY_MM_org_id_scope_ref_id_idx``) need no autogenerate
exclusion — ``env.py::_include_object`` already drops everything prefixed ``audit_event_``. The
``ix_``-prefixed parent is NOT covered by that rule, which is exactly why it is mirrored in the ORM.
"""

from __future__ import annotations

from alembic import op

revision: str = "0075_audit_scope_ref_index"
down_revision: str | None = "0074_operator_alarms"
branch_labels: str | None = None
depends_on: str | None = None

_INDEX = "ix_audit_event_org_id_scope_ref_id"


def upgrade() -> None:
    # A btree tuple cannot exceed 2704 bytes, and `scope_ref` is unbounded Text built from
    # caller-supplied values. New rows are capped at the model (AuditEvent._bound_scope_ref), but a
    # row written BEFORE that cap could still be over-long and would abort this DDL with a raw
    # PostgreSQL error mid-upgrade. So catch that specific failure and re-raise it as something an
    # operator can act on.
    #
    # ⚠ Deliberately NOT a `SELECT ... WHERE octet_length(scope_ref) > N` pre-flight. Two reasons,
    # both of which bit the first version of this migration:
    #   * raw length does not decide indexability. PostgreSQL compresses before indexing, so a long
    #     compressible value indexes fine while a shorter incompressible one does not. Any fixed
    #     byte threshold therefore rejects legitimate rows — including ones this very migration's
    #     own cap permits (512 four-byte characters is 2048 bytes, over a 2000-byte bound).
    #   * a data-dependent read breaks OFFLINE generation. Under `alembic upgrade X:Y --sql` the
    #     bind emits the statement and returns None, so `.scalar_one()` raises AttributeError and no
    #     SQL is produced at all. Letting the DDL speak keeps `--sql` working.
    # Postgres itself is the exact oracle here, and it costs nothing to ask it.
    try:
        op.create_index(_INDEX, "audit_event", ["org_id", "scope_ref", "id"])
    except Exception as exc:  # noqa: BLE001 - re-raised below; only the size failure is translated
        if "index row size" not in str(exc):
            raise
        raise RuntimeError(
            f"Cannot create {_INDEX}: at least one audit_event row has a scope_ref too large to "
            "index (PostgreSQL btree tuples are capped at 2704 bytes). Such rows predate the cap "
            "now enforced in AuditEvent._bound_scope_ref. audit_event is append-only and "
            "hash-chained, so this migration will not rewrite them. Find the candidates with:  "
            "SELECT id, occurred_at, octet_length(scope_ref) FROM audit_event ORDER BY 3 DESC "
            "LIMIT 20;  then decide with the quality owner how to proceed - they are legitimate "
            "audit records, so the usual answer is to shorten them under a documented, signed-off "
            "correction, not to delete them."
        ) from exc


def downgrade() -> None:
    # Dropping the parent index drops its inherited child indexes with it; no per-partition cleanup.
    op.drop_index(_INDEX, table_name="audit_event")
