# Issue #360 — Pending Purge Authority Implementation Plan

**Goal:** Prevent a forged `pending_blob_purge` row from directing the reaper to erase a live
object or invoke governance bypass, while preserving ordinary and R27 crash recovery.

**Approved architecture:** Authority-bound Option B from
`docs/superpowers/specs/2026-07-29-issue-360-purge-authority-design.md`.

## Constraints

- Migration head is `0080`; this slice adds `0081`.
- No new permission key, endpoint, response schema, or OpenAPI change.
- Preserve purge-after-commit and the `blob`-row-iff-bytes invariant.
- Preserve two-person R27 authorization and ordinary retention-sweep behavior.
- Existing rows must upgrade safely without being promoted to bypass authority.
- Integration assertions are run-scoped and must exercise the non-owner application role.

## Task 1 — migration and ORM authority fields

- [x] Add nullable Record, disposition-event, and WORM-request FKs plus `authority_bound` to
      `PendingBlobPurge`.
- [x] Add migration `0081_pending_purge_authority.py`.
- [x] Mark pre-existing rows legacy; make new-row legacy selection server-controlled.
- [x] Replace broad INSERT/UPDATE grants with column-scoped privileges.
- [x] Add the authority-shape CHECK and matching ORM constraints.
- [x] Verify `uv run alembic heads` and `alembic check`.

## Task 2 — bind lawful disposition transactions

- [x] Make `_write_tombstone` return an explicitly identified immutable event.
- [x] Write the tombstone before marking DESTROY evidence inside the same transaction.
- [x] Thread the event and optional executed R27 request into every marker, including the
      structured-PDF rendition.
- [x] Keep `_PurgeSpec` and immediate post-commit purge behavior unchanged except for the
      exact-object ownership check.

## Task 3 — reaper authorization

- [x] Replace SHA-dependent ownership with `(bucket, object_key)` ownership.
- [x] Add a repository helper that loads the bound Record/event/request tuple so the service derives
      whether governance bypass is authorized.
- [x] Refuse and delete invalid bound markers without calling S3.
- [x] Force legacy markers through non-bypass recovery.
- [x] Preserve per-marker commit, failure rotation, and idempotency.

## Task 4 — mutation-distinguishing tests

- [x] Update existing crash/re-capture/cross-bucket tests for bound markers.
- [x] Add forged bare-marker and mismatched-authority refusals.
- [x] Add wrong-SHA/live-object protection.
- [x] Add R27 crash-recovery bypass proof.
- [x] Add legacy non-bypass proof.
- [x] Add PostgreSQL privilege proof: sensitive UPDATE denied, row-lock claim allowed.

## Task 5 — authoritative docs

- [x] Add the approved R27 authority-bound addendum to the decisions register.
- [x] Update the records/retention and data-model descriptions.
- [x] Add an Issue #360 shipped entry to slice history.

## Task 6 — gates and publication

- [x] Focused unit/integration tests.
- [x] Ruff lint + format and mypy.
- [x] Migration up/down/up + populated legacy-row smoke + `alembic check`.
- [x] Full API unit suite and relevant integration file/shard-safe order.
- [x] Contract lint (unchanged contract proof) and `git diff --check`.
- [x] Adversarial branch-diff review; fold verified findings.
- [ ] Commit, push, open a ready PR that closes Issue #360, and monitor all CI checks.
