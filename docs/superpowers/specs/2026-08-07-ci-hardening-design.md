# CI Hardening Design

**Date:** 2026-08-07
**Status:** Approved for implementation by the repository owner
**Scope:** CI orchestration, its local/documented mirrors, and integration timing data

## Context

The successful `main` run `31190679574` completed in 11m56s. The `web` job was the critical path at
11m52s, while `contract-responses` completed in 7m51s and the slowest integration shard completed in
4m51s. The web suite is intentionally restricted to one Vitest worker because parallel jsdom teardown
has previously produced nondeterministic `document is not defined` failures.

The objective is to reduce feedback time without reducing the selected test inventory or weakening a
failure gate. This change does not introduce path filtering, changed-file test selection, retries,
`fail-fast: true`, or higher in-process Vitest concurrency.

## Design

### Web test orchestration

Replace the single `web` job with:

1. `web-shards`, a two-entry matrix (`1/2`, `2/2`) with `fail-fast: false`. Each runner installs the
   frozen npm lock and executes `vitest run --shard=<index>/2`. The existing `maxWorkers: 1`, fork pool,
   and per-file isolation remain unchanged.
2. The second matrix entry also runs `npm run lint && npm run build` under `if: !cancelled()`. Static
   checks therefore still execute if that shard's tests fail, without paying for another checkout and
   frozen npm installation. `build` already performs `tsc --noEmit`, so the separate `typecheck`
   invocation is removed as duplicate work.
3. `web`, a stable zero-work aggregate job with `needs: web-shards` and `if: always()`. Its only step
   fails unless every matrix entry succeeded.

Running static checks in one already-hydrated shard avoids another npm installation. Assigning them to
the shard with 124 rather than 125 files is a deterministic initial choice; actual shard timings will
decide whether that assignment should later move.

### Python collection roots

Keep every existing marker while narrow-scoping collection:

- Unit: `pytest tests/unit -m unit`
- Integration matrix: `pytest tests/integration -m integration ...`
- Contract responses: `pytest tests/integration/test_contract_response_schemas.py -m contract ...`

`tests/conftest.py` remains authoritative for directory-to-marker classification. The explicit marker is
retained so a misplaced or incorrectly marked test cannot silently enter a gate.

### Contract authority

The `contracts` job will run `bash scripts/gen-contracts.sh --check` after Node setup. This makes the
committed `.contract.lock` checksum a CI gate instead of a developer convention. A deliberately modified
OpenAPI fixture must make the command fail before the real contract is restored.

### Integration timing refresh

Run the existing `scripts/refresh-test-durations.sh` against successful `main` run `31190679574`. Its four
artifacts must be non-overlapping, and their merged key set becomes the committed
`apps/api/.test_durations`. Refreshing timings changes shard allocation only; pytest selection remains
controlled by `-m integration`.

### Workflow contract regression

Add a dependency-free shell test that asserts the structural invariants above and invoke it from the
`contracts` job. Complement it with a parsed-YAML API unit test that binds exact matrix shape, active
commands, step order, hard-fail behavior, and job-level aggregate semantics. The shell test must fail
against the pre-change workflow, then pass after the workflow change. Together they guard against
future accidental removal or weakening of a shard, aggregate gate, direct collection root, isolation
setting, or contract-lock check.

### Local mirror and documentation

Keep `just check` and `just test-contract` aligned with the direct roots and duplicate-TypeScript
removal used in CI. Update the documented check count, web command, and contract-lock behavior so
contributors do not keep running or describing the superseded orchestration.

## Failure behavior

- Either web shard fails -> stable `web` fails; shard 2 still attempts static checks unless cancelled.
- Lint, TypeScript, or Vite build fails -> stable `web` fails.
- Contract checksum drifts -> `contracts` fails.
- A Python marker or directory selection is wrong -> the corresponding pytest invocation fails or its
  collection-parity verification detects a changed item set.
- A timing artifact overlaps another shard -> refresh script refuses to write the merged timing file.

## Acceptance criteria

- Both web shards together select the same 249 files and 1,468 tests observed on approved `main`.
- `maxWorkers: 1` and Vitest isolation are unchanged.
- The stable check name remains `web` and succeeds only after both shards, including shard 2's static
  checks, succeed.
- Unit, integration, and contract commands collect the same node IDs as their former marker-only forms.
- Contract drift is mutation-proven red and the restored contract is green.
- Refreshed duration artifacts are disjoint and cover the complete current integration selection.
- Existing R61 backstop tests remain green.

## Non-goals

- Contract-response sharding
- Coverage-policy changes
- Path-based job skipping
- Security-ratchet or GitHub-setting changes
- Action SHA pinning
- Commits, pushes, pull requests, or live repository mutations
