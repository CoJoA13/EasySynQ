# Issue #359 — serialize record capture with physical purge

> Owner-authorized next-issue selection after Issue #360. Backend + integration tests + docs.
> No migration, no new permission key, and no OpenAPI change.

## 1. Problem

The pending-purge worker checks whether a live `blob` row owns a physical object before deleting
that object from S3. That check closes the broad crash-recovery window, but it is not atomic with
the delete:

1. the worker observes no live owner;
2. a byte-identical record capture promotes the staged object and commits a new `blob` row;
3. the worker deletes the object it checked before the capture committed.

The result is a live `blob` row and `evidence_blob` link over missing bytes. That violates the
`blob`-row-iff-bytes` invariant and destroys the newly captured record's evidence.

## 2. Decision — one transaction advisory lock per physical object

Capture and purge share a blocking PostgreSQL transaction advisory lock keyed by the physical
object identity `(bucket, object_key)`.

For record evidence, `object_key` is the lowercase SHA-256 digest, so this is the per-SHA
serialization required by Issue #359. Including the bucket preserves Issue #360's exact-object
rule: the marker SHA is untrusted diagnostic data, and identical content in another bucket is a
different object.

The lock is transaction-scoped:

- a database commit or rollback always releases it;
- a crashed connection releases it;
- no application-managed unlock path can strand a session-level lock;
- PostgreSQL's actual `hashtext` values are resolved, de-duplicated, and numerically sorted before
  multi-object acquisition, so hash collisions can only add harmless serialization.

## 3. Capture boundary

`_attach_evidence` normalizes and de-duplicates the evidence inputs, resolves their actual
PostgreSQL `hashtext` advisory keys, de-duplicates collisions, and acquires those numeric keys in
sorted order before its first `blob` lookup. Sorting raw object keys is insufficient: a collision
can make overlapping captures acquire the effective lock set in opposite orders. It holds the locks
across:

- the WORM `finalize_worm` promotion;
- the conflict-safe `blob` insert and authoritative re-read;
- the `evidence_blob` insert; and
- the enclosing capture transaction's commit.

Actual-key acquisition prevents two multi-evidence captures with opposite input ordering or a
32-bit hash collision from deadlocking. The content-hash manifest remains order-independent and
unchanged.

`capture_record(_commit=False)` deliberately retains the locks for its caller's larger
transaction. The correction and ingestion callers already commit or roll back that transaction;
the helper must not release the locks early.

## 4. Purge boundaries

Both physical-delete paths acquire the same lock before the live-owner check:

- immediate post-commit `_purge_marked`; and
- hourly `reap_pending_blob_purges`.

The reaper already claims marker rows with `FOR UPDATE SKIP LOCKED`. Immediate purge therefore
claims its specific marker row before taking the physical-object lock as well. That common
marker-row → object-lock order prevents an immediate-purge/reaper deadlock; a missing marker means
the competing path already completed the work, so immediate purge returns without replaying it.
Because the reaper commits or rolls back per marker, that transaction end releases every other row
claim from the original batch. It therefore re-claims each snapshot immediately before taking its
physical-object lock, skipping a snapshot whose marker a competing consumer already removed.

They hold it through either:

- detecting a live owner, deleting only the stale marker, and committing; or
- validating authority, deleting the S3 object, deleting the marker, and committing.

If capture owns the lock first, purge waits and then sees the committed live owner. If purge owns
it first, capture waits until the old bytes are gone, then promotes the staged bytes and establishes
the new live owner. Neither ordering can leave a live row over deleted bytes.

On a reaper storage failure, the transaction is explicitly rolled back before continuing so the
new transaction advisory lock and row claims are released immediately while the marker remains for
the next run.

## 5. Deterministic concurrency proof

A Docker-backed integration test exercises both immediate purge and reaper recovery:

1. capture and mark a record for R27 purge, then commit without deleting the bytes;
2. pause the selected purge path after its ownership check when it enters `purge_object`;
3. start a byte-identical capture in a second database session;
4. prove PostgreSQL reports that capture waiting on an advisory lock;
5. release purge, then let capture finish; and
6. assert the recaptured object, `blob` row, and evidence link survive while the stale marker is
   removed.

Without the shared lock, capture crosses the paused purge window, the worker deletes its bytes,
and the final storage assertion fails.

## 6. Documentation

Amend the records disposition implementation note in `docs/06-records-and-evidence.md`, add the
shipped Issue #359 slice to `docs/slice-history.md`, and record the lock-order invariant in the
engineering-patterns catalog.

## 7. Non-goals

- No change to marker authority, R27 dual control, or object-lock policy.
- No schema or privilege change.
- No lock around `:init-upload`; a purge between presign and capture may require a safe upload retry
  but cannot destroy committed evidence.
- No physical purge of evidence-pack derived artifacts (Issue #361).
- No broader cross-domain blob-key redesign.
