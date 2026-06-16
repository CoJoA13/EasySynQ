# Spec — Scheduled retained-backup verification (redesign of Phase-1 I-7 / PR #155)

> **Status:** approved design, NOT yet implemented. Implement on branch `chore/scheduled-restore-test`
> (PR [#155](https://github.com/CoJoA13/EasySynQ/pull/155), currently a **draft**). Owner approved the
> "verify retained archives" direction + the design calls below on 2026-06-16.

## Why (the gap Codex caught)

PR #155 shipped a weekly Beat job that calls `run_restore_test` → `drill.run_drill`, which builds a
**fresh** pg_dump, packs a transient archive, restores *that*, and runs the triad. It proves the backup
*mechanism* works and that the live DB round-trips — but it **never opens a retained durable archive**
(`build_durable_backup`'s `easysynq-backup-*.tar.enc`). So for any install with `BACKUP_ENCRYPTION_KEY`
set, or with a corrupt/undecryptable retained archive, the weekly run can stamp `last_restore_test_result
= PASS` while the backups an operator would actually restore from are unusable. That defeats the point of
I-7 ("catch silent backup rot"). (Codex P2 on #155, commit `9e11d45`; thread left **unresolved**.)

The P1 fix already merged in concept on this branch (the drill no longer leaves its transient plaintext
archive — `run_drill`'s `finally` unlinks the `.tar` + `.sha256`). **Keep that fix.** This redesign
changes *what the scheduled job verifies*.

## What ships

A new **`verify_latest_retained_backup(org_id, actor_id=None)`** that verifies the NEWEST *retained*
durable archive is restorable + intact, reusing the existing decrypt/manifest/restore/triad primitives.
The Beat job / Celery task / `restore_test_interval_seconds` config / `run_scheduled_restore_tests`
fan-out from #155 all **stay** — only the per-org call changes: `run_restore_test` → the new function.

### Algorithm (`services/backup/service.py`, new async fn; mirror `run_restore_test`'s shape)

1. Load the `backup_policy` for the org (→ `policy.destination`). No policy → `{"result": "FAIL",
   "reason": "no backup policy configured"}` (matches `run_restore_test`).
2. Serialize on **`LOCK_RESTORE_DRILL`** (a concurrent manual drill or verify SKIPs — they share the
   pg_restore/scratch resource family). `held == False` → `{"result": "SKIPPED", ...}`.
3. Off the event loop (`asyncio.to_thread`), call a new sync **`drill.verify_retained_archive(settings,
   destination=policy.destination)`** (see below) → `DrillResult`.
4. Persist `policy.last_restore_test_at` + `last_restore_test_result` and emit the
   `RESTORE_TEST_PASSED` / `RESTORE_TEST_FAILED` audit — EXACTLY like `run_restore_test`, but put a
   `source: "scheduled_retained_verify"` (+ the archive filename) into the audit `after` so an auditor
   can tell a scheduled retained-verify from the on-demand G-C drill. A `SKIPPED` result persists
   nothing and audits nothing (just logs).

### Sync verifier (`services/backup/drill.py`, new fn reusing existing primitives)

`verify_retained_archive(settings, *, destination, after_restore=None) -> DrillResult` — modelled on
`restore.run_restore` steps 1–5 (NOT the live-restore steps 6–8), but verify-only:

1. **Find newest archive:** glob `destination` for `easysynq-backup-*.tar` and `*.tar.enc` (exclude
   `.sha256`); pick the lexical-max name (the stamp `YYYYMMDDTHHMMSSZ-<uuid8>` sorts chronologically).
   **None found → `DrillResult("SKIPPED", "no retained backup archive to verify yet")`** (fresh install,
   nightly hasn't run — NOT a FAIL).
2. `archive.verify_archive(src)` (the `.sha256` sidecar) → FAIL on mismatch.
3. If `crypto.is_encrypted_archive(src)`: `crypto.decrypt_archive(src, tmp/"archive.tar",
   secret=settings.backup_encryption_key)` → a `BackupCryptoError` (wrong/missing key) becomes
   `DrillResult("FAIL", f"decrypt failed: …")`. Else use `src` directly (plaintext `.tar` fallback).
4. `archive.read_manifest(plain)` → `table_counts` (from `manifest["config"]["table_counts"]`) + the
   `blobs` list (the point-in-time blob set the archive was built against).
5. Restore into a **dedicated** scratch namespace prefixed **`verify_easysynq_`** (DISTINCT from the
   drill's `scratch_easysynq_` and the live restore's `restore_easysynq_`, so this never sweeps/clobbers
   an operator's standing verified target or a drill's scratch). Add a `_sweep_stale_verify` that drops
   only `verify_easysynq_*`. Reuse `drill._create_scratch_db`, `archive.unpack_dump`,
   `archive.restore_database`, `drill._copy_blobs` (copies the *manifested* blobs from the live vault
   into the non-WORM scratch bucket — a READ; the WORM vault is never written).
6. Run `drill.run_triad(settings, handle)` — restored counts vs the **archive's** `table_counts`, the
   `document_version→blob` FK check, and the blob SHA-256 re-hash. A manifested blob that's been disposed
   or corrupted since the backup → the re-hash leg FAILs (a genuine "this retained backup is no longer
   fully restorable" signal). `after_restore` is the TEST-ONLY fault injector (same contract as the
   drill).
7. **ALWAYS tear down** in `finally` (DB via `_drop_scratch_db`, bucket via `_delete_scratch_objects`) —
   it is a verify, never a standing cutover target. Never raises (honest FAIL).

Legacy archive with no `table_counts` (none currently ship without them, but `restore.run_restore`
guards it): skip row-count parity, still run FK + blob-rehash (mirror `run_restore`'s
`"skipped (legacy archive, no manifest counts)"` note).

### Reuse map (do NOT reimplement)

`archive.verify_archive` · `crypto.is_encrypted_archive` · `crypto.decrypt_archive` (`BackupCryptoError`)
· `archive.read_manifest` · `archive.unpack_dump` · `archive.restore_database` · `drill._create_scratch_db`
· `drill._copy_blobs` · `drill.run_triad` · `drill._drop_scratch_db` · `drill._delete_scratch_objects` ·
`ScratchHandle`. The new code is the newest-archive finder, the `verify_easysynq_` sweep, and the
orchestration `finally`.

## Design calls (owner-approved 2026-06-16)

- **Newest archive only** (the one you'd actually restore), not all retained archives.
- **No archive yet → SKIP**, not FAIL (a fresh install with no nightly run yet must not flap red).
- **Triad-only.** Skip the checkpoint-not-ahead + chain re-verify (`restore.run_restore` steps 6–8) —
  those are *live-restore tamper* guards whose FLAGGED-on-unreachable-off-host semantics would muddy a
  clean weekly PASS/FAIL. The integrity triad is the rot signal.
- **Reuse** `last_restore_test_result` + `RESTORE_TEST_*` (don't add a column), distinguished by a
  `source` tag in the audit `after`.
- **Keep** the #155 scaffolding (Beat entry, task, config setting, `run_scheduled_restore_tests`) — only
  swap its per-org call to `verify_latest_retained_backup`. **Keep** the drill's transient-archive
  cleanup (the P1 fix). Consider renaming the Beat entry/log lines from "restore-test" to
  "backup-verify" for clarity (optional; not required).

## Tests (`apps/api/tests/integration/test_backup.py`, CI-only on Windows)

1. **PASS over a real retained archive:** run `run_scheduled_backups()` (writes a durable archive to a
   temp dest) over a real Effective doc + blob, then `verify_latest_retained_backup(org)` → `PASS`;
   assert `last_restore_test_result == "PASS"` + a `RESTORE_TEST_PASSED` audit with
   `source == "scheduled_retained_verify"`.
2. **FAIL on a corrupted manifested blob:** after the durable backup, corrupt/delete the source blob in
   the vault bucket, then verify → `FAIL` (blob re-hash leg). (Mirror `test_drill_fails_on_corrupted_
   restored_blob` via the `after_restore` injector, or by mutating the vault object.)
3. **SKIP when no archive:** configure a fresh empty dest, verify → `SKIPPED`, nothing persisted.
4. **Encrypted round-trip:** with `BACKUP_ENCRYPTION_KEY` set, the durable archive is `.tar.enc`; verify
   decrypts + PASSes. (If the existing fixtures run keyless, at least assert the plaintext `.tar` path.)
5. **Teardown:** assert no `verify_easysynq_*` DB lingers and the scratch-bucket prefix is empty after
   (mirror `test_drill_tears_down_scratch_namespace`).

## Out of scope / notes

- Don't touch `restore.run_restore` (the operator cutover) or `run_restore_test` (the on-demand G-C
  drill) — both stay. This adds a third, verify-only path alongside them.
- `run_scheduled_restore_tests` keeps its best-effort per-org try/except + fresh-session-per-unit shape.
- Update `run_restore_test_task_registration` test name/comments if the Beat entry is renamed.
- Re-run `diff-critic` + push; Codex re-reviews; only merge on Codex 👍 (the standing rule).
