# Issue #360 — bind pending purge authority to the lawful disposition

> Owner-approved design for the `pending_blob_purge` hardening fast-follow from Batch 5.
> Backend + migration + integration tests + docs. Migration head `0080 → 0081`; no new
> permission key and no OpenAPI change.

## 1. Problem

`pending_blob_purge` is a durable command to the records reaper: if no `blob` row owns the
marker's `(sha256, bucket, object_key)`, the worker calls `purge_object`, including
`BypassGovernanceRetention` when the marker says so. The non-owner application role can insert
and update marker rows. A forged marker can therefore name a live object under a false SHA and
ask the worker to erase it, turning a database compromise into an S3/WORM bypass.

The immediate post-disposition purge does not consume arbitrary database rows: it receives an
in-memory `_PurgeSpec` produced by the lawful transaction. The vulnerable boundary is crash
recovery, where the reaper treats the marker itself as authority.

## 2. Decision — authority-bound Option B

Keep crash recovery for both ordinary retention disposal and the R27 legal-order hatch, but make
the marker a pointer to authority rather than authority in its own right.

Every newly-created marker is bound to:

- the exact `record`;
- the exact immutable `disposition_event` that authorized the DESTROY; and
- for an R27 disposition, the exact executed `worm_destroy_request`.

The reaper validates those rows before erasing bytes:

1. The Record is `DISPOSED`.
2. The immutable event belongs to the same org and Record and has `action=DESTROY`.
3. An ordinary marker must point to a policy-backed, non-WORM disposition and may never bypass.
4. An R27 marker must point to an executed, non-cancelled request for the same org and Record,
   whose requester, approver, and legal basis match the immutable event. Only that branch may
   replay `bypass_governance=true`.

The raw marker boolean never establishes bypass authority.

## 3. Exact-object liveness

The safety re-check is keyed by physical identity: `(bucket, object_key)`. The marker's SHA is
diagnostic/provenance data, not part of the ownership decision. A live `blob` row at the exact
location cancels the marker even if a forged row supplied a different SHA. A matching SHA in a
different bucket remains a distinct object and does not cancel a legitimate purge.

The records reaper also refuses marker buckets outside the configured records-evidence and
structured-rendition buckets. A valid disposition event must not turn this worker into a delete
oracle for unrelated S3 namespaces (for example, audit checkpoints).

## 4. Migration and privilege model

Migration `0081` adds nullable authority references for upgrade compatibility:

- `record_id` FK → `record.id`;
- `disposition_event_id` FK → `disposition_event.id`;
- `worm_destroy_request_id` nullable FK → `worm_destroy_request.id`;
- `authority_bound boolean`.

Rows already present at upgrade are marked `authority_bound=false`. New rows receive the
server-controlled default `true`; the application role is not granted INSERT or UPDATE privilege
on that discriminator. A CHECK requires bound rows to carry the Record and disposition event and
requires a request reference whenever bypass is requested.

The migration replaces broad table UPDATE with the smallest column privilege that still permits
`SELECT … FOR UPDATE SKIP LOCKED`; an integration test proves both the claim and the denial of
sensitive-field mutation on PostgreSQL 16. INSERT is column-scoped so callers cannot opt a new row
into legacy mode or set `created_at`.

Legacy rows cannot be reconstructed reliably because their deleted `evidence_blob` link was their
only Record association. They remain recoverable but are forced through the non-bypass path and
the exact-object liveness check. A legacy R27 marker may therefore wait until its object lock
expires; an upgrade never converts an unbound database row into governance-bypass authority.

## 5. Runtime shape

`_write_tombstone` returns the explicit `DispositionEvent`. DESTROY flows write that event before
marking evidence in the same transaction; rollback remains atomic if marking fails.
`_mark_record_evidence_for_purge` receives the event and optional R27 request and writes their
identifiers into every evidence and structured-rendition marker.

The reaper snapshots marker fields, validates authority through one repository query/helper, then:

- drops a marker without touching S3 when an exact live object owner exists;
- drops and warns on an invalid bound marker;
- refuses a target outside the configured records/renditions buckets;
- treats an unbound legacy marker as non-bypass only;
- otherwise purges with the validated/derived bypass decision and deletes the marker after success.

The immediate `_purge_marked` path remains post-commit, idempotent, and re-capture-safe.

## 6. Tests

Docker-backed integration coverage must distinguish these cases:

- ordinary policy DESTROY crash recovery still purges;
- R27 crash recovery still replays governance bypass after two-person authorization;
- a bare forged marker is structurally refused;
- a bound marker with a mismatched Record/event/request is refused without an S3 call;
- a false SHA targeting a live `(bucket, object_key)` is cancelled, leaving bytes intact;
- legacy rows are never allowed to bypass;
- application-role UPDATE of authority/target fields is denied while `FOR UPDATE SKIP LOCKED`
  still succeeds.

Existing re-capture, cross-bucket, storage-failure, DB-failure, and starvation-rotation tests remain
the regression backstop.

## 7. Documentation

Record the decision as an R27 addendum in `docs/decisions-register.md`, describe the expanded
entity in `docs/14-data-model.md`, correct the reaper description in
`docs/06-records-and-evidence.md`, and add the shipped slice to `docs/slice-history.md`.

## 8. Non-goals

- The separate per-SHA capture-versus-purge advisory lock remains Issue #359.
- No evidence-pack derived-artifact purge (Issue #361).
- No permission-catalog or API contract change.
- No change to MinIO object-lock mode or the R27 two-person policy.
