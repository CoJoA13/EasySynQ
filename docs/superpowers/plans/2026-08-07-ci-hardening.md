# CI Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce CI wall time while preserving the full web, unit, integration, and contract test selections and strengthening the contract-drift gate.

**Architecture:** Vitest file-level work is split across two isolated GitHub-hosted runners, with static checks running on the second already-hydrated runner and a stable zero-work `web` aggregate preserving failure semantics. Python jobs keep their markers but collect only from their authoritative directories. A dependency-free workflow contract test protects the orchestration, and existing shard artifacts refresh integration timing weights.

**Tech Stack:** GitHub Actions YAML, Bash, Vitest 4, pytest, pytest-split, GitHub CLI.

## Global Constraints

- Preserve `pool: "forks"`, `maxWorkers: 1`, and default Vitest isolation.
- Preserve all 249 web test files / 1,468 tests, 283 contract cases, and current integration selections.
- Do not add path filtering, retries, `fail-fast: true`, or changed-file-only selection.
- Do not modify product code, dependencies, GitHub settings, or live systems.
- Do not commit, push, or open a pull request without separate owner authorization.
- Preserve the pre-existing untracked root `.codex/` directory.

---

### Task 1: Add a red workflow-contract regression

**Files:**
- Create: `scripts/tests/test-ci-hardening.sh`
- Create: `apps/api/tests/unit/test_ci_workflow.py`

**Interfaces:**
- Consumes: `.github/workflows/ci.yml`, `apps/web/vite.config.ts`
- Produces: a dependency-free exit-zero/exit-one structural regression command

- [ ] **Step 1: Add assertions for the approved invariants**

  The shell test must assert exact presence of `web-shards`, `fail-fast: false`, matrix `[1, 2]`,
  `--shard=${{ matrix.shard }}/2`, the stable `web` dependency/result gate, direct pytest roots,
  `gen-contracts.sh --check`, and its own CI invocation. It must reject `npm run typecheck` in the
  workflow and assert `maxWorkers: 1` remains in `vite.config.ts`.

- [ ] **Step 2: Run the test against the pre-change workflow**

  Run: `bash scripts/tests/test-ci-hardening.sh`

  Expected: non-zero with failures for missing `web-shards`, direct roots, and contract check.

- [ ] **Step 3: Add a parsed-YAML semantic regression**

  Use the existing direct PyYAML dependency to assert exact matrix shape, commands, step order,
  hard-fail semantics, stable aggregate fields, package scripts, and Vitest isolation settings.

---

### Task 2: Implement the workflow orchestration

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `justfile`
- Modify: `CLAUDE.md`
- Modify: `docs/dev-workflow.md`
- Modify: `.pre-commit-config.yaml` (comment only)
- Modify: current `.claude/commands/`, `.claude/rules/windows-dev.md`, and the fresh-Linux runbook

**Interfaces:**
- Consumes: the existing npm and uv lockfiles and existing marker taxonomy
- Produces: `web-shards` matrix, shard-2 static checks, and stable `web` gate; narrowed Python collection commands

- [ ] **Step 1: Add two complete Vitest shards**

  Add `strategy: { fail-fast: false, matrix: { shard: [1, 2] } }` and run:

  ```yaml
  run: npm test -- --shard=${{ matrix.shard }}/2
  ```

- [ ] **Step 2: Run static checks on the second hydrated shard**

  Add this step after the Vitest step so it runs even when shard 2's tests fail but not when cancelled:

  ```yaml
  - name: lint and build
    if: ${{ !cancelled() && matrix.shard == 2 }}
    working-directory: apps/web
    run: npm run lint && npm run build
  ```

- [ ] **Step 3: Convert `web` into the stable aggregate gate**

  Set `needs: web-shards`, `if: ${{ always() }}`, fail unless
  `${{ needs.web-shards.result }}` is `success`. The aggregate needs no checkout or dependency setup.

- [ ] **Step 4: Narrow Python collection without dropping markers**

  Use these exact commands:

  ```text
  uv run pytest tests/unit -m unit
  uv run pytest tests/integration -m integration ...
  uv run pytest tests/integration/test_contract_response_schemas.py -m contract --tb=short
  ```

- [ ] **Step 5: Add the contract-lock and workflow-contract gates**

  The `contracts` job runs:

  ```text
  bash scripts/gen-contracts.sh --check
  bash scripts/tests/test-ci-hardening.sh
  ```

- [ ] **Step 6: Run the focused workflow regression**

  Run: `bash scripts/tests/test-ci-hardening.sh`

  Expected: all assertions pass.

- [ ] **Step 7: Align local mirrors and contributor documentation**

  Apply the same direct pytest roots and duplicate-TypeScript removal to `just check` /
  `just test-contract`, then update the CI count, web loop, and contract-lock descriptions.

---

### Task 3: Refresh integration timing weights

**Files:**
- Modify: `apps/api/.test_durations`

**Interfaces:**
- Consumes: `test-durations-1` through `test-durations-4` from run `31190679574`
- Produces: sorted, non-overlapping timing weights for the current integration inventory

- [ ] **Step 1: Run the existing refresh command**

  Run: `scripts/refresh-test-durations.sh 31190679574`

  Expected: four artifacts download and the script reports the merged entry count.

- [ ] **Step 2: Validate the refreshed file**

  Confirm valid JSON, unique node IDs, sorted keys, no contract-marker node IDs, and a material increase
  from the stale 430-entry baseline. Compare the artifact key union independently with the written file.

---

### Task 4: Prove selection parity and contract failure behavior

**Files:**
- Verify only; restore any temporary mutation before continuing

**Interfaces:**
- Consumes: old/new pytest commands, `packages/contracts/openapi.yaml`
- Produces: node-ID parity evidence and a red/green contract-drift result

- [ ] **Step 1: Compare pytest collections**

  Collect node IDs for each old marker-only command and its new directory-scoped equivalent. Normalize
  and compare the sets; each pair must be identical.

- [ ] **Step 2: Prove contract drift turns red**

  Safely copy the contract to a temporary backup, add a valid descriptive mutation, run
  `bash scripts/gen-contracts.sh --check`, require non-zero with `contract drift`, restore the original,
  and rerun the command to require zero.

- [ ] **Step 3: Validate the web shard union**

  Install the frozen npm dependencies, list or execute both shards, and confirm their file sets are
  disjoint and their union equals the unsharded file set. Execute both shards for the broad verification.

---

### Task 5: Broad verification and handoff

**Files:**
- Verify: all changed files and repository status

**Interfaces:**
- Consumes: integrated worktree state
- Produces: fresh evidence mapped to every acceptance criterion

- [ ] **Step 1: Run shell and YAML checks**

  Run the CI-hardening regression, R61 regression harness, R61 repository scan, `bash -n` on changed
  shell scripts, and a YAML parser over `.github/workflows/ci.yml`.

- [ ] **Step 2: Run affected web checks**

  Run frozen install, both Vitest shards, lint, and build. Both shards must pass with a combined inventory
  matching approved `main`.

- [ ] **Step 3: Inspect the integrated diff**

  Verify that only the workflow, its regression/local mirrors/documentation, timing file, design, and
  plan changed; confirm the root checkout and its `.codex/` directory were not modified.

- [ ] **Step 4: Report without publishing**

  Provide changed-file links, exact verification results, remaining unverified GitHub-hosted behavior,
  and the isolated branch/worktree name. Do not stage, commit, push, or create a PR.
