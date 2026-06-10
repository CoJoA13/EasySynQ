# S-drift-3 — D1 blob integrity verify + D4 superseded-copies report + the admin drift-status surface (slice design)

> **Status:** approved (owner, 2026-06-10). **Track:** v1.x drift family (backend-first), slice 3 of 3
> (the family decomposition is the S-drift-1 spec §0 fork: S-drift-1 D5 ✅ → S-drift-2 D2+D3 ✅ →
> **S-drift-3 D1 + D4 + the thin admin drift-status surface (this spec)** → trailing S-web-8).
> **This slice completes the drift family's detection legs** — after it, every doc 05 §9.1 row
> (D1–D5) is shipped. **Spec sources:** doc 05 §9.1 rows D1/D4 + §9.2.1's detection boundary (D4 is
> the ONLY leg that reaches copies outside the mirror); doc 03 §8.2 ("a scheduled `verify` job
> re-hashes a rolling sample and the full set periodically, raising an audit alarm on mismatch");
> doc 05 §6.4 (the controlled-rendition stamp + EXPORTED/PRINTED trail D4 reads). **Migration:**
> `0047` (head today: `0046`). **Closes:** the S-drift-2 spec's declared seams — the additive
> `drift_scan` `kind=BLOB_REHASH` and the `(kind, started_at DESC)` latest-per-kind read the admin
> surface was reserved for. **Opens the permission catalog (R38):** ONE new SYSTEM-domain key
> `drift.read` (owner fork §0.1) — a decisions-register entry (R41) rides in-PR. **Contract change:**
> two new admin GETs documented in `openapi.yaml` in-PR. No web work (S-web-8 trails).

## 0. Owner forks (resolved 2026-06-10, via AskUserQuestion)

1. **Permission = a NEW `drift.read` SYSTEM-domain key** (over riding `storage.read`/`config.read`).
   Drift/integrity status is its own operational capability; `storage.read` is storage *config*, and
   the D4 copies report isn't storage at all — riding it would silently widen every storage-config
   reader's view. `is_system_domain=true`, `sod_sensitive=false`, `sig_hook=false`,
   `finest_scope=SYSTEM`; seeded + granted to **System Administrator** (so the `demo` login holds it
   natively). R38 additive procedure: register entry R41, catalog-count bump, no rename/removal.
2. **D1 mismatch event = ONE `BLOB_INTEGRITY_FAILED`** (over a BLOB_TAMPER/BLOB_MISSING pair). The
   classification rides `after.classification` (`HASH_MISMATCH` | `OBJECT_MISSING` | `READ_ERROR`).
   Unlike MIRROR_STALE/MIRROR_TAMPER there is no severity split — every class is equally
   alarm-worthy (`OBJECT_MISSING` = storage tamper OR a broken blob-row-iff-bytes invariant, never
   skippable), so one name is what alert wiring keys on.
3. **D1 cadence = one daily rolling task; rotation IS the periodic full set.** Each run verifies the
   K least-recently-verified blobs (`verified_at NULLS FIRST → oldest`); defaults
   `BLOB_VERIFY_INTERVAL_SECONDS=86400`, `BLOB_VERIFY_SAMPLE_SIZE=500`, so the FULL set is provably
   covered every ⌈N/K⌉ days (a 15k-blob vault ≈ monthly) with no I/O spikes and one schedule. CLI
   `--full` gives the on-demand complete pass; the status endpoint reports coverage honestly
   (`total`, `never_verified`, `oldest_verified_at`).

Two sub-choices settled in design review (flagged to the owner with the design, approved with it):
**D4 is a LIVE READ** over the audit trail — no persisted scan row (there is nothing to correct and
nothing to baseline; the EXPORTED/PRINTED events are already the durable record); and **D1's audit
events key `object_type=config`** on the org (the mirror scan's non-attributable branch precedent)
with the `sha256` in the payload — a deduplicated blob has no single owning document, and a new
`blob` audit_object_type would be enum surface without a consumer.

## 1. Why / what

**D1 (doc 05 §9.1):** *"Re-hash a rolling sample + periodic full set; compare to stored SHA-256;
mismatch → audit alarm."* The vault's blobs are the bytes everything else points at; WORM object-lock
prevents legitimate overwrite, but bit-rot, storage-layer tamper, and invariant regressions (a blob
row whose bytes are gone) are only *detectable* by re-reading and re-hashing. `blob.sha256` is the PK
(identity IS the digest) and `blob.verified_at` (doc 14 §5.4) has been the reserved cursor since
S0 — this slice finally writes it.

**D4 (doc 05 §9.1, R11):** the verify token already shipped (S7c `/verify` — CURRENT/SUPERSEDED/
UNKNOWN for any copy in the wild). What's left is the *reportable count*: "downloads are audited so
the count of outstanding exported/printed copies of a now-superseded version is reportable."
`render_dynamic_copy` (S7d) emits `EXPORTED`/`PRINTED` audit rows keyed `object_type=version`,
`object_id=version_id`, `scope_ref=identifier` — and it only ever serves the **then-Effective**
version, so every such event on a version now `Superseded`/`Obsolete` is, by construction, an
outstanding copy of a superseded rendition. Per §9.2.1's detection boundary, D4 is the only leg that
reaches copies outside the mirror — the report is the management-visible half of that leg.

**The admin surface:** S-drift-2 wrote `drift_scan` rows but nothing reads them ("written, not yet
read — S-drift-3"). This slice adds the thin read: latest scan per kind + blob coverage + the D4
report, gated on the new `drift.read` key. The S-web-8 UI will consume exactly this.

**What already exists (bind, don't rebuild):** `drift_scan` (+ its `(kind, started_at DESC)` index,
REVOKE UPDATE/DELETE — insert-only), `DRIFT_SCAN_KIND_VALUES` as the single enum-tuple source;
`storage.fetch_bytes`/`stream_object` (the internal, never-presigned worker read path; WORM blocks
writes, not GETs); `services/common/org.get_single_org_id` (the resilient single-org lookup);
`pg_advisory_lock` + the `pg_locks` constants; the `tasks/mirror.py` worker shape (own disposed
engine, skip-if-held, never raises); the `api/config.py` admin-endpoint shape (`require()` on a
SYSTEM key); the 0028 additive-key seed shape (⚠ with the #107 resilient org lookup, NOT 0028's
DEFAULT-only `scalar_one_or_none` — this install is `AHT`).

## 2. Schema — migration `0047` (no new tables)

1. **`ALTER TYPE drift_scan_kind ADD VALUE IF NOT EXISTS 'BLOB_REHASH'`** — additive, no-op
   downgrade (the 0011 pattern). ORM: `DriftScanKind.BLOB_REHASH` member added in `_drift_enums.py`
   (a from-scratch `upgrade head` rebuilds the type from `DRIFT_SCAN_KIND_VALUES`, so migrated and
   fresh DBs converge).
2. **`ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BLOB_INTEGRITY_FAILED'`** + the matching
   `EventType` member in `_audit_enums.py`. Neither new value is used by a row inside this
   migration (the PG16 in-txn rule).
3. **R38 seed: `drift.read`** — `pg_insert(permission).on_conflict_do_nothing(["key"])` with
   `(key='drift.read', resource='drift', action='read', is_system_domain=true, sod_sensitive=false,
   sig_hook=false, finest_scope='SYSTEM')`; one `role_grant` to **System Administrator**
   (`scope_template={"level":"SYSTEM"}`, `on_conflict_do_nothing`). ⚠ Org lookup = the resilient
   pattern (`scalar_one_or_none` on `short_code='DEFAULT'` + a SELECT-the-only-org fallback — the
   #107 lesson; 0028 predates it and silently skips a renamed-org install). Multi-org guard: if the
   fallback finds ≠1 org, skip the grant (log), never abort the upgrade.
4. **Downgrade:** delete the `role_grant` rows for `drift.read` BEFORE the `permission` row (the
   RESTRICT FK); enum ADD VALUEs are irreversible → no-op (0001 drops the types wholesale, so the
   round-trip passes). `alembic check` must round-trip clean (`/check-migrations`).
5. Tests: catalog-count assertion bump in `test_authz.py`.

No change to `blob` (the `verified_at` column exists since S0; the app role holds UPDATE on `blob` —
no REVOKE applies — verified before design).

## 3. The D1 scanner — `services/vault/blob_verify.py` (new module)

`verify_blobs(session, *, sample_size=None, full=False, hasher=None) -> BlobVerifyReport`. Mirrors
`mirror_scan`'s posture: collect in memory (DB-read-only while hashing), persist once at terminal,
never raise.

1. **Sample selection** — column-select (never entities — identity-map hygiene):
   `SELECT sha256, bucket, object_key, size_bytes FROM blob ORDER BY verified_at ASC NULLS FIRST,
   sha256 LIMIT :k` (`full=True` drops the LIMIT; the `sha256` tiebreak makes the order
   deterministic). Zero rows (fresh install) → CLEAN, zero events.
2. **Per blob, stream-hash** via a new `storage.hash_object(object_key, *, bucket) -> str`: sync
   boto3 `get_object` Body read in `_STREAM_CHUNK` (1 MiB) chunks feeding `hashlib.sha256`, run
   off-thread (the `stream_object` pattern) — never whole-buffer (`fetch_bytes` would materialise a
   multi-GB blob), never presign (the api-tier path; D1 is a worker read). `hasher` is injectable
   for unit tests.
3. **Classify:**

| Outcome | Classification | Event | `verified_at` |
|---|---|---|---|
| Digest == `sha256` PK | OK | — | **stamped** |
| Digest ≠ PK | `HASH_MISMATCH` | `BLOB_INTEGRITY_FAILED` | not stamped |
| `NoSuchKey`/404 | `OBJECT_MISSING` (tamper OR a broken blob-row-iff-bytes invariant — never silently skippable) | `BLOB_INTEGRITY_FAILED` | not stamped |
| Other object-scoped `ClientError` (e.g. AccessDenied) | `READ_ERROR` (+ error note) | `BLOB_INTEGRITY_FAILED` | not stamped |
| Connection-class failure (`EndpointConnectionError`, timeouts, …) | **scan-infrastructure failure** → abort: `FAILED` report, salvage findings so far | — (no noise findings for unreached blobs) | not stamped |

4. **Stamp-on-OK-only is load-bearing.** A finding leaves the blob at the rotation head, so every
   subsequent scan re-detects and re-alarms until the operator restores the object: unlike the
   mirror there is **no auto-correction**, so the alarm must persist — and it keeps the
   latest-per-kind status surface honestly `DIVERGENT` (stamping a bad blob would let the next
   day's clean sample mask an unresolved corruption as CLEAN). Rotation starvation is bounded: bad
   blobs occupy their count of sample slots; if bad ≥ sample_size the entire vault is on fire and
   every scan covering the bad set is correct behavior. A *transient* READ_ERROR self-clears on the
   next run (unstamped → still at the head → re-verified).
5. Returns `BlobVerifyReport(scan_id, started_at, status CLEAN|DIVERGENT|FAILED, findings,
   ok_shas, counts)` — counts `{scanned, ok, mismatched, missing, read_errors, stamped, full,
   sample_size, total_blobs}`.

## 4. Persistence — `persist_blob_verify(session, report, *, triggered_by) -> bool`

ONE transaction (the `persist_scan_results` shape): per-finding `AuditEvent`
(`event_type=BLOB_INTEGRITY_FAILED`, `actor_type=system`, `actor_id=NULL`, `object_type=config`
keyed on the org via `get_single_org_id`,
`after={sha256, bucket, object_key, classification, found_sha256, size_bytes, note?, scan_id}`)
→ `UPDATE blob SET verified_at=now() WHERE sha256 IN (:ok_shas)` → the `drift_scan` row
(`kind=BLOB_REHASH`, `status`, `counts` + `triggered_by='beat'|'cli'`). Commit; on any failure →
rollback, log, return False. **A persist failure stamps nothing**, so the next run redoes the same
sample — self-healing, no idempotency ledger (the S-drift-2 posture; there is no rebuild to defer
here, so the bool is informational + logged). NO per-clean-scan audit event (the hourly-CLEAN-spam
rule); EVERY scan gets its `drift_scan` row (the row-per-scan contract). A `FAILED` report still
persists (rollback-first — the failed scan may have poisoned the txn — then write; the
`persist_scan_results` FAILED branch precedent).

## 5. Task, CLI, locking, knobs

| Entry point | Behavior |
|---|---|
| **NEW** `easysynq.blob.verify` task (daily Beat) | rolling sample (`settings.blob_verify_sample_size`) → persist. |
| **NEW** CLI `easysynq blob verify [--full] [--sample-size N]` | same pipeline, `triggered_by='cli'`; prints the summary; `--full` = the on-demand complete pass (doc 03 §8.2's "full set"). |

- **Single-flight = NEW `LOCK_BLOB_VERIFY = 7710007`** in `pg_locks.py` — independent of
  `LOCK_MIRROR_SYNC` (blob verify never touches the mirror; serializing them would couple unrelated
  cadences). Skip-if-held, the established posture. The S-drift-2 §11a lock learning applies
  verbatim: NO in-session `holds_advisory_lock` recheck (a Session releases its connection on
  commit — the recheck runs on a recycled backend and false-skips); the task-entry
  `pg_try_advisory_lock` on the task's own engine is the whole story.
- Worker shape = the `tasks/mirror.py` precedent: new module `tasks/blob_verify.py`, own
  `create_async_engine` + `dispose()` in `finally`, `asyncio.run`, **never raises** (the backup
  posture). Registered in `tasks/__init__.py` + the `app.tasks` membership unit test.
- Beat entry in `tasks/app.py` scheduled from **`settings.blob_verify_interval_seconds` (int,
  default 86400)**; sample size from **`settings.blob_verify_sample_size` (int, default 500)**.
- New CLI module `cli/blob.py` registered in `cli/__init__.py`. ⚠ New CLI module + Beat entry are
  **not in the running container until rebuilt** (`up -d --build migrate api worker beat`) — the
  live-smoke prerequisite.

## 6. The D4 report — `services/vault/drift_report.py` (new module, read-only)

`superseded_copies(session, *, limit=50, offset=0) -> dict`: aggregate over
`audit_event WHERE event_type IN ('EXPORTED','PRINTED') AND object_type='version'` joined
`document_version ON dv.id = ae.object_id` filtered `dv.version_state IN ('Superseded','Obsolete')`,
joined `documented_information` for the identifier + the document's CURRENT effective revision label
(via `current_effective_version_id`, NULL-safe — an obsoleted document has none). Per-version rows
`{document_id, identifier, revision_label, version_state, exported, printed, last_copy_at,
current_revision_label}` ordered `last_copy_at DESC` (deterministic tiebreak `version_id`), plus
headline totals `{versions, copies}` computed over the FULL filtered set (not the page).
`ix_audit_event_event_type` carries the partitioned-table read; EXPORTED/PRINTED are rare events.
Semantics note: a version that was never Effective has no such events (only the Effective version is
servable), and copies of the *currently* Effective version are deliberately excluded — they are
controlled, not outstanding. There is no decrement leg (a paper copy can't be un-printed); the count
is the honest upper bound R11 asks for, and the S7c verify token is the per-copy resolution.

`drift_status(session) -> dict`: latest `drift_scan` per kind —
`SELECT DISTINCT ON (kind) … ORDER BY kind, started_at DESC` (riding `ix_drift_scan_kind_started_at`,
the read this index was built for) — each row projected `{kind, status, started_at, finished_at,
counts, triggered_by}`; a never-run kind is `null` (a fresh install before the first Beat tick).
Plus `blob_coverage = {total, never_verified, oldest_verified_at}` (one aggregate query) and the D4
headline `{versions, copies}`.

## 7. The admin surface — `api/drift.py` (new router)

Both gated **`require("drift.read")`** (the `api/config.py` SYSTEM-key shape; deny-by-default —
as-built, the SYSTEM-domain key IS the admin gate, the `config.update` precedent; doc 15 §8.17's
`is_system_admin`+MFA posture is realized through SYSTEM-domain key holdings in v1):

| Method | Path | Returns |
|---|---|---|
| GET | `/admin/drift/status` | `{scans: {MIRROR: {...}\|null, BLOB_REHASH: {...}\|null}, blob_coverage, superseded_copies: {versions, copies}}` |
| GET | `/admin/drift/superseded-copies?limit=&offset=` | `{total: {versions, copies}, items: [...]}` |

All-static paths (no `/{id}` shadow concern). Router mounted with the other admin routers.
**`openapi.yaml` documents both in-PR** (`/check-contracts`). No SSE/async — both are cheap reads.

## 8. Error handling

- The scan body is wrapped: any unexpected exception → `FAILED` report salvaging findings collected
  so far; the Beat task and CLI **never raise** (the backup/mirror posture). Per-object errors are
  findings (§3); connection-class errors are infrastructure failures (§3, last row) — MinIO-down
  must not mint hundreds of noise findings or audit events.
- `persist_blob_verify` failure: logged, nothing stamped, next run self-heals (§4).
- The D4/status endpoints are plain reads — no lock, no scan trigger (pure GET, no side effect).

## 9. Non-goals (this slice)

- **No web UI** — S-web-8 consumes `drift.read` + these two GETs.
- **No auto-correction of a failed blob** — restore-from-backup is the operator action (runbook);
  the persistent re-alarm (§3.4) is the deliberate substitute.
- **No alerting/notification engine** — the alarm = the `BLOB_INTEGRITY_FAILED` audit event +
  structured log (the MIRROR_TAMPER posture; doc 12 §3 wiring is ops-level).
- **No new audit_object_type** (`config` keyed on the org carries the blob events; fork note §0).
- **No D5 aggregation on the status surface** — review-overdue lives on the compliance checklist
  (S-drift-1) and the future PDCA dashboard.
- **No persisted D4 scan** — it is a live read (fork note §0).
- **No `verified_at` backfill** in 0047 — NULL means "never verified", which is the truthful initial
  state and exactly what `NULLS FIRST` consumes.
- **No pack-download counting in D4** — `PACK_DOWNLOADED` is a sealed-pack delivery (its own audited
  channel, time-boxed + revocable), not a controlled-rendition copy; doc 05 scopes D4 to the §6.4
  EXPORTED/PRINTED trail.

## 10. Testing & verification

- **Unit** (`hasher` injected; no MinIO): the §3 classification matrix (one test per row, incl.
  NoSuchKey→OBJECT_MISSING and connection-error→FAILED-with-salvage); stamp-on-OK-only (a finding's
  blob is NOT in `ok_shas`); rotation order (`NULLS FIRST` then oldest, deterministic tiebreak);
  `full` ignores the limit; zero-blobs→CLEAN; report counts; `persist_blob_verify` one-txn shape +
  failure→False+nothing-stamped; the task in `app.tasks` + the Beat entry + both settings knobs +
  `LOCK_BLOB_VERIFY` distinctness; the two new enum members; D4 SQL fragments where unit-testable
  (the row-shape projection). Scan-never-raises (hasher that explodes → FAILED report, task returns).
- **Integration (Linux CI; this box runs api static checks only):** synthetic tamper via planted
  blob **rows** (never fight WORM): a row whose `sha256` ≠ the real bytes it points at →
  HASH_MISMATCH; a row pointing at a nonexistent key → OBJECT_MISSING; assert the
  `BLOB_INTEGRITY_FAILED` events (payload incl. classification + sha256), the `drift_scan
  kind=BLOB_REHASH` row, unstamped `verified_at` on the bad rows, stamped on the clean ones, and
  re-alarm on a second run (the persistent-alarm contract). Clean-pass test: all stamped, no events,
  CLEAN row. D4 end-to-end: create→release→`render_dynamic_copy(export+print)`→revise+release (the
  helper signs as the APPROVER, never the author — SoD-2)→assert the superseded version appears with
  the right counts + the effective version does NOT. Endpoint tests: 403 without `drift.read`, 200
  with (the seeded System Administrator grant), status shows both kinds after a mirror scan + a blob
  verify. ⚠ **Self-provide every precondition; run-scoped/delta assertions only** (the shared
  session DB + data-driven shard composition — never assume clean OR dirty). If the new tests are
  heavy: re-run `bash scripts/refresh-test-durations.sh <green-run-id>` against the PR's own first
  green run and commit the diff in-PR (the #109 contract).
- **Local gates (this box):** `/check-api` static legs + `/check-migrations` + `/check-contracts`
  (no `/check-web` — web untouched).
- **Pre-PR:** diff-critic on the branch diff. **Pre-merge live smoke** (rebuild
  migrate/api/worker/beat first): plant a bad blob row via the worker heredoc, run
  `easysynq.blob.verify`, verify the audit row + the `drift_scan` row + `verified_at` movement; GET
  both endpoints as `demo` (holds `drift.read` natively post-0047); confirm the re-alarm, restore
  (delete the planted row), confirm CLEAN.

## 11. Docs in-PR

`docs/05` §9.1 (mark D1 + D4 shipped — the family complete) · `docs/03` §8.2 (pointer to the
implementation) · `docs/07` §3.9 (the `drift.read` catalog row) · `docs/14` (`blob.verified_at`
semantics note + the `drift_scan` kind value) · `docs/15` (the two admin GETs) ·
`docs/decisions-register.md` **R41** (`drift.read` — the R38 additive procedure) · a runbook note
(BLOB_INTEGRITY_FAILED response: quarantine nothing — blobs are WORM; verify the backup chain,
restore the object to a fresh key/bucket per R37, re-run `easysynq blob verify` to clear; the
superseded-copies report's recall-list usage) · `docs/slice-history.md` entry · CLAUDE.md
Current-status pointer (head `0047`, next `0048`).
