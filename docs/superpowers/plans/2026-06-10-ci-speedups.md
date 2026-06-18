# CI Speedups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut PR CI wall-clock from ~18 min to ~13 min and stop superseded runs, per `docs/superpowers/specs/2026-06-10-ci-improvements-design.md` (P1 durations refresh + P2 concurrency-cancel + P3 caching). No test-behavior or gate change.

**Architecture:** One workflow file edit (`.github/workflows/ci.yml`): a top-level `concurrency` block, setup-uv v8 + lockfile-keyed caches, npm cache for web, and `--store-durations --clean-durations` + a success-only artifact upload on each integration shard so every green run publishes fresh per-shard timings. A new `scripts/refresh-test-durations.sh` merges the four disjoint artifacts into `apps/api/.test_durations`. The PR's own first CI run supplies the artifacts; the merged file is committed to the same branch and the second run validates the balance.

**Tech Stack:** GitHub Actions, pytest-split 0.11 (`duration_based_chunks`), `gh` CLI, bash + uv-managed Python.

**Verification model:** Workflow YAML cannot be executed locally — local checks are YAML validity + `bash -n`; the real verification is the PR's own CI runs (that is by design: run 1 generates the durations artifacts, run 2 proves the re-balance).

---

### Task 1: Rewrite `.github/workflows/ci.yml` (concurrency + caches + durations publishing)

> **Superseded note (as-built):** the inlined YAML below is the pre-review draft; the shipped `ci.yml`
> follows the corrections in [`../specs/2026-06-10-ci-improvements-design.md`](../specs/2026-06-10-ci-improvements-design.md)
> §3 (`### .github/workflows/ci.yml`): the concurrency `group` uses a per-run id on main
> (`ci-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}`, not bare
> `github.ref`); `setup-uv` is pinned to `@v8.2.0` (not floating `@v8`); and each shard's
> `upload-artifact` step adds `include-hidden-files: true` + `if-no-files-found: error`. Read the snippet
> for shape; defer to the spec §3 and the live `ci.yml` for the exact values.

**Files:**
- Modify: `.github/workflows/ci.yml` (full replacement below)

- [ ] **Step 1: Replace the file content with:**

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

# A newer push to the same PR supersedes the in-flight run — cancel it. Pushes to main are never
# cancelled (every merged commit keeps a full CI record).
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  contracts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "22" }
      - name: lint OpenAPI
        run: npx --yes @redocly/cli lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml

  api:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: install uv
        uses: astral-sh/setup-uv@v8
        with:
          enable-cache: true
          cache-dependency-glob: "apps/api/uv.lock"
      - name: sync
        working-directory: apps/api
        run: uv sync --frozen
      - name: lint
        working-directory: apps/api
        run: uv run ruff check . && uv run ruff format --check --diff .
      - name: types
        working-directory: apps/api
        run: uv run mypy src
      - name: unit tests
        working-directory: apps/api
        run: uv run pytest -m unit

  migrations:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: easysynq
          POSTGRES_PASSWORD: easysynq
          POSTGRES_DB: easysynq
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U easysynq" --health-interval 5s
          --health-timeout 3s --health-retries 20
    env:
      DATABASE_URL_SYNC: postgresql+psycopg://easysynq:easysynq@localhost:5432/easysynq
      DATABASE_URL: postgresql+psycopg://easysynq:easysynq@localhost:5432/easysynq
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with:
          enable-cache: true
          cache-dependency-glob: "apps/api/uv.lock"
      - working-directory: apps/api
        run: uv sync --frozen
      - name: upgrade head then downgrade base (reversible) then back to head
        working-directory: apps/api
        run: uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head
      - name: autogenerate drift check (models == migrations)
        working-directory: apps/api
        run: uv run alembic check

  # The integration suite is sharded across N parallel runners (pytest-split, balanced by the
  # committed apps/api/.test_durations). Each shard is its own process spinning its own testcontainers
  # → the single-DB / single-org contract holds per shard. ~45 min serial → ~13 min wall.
  #
  # Each green shard also re-times exactly the tests it ran (--store-durations --clean-durations
  # replaces the workspace file with this shard's fresh timings; the *split* still reads the
  # committed file) and publishes them as an artifact. scripts/refresh-test-durations.sh merges the
  # four into apps/api/.test_durations — re-run it whenever shard wall-clocks drift apart.
  integration-shards:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        group: [1, 2, 3, 4]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with:
          enable-cache: true
          cache-dependency-glob: "apps/api/uv.lock"
      - working-directory: apps/api
        run: uv sync --frozen
      - name: integration tests (shard ${{ matrix.group }}/4, testcontainers spin their own Postgres)
        working-directory: apps/api
        run: >-
          uv run pytest -m integration --splits 4 --group ${{ matrix.group }}
          --durations-path .test_durations --store-durations --clean-durations
      - name: publish fresh shard durations
        if: success()
        uses: actions/upload-artifact@v4
        with:
          name: test-durations-${{ matrix.group }}
          path: apps/api/.test_durations
          retention-days: 7

  # Aggregator gate: keeps the required-status-check name "integration" stable (no branch-protection
  # change needed) and passes iff EVERY shard passed.
  integration:
    needs: integration-shards
    if: ${{ always() }}
    runs-on: ubuntu-latest
    steps:
      - name: gate on the shard results
        run: |
          result='${{ needs.integration-shards.result }}'
          if [ "$result" != "success" ]; then
            echo "integration shards did not all pass (result=$result)"; exit 1
          fi
          echo "all integration shards passed"

  web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: apps/web/package-lock.json
      - working-directory: apps/web
        run: npm ci
      - working-directory: apps/web
        run: npm run lint && npm run typecheck && npm run build && npm test
```

- [ ] **Step 2: Validate the YAML parses**

Run (from repo root):
```bash
cd apps/api && ~/.local/bin/uv run --no-sync python -c "import yaml; yaml.safe_load(open('../../.github/workflows/ci.yml')); print('yaml ok')"
```
Expected: `yaml ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: concurrency-cancel superseded PR runs, uv/npm caches, shards publish fresh durations"
```

---

### Task 2: Create `scripts/refresh-test-durations.sh`

**Files:**
- Create: `scripts/refresh-test-durations.sh` (mode 100755, like the other repo scripts)

- [ ] **Step 1: Write the script:**

```bash
#!/usr/bin/env bash
# Refresh apps/api/.test_durations from the per-shard artifacts a green ci.yml run publishes.
#
# Each integration shard runs pytest-split with --store-durations --clean-durations, so its
# test-durations-<N> artifact holds fresh timings for exactly the tests it ran; the union of the
# four shards is a complete fresh durations file. Run this whenever shard wall-clocks drift apart,
# review the diff, commit.
#
# Usage: scripts/refresh-test-durations.sh [run-id]
#   run-id  a ci.yml run whose integration shards were green; defaults to the latest
#           successful run on main.
set -euo pipefail
cd "$(dirname "$0")/.."

run_id="${1:-}"
if [ -z "$run_id" ]; then
  run_id="$(gh run list --workflow=ci.yml --branch=main --status=success --limit 1 \
    --json databaseId --jq '.[0].databaseId')"
  [ -n "$run_id" ] || { echo "error: no successful ci.yml run on main found" >&2; exit 1; }
fi
echo "merging durations artifacts from run $run_id"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for g in 1 2 3 4; do
  gh run download "$run_id" -n "test-durations-$g" -D "$tmp/$g"
done

(cd apps/api && uv run --no-sync python - "$tmp" <<'PYEOF'
import json
import pathlib
import sys

tmp = pathlib.Path(sys.argv[1])
merged: dict[str, float] = {}
for g in (1, 2, 3, 4):
    shard = json.loads((tmp / str(g) / ".test_durations").read_text())
    overlap = merged.keys() & shard.keys()
    if overlap:
        raise SystemExit(
            f"error: shards overlap on {sorted(overlap)[:3]} — artifacts are not from one run"
        )
    merged.update(shard)
out = pathlib.Path(".test_durations")
out.write_text(json.dumps(dict(sorted(merged.items())), indent=4))
print(f"wrote apps/api/.test_durations with {len(merged)} entries")
PYEOF
)
echo "done — review the diff and commit apps/api/.test_durations"
```

- [ ] **Step 2: Syntax-check and mark executable**

```bash
bash -n scripts/refresh-test-durations.sh && echo "syntax ok"
git add scripts/refresh-test-durations.sh
git update-index --chmod=+x scripts/refresh-test-durations.sh
```
Expected: `syntax ok`; `git ls-files -s scripts/refresh-test-durations.sh` shows mode `100755`.

- [ ] **Step 3: Commit**

```bash
git commit -m "ci: add refresh-test-durations.sh (merge per-shard durations artifacts)"
```

---

### Task 3: diff-critic review of the branch diff

- [ ] **Step 1:** Run the `diff-critic` agent (Agent tool, `subagent_type: diff-critic`) on the `feat/ci-speedups` diff vs `main`. Fold only confirmed findings; fix and commit.

---

### Task 4: Open the PR; first CI run generates the durations artifacts

- [ ] **Step 1:** Push and open the PR against `main` (`gh pr create`), titled
  `ci: re-balance integration shards + concurrency-cancel + uv/npm caches`. The body explains the
  two-run choreography (run 1 = still-imbalanced, produces artifacts; then the fresh
  `.test_durations` lands; run 2 validates) and links the spec.
- [ ] **Step 2:** Wait for run 1's integration shards to go green (~18 min — still the old balance).
  If a shard fails, debug before proceeding (durations artifacts are success-only).

---

### Task 5: Refresh `.test_durations` from run 1's artifacts

- [ ] **Step 1:**

```bash
bash scripts/refresh-test-durations.sh <run-1-id>
```
Expected: `wrote apps/api/.test_durations with ~4xx entries` (≈ today's ~406 collected integration tests; was 250).

- [ ] **Step 2: Sanity-check the merged file** — entry count ≥ 400, includes
  `test_mirror_scan.py`/`test_periodic_review.py`/`test_capa.py` keys, top file totals roughly match
  the log-derived table in the spec (§F1).

- [ ] **Step 3: Commit + push**

```bash
git add apps/api/.test_durations
git commit -m "ci: refresh .test_durations (250 → ~4xx entries; re-balances the 4 shards)"
git push
```

---

### Task 6: Validate run 2's balance and finalize

- [ ] **Step 1:** Wait for run 2; confirm all required checks green.
- [ ] **Step 2:** Pull the shard job timings (`gh api .../runs/<run-2>/jobs`); expect the four shards
  within ~11–14 min of each other's start-to-finish (perfect ≈ 11.2 min test time + ~1.5 min
  overhead). Record before/after numbers in the PR description.
- [ ] **Step 3:** Confirm the caches took (run-2 `sync` steps should show cache restore; npm ci faster).
  Note: run 2 is the first run *after* the caches were saved by run 1, so it is the first proof.

---

## Out of scope (proposal-only, in the spec)

- Codex trigger change — owner console action (spec §5).
- 6-shard bump (P5) and docs-only path filter (P6) — decide after this PR's numbers land.
