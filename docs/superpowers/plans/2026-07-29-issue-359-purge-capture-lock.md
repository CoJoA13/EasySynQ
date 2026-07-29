# Issue #359 — Capture/Purge Advisory Lock Implementation Plan

**Goal:** Make the live-owner check and physical purge atomic against byte-identical record capture
so a stale marker can never erase newly captured evidence.

**Architecture:** Physical `(bucket, object_key)` transaction advisory lock from
`docs/superpowers/specs/2026-07-29-issue-359-purge-capture-lock-design.md`.

## Constraints

- No migration, permission-key, endpoint, response-schema, or OpenAPI change.
- Preserve purge-after-commit, authority-bound recovery, and R27 dual control.
- Preserve exact-object ownership semantics from Issue #360.
- Resolve, de-duplicate, and sort the actual PostgreSQL advisory keys before multi-lock capture.
- Integration assertions must be run-scoped and deterministic.

## Task 1 — deterministic regression proof

- [x] Add one parameterized concurrency test covering immediate purge and reaper recovery.
- [x] Pause at the storage boundary after the no-owner check.
- [x] Prove the competing capture waits on a PostgreSQL advisory lock.
- [x] Assert bytes, the live `blob` row, and the new evidence link survive.
- [x] Mutation-verify that removing either side's lock makes the test fail.
- [x] Prove later reaper snapshots are re-claimed after the prior per-marker transaction ends.
- [x] Prove resolved hash collisions are de-duplicated and sorted by actual advisory key.

## Task 2 — shared physical-object lock

- [x] Add one records repository helper for the transaction advisory lock.
- [x] Key it by configured bucket plus object key, never marker SHA.
- [x] Document transaction lifetime and collision behavior.

## Task 3 — capture serialization

- [x] Normalize/de-duplicate evidence before DB/storage work.
- [x] Resolve and acquire all unique PostgreSQL advisory keys in numeric sorted order.
- [x] Hold them through WORM promotion, blob/evidence inserts, and enclosing commit.
- [x] Preserve `_commit=False` composition for correction and ingestion.

## Task 4 — purge serialization

- [x] Acquire the same lock before `_purge_marked` checks ownership.
- [x] Claim the immediate-purge marker first so it matches the reaper's row→object lock order.
- [x] Acquire it before the reaper checks ownership or validates authority.
- [x] Re-claim every later reaper snapshot after per-marker commit/rollback releases batch claims.
- [x] Hold it through S3 purge, marker deletion, and commit.
- [x] Roll back a failed reaper purge before continuing so the xact lock is released.

## Task 5 — authoritative docs

- [x] Amend `docs/06-records-and-evidence.md`.
- [x] Add the Issue #359 shipped entry to `docs/slice-history.md`.
- [x] Add the physical-object lock/order rule to `.claude/rules/engineering-patterns.md`.

## Task 6 — gates and publication

- [x] Focused concurrency and records-disposition integration tests.
- [x] Full touched integration file.
- [x] Ruff lint/format and mypy.
- [x] Full API unit suite and `git diff --check`.
- [x] Adversarial branch-diff review; fold verified findings.
- [x] Commit, push, and open ready PR #412 closing Issue #359.
- [ ] Monitor all PR checks and review threads through completion.
