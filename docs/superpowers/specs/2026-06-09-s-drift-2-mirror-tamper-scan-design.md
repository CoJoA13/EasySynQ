# S-drift-2 — Mirror tamper / staleness scan (D2+D3) (slice design)

> **Status:** approved (owner, 2026-06-09). **Track:** v1.x drift family (backend-first), slice 2 of 3
> (the family decomposition is the S-drift-1 spec §0 fork: S-drift-1 D5 ✅ → **S-drift-2 D2+D3 (this
> spec)** → S-drift-3 D1 + D4 + the thin admin drift-status surface → trailing S-web-8).
> **This is the family's THESIS slice:** D2 today — "authority flows vault → mirror" — is *asserted*,
> not *verified*; the scan makes it an enforced, audited invariant. **Spec sources:** doc 05 §9.1 rows
> D2/D3, §9.2 (the scan flow), §9.2.1 + decisions-register **R11** (quarantine-before-overwrite,
> cadence default hourly = the accepted drift window, the mount/permission contract, detection covers
> ONLY files within the mirror); doc 12 §3 integrity-alert row. **Migration:** `0046` (head today:
> `0045`). **Closes:** the explicit D-6 seam in `services/vault/mirror.py` ("there is no
> comparison/scan code yet"). **No new permission key (R38 untouched), no new/changed endpoint
> (openapi.yaml untouched)** — the admin read surface is S-drift-3's.

## 0. Owner forks (resolved 2026-06-09, via AskUserQuestion)

1. **Composition = one pipeline, scan-first.** Every `sync_mirror` execution scans the *outgoing*
   `current/` tree BEFORE rebuild+swap (quarantine + audit, then the rebuild IS the vault-wins
   correction), plus a NEW hourly Beat scan task running the same pipeline that rebuilds only when
   divergence / behind-vault / no-baseline is found. Both serialize under the existing
   `LOCK_MIRROR_SYNC`. Rationale: `_prune_builds` rmtree's the old build after every swap, so an
   *unscanned* rebuild silently destroys tamper evidence — scan-first is what makes R11's
   quarantine-before-overwrite real; it also satisfies R11's "each mirror-sync" detection leg verbatim.
2. **Event names = `MIRROR_STALE` + `MIRROR_TAMPER`** (doc 05 §9.2-faithful; two additive
   `event_type` values). The classification IS the event type — alert wiring can key on
   `MIRROR_TAMPER` alone. `mirror.py`'s docstring `MIRROR_DRIFT_DETECTED` was a pre-spec code note —
   fixed in-slice.
3. **Persistence = `mirror_build` baseline + family-generic `drift_scan` summary table.**
   `mirror_build` (the PG-persisted build manifest) is the scan's expected-state authority — needed
   for soundness regardless of fork. `drift_scan` (kind=`MIRROR` now) is doc 05 §9.2's "write scan
   summary" PG write; S-drift-3's D1 blob re-hash reuses it (additive kind `BLOB_REHASH`) and the
   S-drift-3 admin drift-status surface reads latest-per-kind.
4. **Quarantine = `<mirror_path>/.quarantine/`, keep forever.** A dotted sibling of `.builds`: same
   volume (no cross-device copy), inherits the R11 mount contract (worker-writable, read-only to
   users), invisible in `current/`, untouched by `_prune_builds`. Never auto-deleted in v1 — it is
   forensic evidence; the runbook documents operator cleanup. The audit rows carry path + both
   digests, so digest-level evidence survives any cleanup.

## 1. Why / what

**D2 (doc 05 §9.1):** *"Mirror-sync worker re-hashes each mirrored file and compares to the expected
digest of the Effective rendition; any mismatch, extra file, or missing file is flagged, the divergent
bytes are QUARANTINED before any overwrite, the anomaly is logged to the audit trail, and only then is
the mirror rewritten from the vault (vault wins)."* **D3:** per-path expected
`{document_id, version_id, digest}`; `STALE_REVISION` if the found digest matches an older version,
`UNEXPECTED_CONTENT` if it matches nothing. Serves metric **M2** ("drift detected and flagged within
one mirror-sync cycle").

**What already exists (bind, don't rebuild):** `sync_mirror` = whole-tree rebuild into
`.builds/<hex>` + atomic `current` symlink swap, serialized under `LOCK_MIRROR_SYNC`;
`_meta/manifest.json` with per-file `{path, sha256, size_bytes}` + `{path, symlink_to}` symlink
entries, deterministically sorted; the additive-enum migration pattern; Beat entries in `tasks/app.py`.

**Two load-bearing facts that shape the design:**

- **`_prune_builds` destroys forensic evidence.** After every swap the old (possibly tampered) build
  tree is deleted. Quarantine-before-overwrite therefore concretely means: scan the *outgoing* tree
  and copy divergent bytes out **before** swap+prune (owner fork §0.1).
- **The on-disk manifest cannot be the scan's authority** (the mirror is never trusted as truth), and
  a dry-run recompute of expected bytes is fragile (`metadata.json` embeds `render_status`, which can
  change between builds; `manifest.json` embeds a deliberately non-deterministic `generated_at`). The
  sound expected state is the **build manifest persisted into PG at build time**, keyed by the
  `.builds/<name>` directory name; the scan resolves `current` to its concrete target and loads
  *that* build's row. The on-disk `manifest.json` is verified as just-another-file via a build-time
  byte digest (`manifest_sha256`), never read as authority.

**Mirrored content note:** the per-doc content file is the **controlled-copy rendition PDF** when
renderable (source bytes when pending/non-renderable, R26) — so `STALE_REVISION` checks the divergent
digest against the same document's other versions' `source_sha256` **and** `rendition_blob_sha256`.

## 2. Schema — migration `0046`

Two new tables (new model modules `mirror_build.py` + `drift_scan.py`, **imported in
`db/models/__init__.py` + `__all__`** — the 0027 lesson) + two additive enum values. No seed rows →
no org lookup in the migration (the 0038/0043 trap is avoided entirely at migrate time; the *runtime*
org lookup in §6 uses the resilient pattern).

**`mirror_build`** — the vault-side expected-state baseline, one row per build:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `org_id` | UUID FK→organization RESTRICT, NOT NULL | the `visual_diff` convention |
| `build_name` | TEXT UNIQUE NOT NULL | the `.builds/<hex>` dir name — the scan's lookup key |
| `built_at` | TIMESTAMPTZ NOT NULL default now() | |
| `manifest` | JSONB NOT NULL | the manifest `files` list (entries enriched per §3) |
| `manifest_sha256` | TEXT NOT NULL | sha256 of the exact bytes written to `_meta/manifest.json` |
| `documents` / `files` / `symlinks` | INTEGER NOT NULL | build counts |

Inserted inside `sync_mirror`'s build transaction (same commit as the rendition-cache writes, before
the swap). Commit-then-swap ordering means a failed swap leaves an orphan row — **harmless**, because
the scan looks up by `current`'s *actual* target. **Keep-last-20 prune** in the same transaction
(delete oldest rows beyond 20). A regenerable registry, NOT an audit record — plain mutable table, no
append-only REVOKE (the `visual_diff` posture).

**`drift_scan`** — one summary row per scan (doc 05 §9.2's "write scan summary"):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `org_id` | UUID FK→organization RESTRICT, NOT NULL | |
| `kind` | native enum `drift_scan_kind` = `('MIRROR')` | S-drift-3 adds `BLOB_REHASH` via additive `ALTER TYPE` |
| `started_at` / `finished_at` | TIMESTAMPTZ | started NOT NULL; finished set at terminal |
| `status` | native enum `drift_scan_status` = `('CLEAN','DIVERGENT','FAILED')` | |
| `counts` | JSONB NOT NULL | `{scanned, ok, stale, tampered, extra, missing, symlink_divergent, quarantined, errors, build_name, is_current, pointer, scan_id, baseline, rebuild_triggered}` (as built: `scan_mirror` is DB-read-only; `persist_scan_results` writes the events + this summary row in one txn, stamping `rebuild_triggered` — the rebuild decision at persist time) |
| `triggered_by` | TEXT NOT NULL | `'beat'` \| `'sync'` \| `'cli'` |

Index `(kind, started_at DESC)` — the S-drift-3 latest-per-kind read. Written once at scan terminal
(write-once by code; the tamper-evident record is the audit trail, this is the operational summary).

**`event_type`:** `ALTER TYPE … ADD VALUE 'MIRROR_STALE', 'MIRROR_TAMPER'` (additive, no-op
downgrade — the 0011 pattern, tuples sourced from the ORM `*_VALUES`; matching Python members).

`alembic check` must round-trip clean (`/check-migrations`); downgrade drops the two tables + their
enums (the event_type values stay — additive pattern).

## 3. The expected-state baseline (manifest persistence + enrichment)

- `build_tree`'s manifest **file entries gain additive optional keys** `document_id` + `version_id`
  for doc-owned files (the content file, `metadata.json`, `CHANGELOG.md`). Schema marker stays
  `easysynq.mirror.manifest/1` (additive keys, no breaking change). `INDEX.md`, `_meta/*`, and
  `_ImportReport/*` entries carry none (not doc-attributable).
- `sync_mirror` computes `manifest_sha256` over the exact `manifest.json` bytes it writes and inserts
  the `mirror_build` row (§2) in the build txn.
- **Upgrade path:** a `current` pointing at a pre-0046 build has no `mirror_build` row → the scan
  reports `NO_BASELINE` (zero anomalies, zero false alarms) and the caller rebuilds, which
  establishes the baseline. Same posture for a missing/dangling `current` (fresh install).

## 4. The scanner — `services/vault/mirror_scan.py` (new module)

`scan_mirror(session, *, mirror_path=None) -> ScanReport`. `mirror.py` itself only gains the
`mirror_build` insert + the docstring fix (§0.2); the welded build path stays otherwise untouched.

1. **Resolve** `current` via `os.readlink` → `build_name`; load the `mirror_build` row. Missing
   either → `NO_BASELINE` (§3).
2. **Walk** the concrete build dir with `os.walk(followlinks=False)` collecting relative paths of
   regular files and symlinks. ⚠ Never `rglob` (Py3.12 follows symlinks — engineering-patterns). The
   walk covers the resolved build dir ONLY — never `mirror_path` itself — so `.quarantine/` and
   sibling builds are structurally out of scope.
3. **Compare + classify** each path against the PG manifest:

| Finding | Classification | Event |
|---|---|---|
| Re-hash == manifest `sha256` | OK | — |
| Digest mismatch, found digest matches known vault bytes of the **same document** (any version's `source_sha256` or `rendition_blob_sha256`) | `STALE_REVISION` | `MIRROR_STALE` |
| Digest mismatch, matches nothing (incl. all generated files: `metadata.json`, `CHANGELOG.md`, `INDEX.md`, `_ImportReport/*`) | `UNEXPECTED_CONTENT` | `MIRROR_TAMPER` |
| On-disk path not in manifest (`_meta/manifest.json` itself is expected — verified against `manifest_sha256`; mismatch → `UNEXPECTED_CONTENT`) | `EXTRA` | `MIRROR_TAMPER` |
| Manifest entry absent on disk (nothing to quarantine — audit only) | `MISSING` | `MIRROR_TAMPER` |
| Symlink target ≠ `symlink_to`, or type swapped (file↔symlink) | `SYMLINK_DIVERGENT` | `MIRROR_TAMPER` |
| Per-file read error (permission tamper) | `UNEXPECTED_CONTENT` + error note | `MIRROR_TAMPER` |

4. **Currency check (the D3 staleness backstop):** manifest `version_id` set vs the live Effective
   set → `is_current`. Behind-vault is NOT tamper (normal lag / a lost post-release enqueue): no
   audit event; it just makes the hourly task rebuild.
5. Returns `ScanReport(anomalies, counts, is_current, baseline_state)`.

## 5. Quarantine (R11: BEFORE any overwrite)

- Layout: `<mirror_path>/.quarantine/<UTC yyyymmddTHHMMSSZ>__<scan-uuid>/<relative-path>` (tree
  structure preserved) + a `quarantine.json` index (paths, expected/found digests, classifications,
  build_name, scan id).
- Copied: divergent-content files and EXTRA files. MISSING → nothing to copy (audit only).
  `SYMLINK_DIVERGENT` → the actual target string is recorded in `quarantine.json` + the audit
  payload (no byte copy — the target may point anywhere; never follow it).
- A quarantine copy failure → log + continue (the audit row still carries both digests); it must
  never block correction.
- Retention: **never auto-deleted** (owner fork §0.4); operator cleanup documented in the runbook.

## 6. Audit events + scan summary

- **Per anomaly**, one `AuditEvent`: `event_type` per §4's table, `actor_type=system`,
  `actor_id=NULL`. Doc-attributable paths (manifest `document_id` present): `object_type=document`,
  `object_id=document_id`, **`scope_ref=identifier`** so `GET /documents/{id}/audit-events` surfaces
  the doc's own tampering (the S-ing-5 precedent). Non-attributable paths: `object_type=config`
  keyed on the org — org resolved via the **resilient single-org lookup** (`scalar_one_or_none` on
  the default short_code + SELECT-the-only-org fallback; the 0038/0043 lesson — this install is
  `AHT`). `after` payload:
  `{path, classification, expected_sha256, found_sha256, quarantine_path, build_name, scan_id}`.
- **Audit-noise posture:** NO per-clean-scan audit event (hourly CLEAN events would spam the trail);
  the `drift_scan` row is the operational trace. Anomalies are ALWAYS audited.
- **Ordering / crash posture:** quarantine files are durably written FIRST, then the audit events +
  the `drift_scan` row commit together (one txn). A crash between leaves quarantined bytes with no
  events — and since no rebuild happened, the divergence is still on disk: the next scan re-detects
  and re-audits (a duplicate quarantine dir under a new scan id is benign). Self-healing, no lost
  evidence, no idempotency ledger needed.

## 7. Composition, cadence, locking

A `scan_and_sync(session, *, rebuild, triggered_by, mirror_path=None, render_sink=None)`
orchestrator in `mirror_scan.py`; callers hold `LOCK_MIRROR_SYNC` (non-blocking try → skip tick —
the existing posture; the same lock serializes scan↔sync so a swap can never prune a tree mid-walk):

| Entry point | Behavior |
|---|---|
| `easysynq.mirror.sync` task (nightly Beat + the release/obsolete enqueue) | scan → quarantine/audit/persist → **always** rebuild+swap (R11's per-sync leg). |
| **NEW** `easysynq.mirror.scan` task (hourly Beat) | scan → rebuild **only if** DIVERGENT ∨ ¬is_current ∨ NO_BASELINE. A CLEAN+current scan does no tree churn. |
| CLI `easysynq mirror sync` | same as the sync task (scans first now). |
| **NEW** CLI `easysynq mirror scan` | scan-only; prints the summary (`triggered_by='cli'`). |

- Beat entry schedule from a new **`settings.mirror_scan_interval_seconds` (int, default 3600)** —
  R11's "default hourly, configurable". Task module registered in `tasks/__init__.py` + the
  `app.tasks` membership unit test (engineering-patterns).
- The worker task shape = the `mirror_sync` precedent (own disposed async engine, `asyncio.run`,
  real `GotenbergRenderSink` only when rebuilding).
- ⚠ New CLI module + Beat entry are **not in the running container until rebuilt** (`up -d --build
  migrate api worker beat`) — the live-smoke prerequisite.

## 8. Error handling

- **Per-file errors are findings, not failures** (§4 row 7).
- **Scan infrastructure failure** (can't read the build dir, PG error mid-scan): catch → `drift_scan`
  `FAILED` row (best-effort) + log; the Beat task **never raises** (the backup posture). The **sync
  path still rebuilds** (a broken scan must never block vault-wins correction); the **hourly path
  does NOT rebuild on FAILED** (a scan failure ≠ evidence the mirror is wrong; the nightly sync
  remains the convergence backstop, and a persistent FAILED row stream is the operator signal).
- `NO_BASELINE` is not an error (status `CLEAN`, counts carry `baseline: "none"`, rebuild follows).

## 9. Non-goals (this slice)

- **No admin drift-status endpoint/UI** — `drift_scan` is written, not yet read (S-drift-3 / S-web-8).
- **No D1 blob re-hash, no D4 superseded-copies report** (S-drift-3).
- **No alerting/notification engine** — `MIRROR_TAMPER`'s "alarm" = the audit event + structured log
  (doc 12 §3 alert wiring is ops-level config, out of scope).
- **No new permission keys** (R38 untouched), **no contract change** (`openapi.yaml` untouched).
- **No mount/permission enforcement** — R11's contract is operator-verified at deploy; the scan
  detects what it cannot prevent (§9.2.1: detection covers ONLY files within the mirror).
- **No quarantine retention knob** (owner fork §0.4 — keep forever; a knob is additive later).
- **No multi-build history** beyond the keep-last-20 baseline prune.

## 10. Testing & verification

- **Unit:** the §4 classification matrix (one test per row, incl. manifest-tamper via
  `manifest_sha256` and same-doc-older-rendition → STALE); quarantine layout + `quarantine.json`;
  MISSING quarantines nothing; `NO_BASELINE` on a row-less build (the upgrade path — zero false
  alarms); the currency check; the walker never follows symlinks; read-error → TAMPER finding;
  infrastructure failure → FAILED row + task doesn't raise; `mirror_build` insert + keep-last-20
  prune; manifest `document_id`/`version_id` enrichment; the new task in `app.tasks` + the Beat
  entry + the settings knob; the two enum members. (Symlink-creating tests are Linux-CI-only on this
  box — the existing posture.)
- **Integration (Linux CI):** end-to-end — `sync_mirror`, then tamper the live tree four ways
  (older-version bytes → `MIRROR_STALE`; foreign bytes → `MIRROR_TAMPER`; extra file; deletion) →
  scan → assert quarantine contents, the per-anomaly audit events, the `drift_scan` row, and that
  `current/` re-hashes clean after correction; a re-scan is `CLEAN`. A two-session lock test (scan
  skip-ticks while sync holds `LOCK_MIRROR_SYNC`). ⚠ **Run-scoped/delta assertions only** (the
  shared session DB); ⚠ any release helper signs as the APPROVER, never the author (SoD-2 — the
  S-drift-1 CI lesson); ⚠ run the FULL integration suite for mirror/symlink work
  (engineering-patterns).
- **Local gates (this box):** api static checks (ruff/format/mypy-strict) + `/check-migrations` +
  `/check-contracts` (no-change check); both api test suites run in Linux CI.
- **Pre-PR:** diff-critic on the branch diff. **Pre-merge live smoke** (rebuild
  migrate/api/worker/beat images first): exec into the worker, tamper a mirrored file + plant an
  extra file, run the scan task, verify the `.quarantine/` contents + the `MIRROR_*` audit rows +
  the `drift_scan` row + the corrected tree; then a clean re-scan. (Backend smoke mechanics per the
  established heredoc pattern; no token needed.)

## 11. Amendments — the 4-lens fold (2026-06-09, post-plan adversarial pass; 1 CRITICAL / 8 MAJOR confirmed)

1. **Pointer integrity (CRITICAL).** The `current` symlink is itself verified, never trusted:
   `mirror_build` gains **`swapped_at`** (stamped in a small post-swap commit; a swap-then-crash
   window self-heals — the scan reports `pointer=selfheal` and `persist_scan_results` stamps it).
   `NO_BASELINE` is reserved for an **empty registry** (fresh install / pre-0046). With any
   registry rows: a missing/unreadable `current`, a real-directory `current`, a target with no row
   (`foreign`), or a target pointing at an **older swapped** build (`rollback`) is a
   **`POINTER_DIVERGENT` finding → `MIRROR_TAMPER`** + rebuild. A foreign/rogue tree is
   quarantined **by move** (same-volume rename — preserves the bytes exactly, unblocks the swap,
   and takes it out of `_prune_builds`' reach); a rollback tree is additionally scanned per-file
   against ITS OWN row's manifest.
2. **The build area is in scope.** Unregistered `.builds/` children are `EXTRA` → `MIRROR_TAMPER`
   + quarantine-by-move (the next sync's prune would otherwise destroy them unaudited).
   **Mirror-root siblings stay out of scope** (deliberate: correcting them would mean deleting
   operator files outside the published tree, and an uncorrectable finding would re-fire every
   scan — the mount contract owns the root).
3. **STALE excludes the expected version's own digests** — replacing the controlled-copy rendition
   with the SAME version's raw source bytes (no banding/QR) is `UNEXPECTED_CONTENT`/TAMPER, per
   doc 05's "matches an *older* version".
4. **Prune safety:** the keep-last-20 prune **never deletes the row `current` points at** (under a
   persistent swap-failure mode, orphan rows otherwise pile above it and detection silently
   disables).
5. **Persist/lock hardening:** `persist_scan_results` returns success; a persist failure **with
   findings** defers the rebuild (PG-down means the rebuild would fail anyway; the on-disk
   divergence is preserved for re-detection). After a FAILED scan or failed persist, the pipeline
   **re-verifies advisory-lock ownership** (`pg_locks.holds_advisory_lock`) before rebuilding — a
   mid-scan connection loss frees the session-level lock and a lockless rebuild could race a
   concurrent sync's prune.
6. **Smaller folds:** a *deleted* `_meta/manifest.json` is a `MISSING` finding (only the tampered
   case was caught); quarantine dirs are created `0o700` (users could otherwise browse tampered
   lookalikes forever); quarantined copies are **re-hashed** (`quarantined_sha256` in
   `quarantine.json`, chain of custody); the CLI `rebuild` force-clear is scoped
   `WHERE version_state = Effective` (a blanket null permanently destroys superseded-rendition
   digests → mis-classifies future rollbacks) and **committed before** the pipeline (a FAILED-scan
   rollback silently undid it); `persist_scan_results` fetches `identifier` by column-select (a
   `session.get` would leave a stale entity in the identity map for the rebuild's reads);
   type-swap findings carry no `note` (`note` is the error channel feeding `counts.errors`);
   findings imply rebuild on the hourly path so quarantine/audit never re-fires hourly for the
   same divergence.
7. **The `current` target SHAPE is verified, not just its basename** (Task-4 spec-review fold,
   2026-06-10): only the relative `.builds/<name>` form `atomic_swap` writes is parsed to a build
   name (`_parse_current_target`); an absolute / out-of-tree / nested / traversal target — even
   one whose basename collides with the registered build — classifies **foreign → MIRROR_TAMPER**
   with the raw target preserved as evidence, and is NEVER resolved against the filesystem (no
   walking or moving of out-of-tree paths). **Codex P2 folds (2026-06-10):** the shape parse uses
   the **native `PurePath` flavor** so a backslash separator is honored only on Windows — a
   `.builds\<name>` target on a Linux deployment is correctly foreign, not silently resolved.
   A `current` replaced by a **regular file** (not just a directory) with a non-empty registry is
   the `rogue` state → MIRROR_TAMPER + the planted bytes are **moved to quarantine before** the
   rebuild overwrites them. And `selfheal` (the swap-then-crash window) fires **only when `current`
   points at the NEWEST build overall** — an unswapped orphan that is merely newer than the newest
   *swapped* row is a rollback, not a crash window to silently stamp.
8. **Test hardening (false-PASS folds):** the lock test drives the real `_run_mirror_scan`
   skip-tick (not the bare primitive); the FAILED family (never-raise, always-rebuilds vs
   if_needed-skips, the FAILED row) is explicitly tested; a CLEAN scan's `drift_scan` row is
   asserted (the row-per-scan contract); the stale leg also covers an **older-rendition** digest;
   the pointer matrix is a pure unit-tested function (`resolve_pointer`).

## 12. Docs in-PR

`docs/05` §9.1/§9.2 (mark the D2/D3 seam closed; event names confirmed) · `docs/14` (the two tables)
· `docs/12` §3 pointer (the integrity-alert row now has a concrete emitter) · a runbook note
(quarantine location + operator cleanup + the mount-contract reminder) · `mirror.py` docstring fix ·
`docs/slice-history.md` entry · CLAUDE.md Current-status pointer (head `0046`, next `0047`).
