# CI improvements — findings & design (2026-06-10)

> Investigation anchored on PR #108 (S-drift-2, ~30 commits, 11+ full CI runs). All timings come from
> the GitHub Actions API job records and the raw shard logs of the final successful PR run
> (27268518707). Constraint honored throughout: **no test-behavior change, no gate weakening** —
> this is speed/signal/cost only.

## 1. Findings

### F1 — Shard imbalance is the whole wall-clock story (root cause: stale `.test_durations`)

The successful PR run's integration shards ran **7.4 / 11.1 / 18.0 / 9.6 min** (shards 1/2/3/4).
Parsing the shard logs gives real per-file durations: **~44.9 min of serial test time across 47
files** — a perfect 4-way balance would be ~11.2 min + ~1.5 min fixed overhead per shard.

The committed `apps/api/.test_durations` dates from PR #61 (the slice that introduced sharding). It
holds **250 entries across 30 files**; the suite is now **~406 tests across 47 files**. Seventeen
whole files are missing — including the heaviest in the suite today:

| file (missing from durations) | actual (log-derived) |
|---|---|
| `test_mirror_scan.py` | 319 s |
| `test_dcr_implement.py` | 253 s |
| `test_periodic_review.py` | 238 s |
| `test_capa.py`, `test_audits.py`, `test_workflow_engine.py`, `test_dcr*.py`… | smaller but numerous |

pytest-split estimates a missing test at the **average** of recorded durations (~6.4 s here), so the
new heavy files look 5–50× cheaper than they are; the default `duration_based_chunks` algorithm then
packs them — plus genuinely-recorded-heavy `test_restore`/`test_mirror`/`test_render` — into the
same contiguous chunk. That chunk is shard 3.

### F2 — "Shard 3 queued late" is disproven

Across all eight PR #108 runs sampled, **every job (shards included) started ≤3 s after creation**.
Shard 3 *finishes* 7–9 min after the others because it is overloaded, not because it queued. Runner
concurrency was never approached: max observed overlap was 2 runs × 8 jobs = 16 concurrent, under
the 20-job public-repo limit. No action needed on runner concurrency.

### F3 — Zero caching anywhere

- `astral-sh/setup-uv@v3` predates built-in caching defaults; six jobs per run (`api`, `migrations`,
  4 shards) each do a cold `uv sync --frozen` (~20–25 s each). setup-uv v8's `enable-cache` defaults
  to on for GitHub-hosted runners.
- `actions/setup-node@v4` in `web` has no `cache: npm`; `npm ci` re-downloads every run.

### F4 — No concurrency-cancel; superseded runs finish pointlessly

PR #108 ran the full workflow **11+ times**, and pushes 4 min apart (08:02, 08:06) ran complete
~75-job-minute CI passes concurrently — the earlier one already superseded. A standard
`concurrency` group with `cancel-in-progress` on PRs eliminates this entirely (pushes to `main`
keep their runs — every merged commit still gets a full record).

### F5 — Codex auto-review churn

`chatgpt-codex-connector` posted **10 review rounds + 33 inline comments** on PR #108 — one full
review per fix-push. Codex review triggering is **not configurable in-repo**; it lives in the owner's
Codex console (chatgpt.com/codex/settings/code-review): the *Automatic reviews* toggle and the
trigger preference (on-PR-open / every-push / smart). On-demand review is always available by
commenting `@codex review` (optionally with focus, e.g. `@codex review for security regressions`).
An `AGENTS.md` with a **Review guidelines** section focuses *what* Codex flags but does not gate
*when* it runs.

### F6 — The 4-shards + aggregate shape is right

Branch protection requires exactly `contracts, api, migrations, web, integration` — the aggregate
job keeps the required-check name stable, so shard count is a free knob (no settings change).
After re-balancing: 4 shards ≈ **~13 min** long pole; 6 shards ≈ **~9.5 min**. Web (~2.5 min) stays
far from the critical path either way.

### F7 — Docs-only path filtering is possible but is the one gate-shaped change

A `dorny/paths-filter` job + a `skipped`-aware aggregate gate could skip integration shards on
diffs confined to `docs/**`, `**/*.md`, `mockup/**`. It touches required-gate logic, so it is
**proposal-only** here (see §3). Docs-only PRs do occur (e.g. #104) but are a minority.

### F8 — Testcontainers image pulls: negligible

Each shard pulls `postgres:16`/MinIO/Redis (~10–20 s amortized). Not worth Docker-layer-cache
machinery on hosted runners; revisit only if Docker Hub rate limiting ever bites.

## 2. Prioritized list (effort → saved)

| # | change | effort | saves | risk |
|---|---|---|---|---|
| P1 | **Refresh `.test_durations` + keep it refreshable** (shards store+upload fresh durations as artifacts; merge script; commit merged file) | S | ~5–6 min off every PR run's long pole (18 → ~13 min) | none — split input only, test behavior identical |
| P2 | **`concurrency` cancel-in-progress for PRs** | XS | entire superseded runs (~75 job-min each; PR #108 had several) | none — main pushes exempt |
| P3 | **uv cache (setup-uv v3→v8) + npm cache** | XS | ~15–20 s × 6 jobs + ~30–40 s web, every run | none — lockfile-keyed |
| P4 | **Codex: switch auto-review to on-PR-open or on-demand** | XS (owner console) | ~9 redundant review rounds/PR of latency + reviewer noise | owner judgement; on-demand `@codex review` keeps full capability |
| P5 | 6 shards instead of 4 | XS | further ~3.5 min (13 → ~9.5) | slightly more fixed overhead/run; do after P1 proves balance |
| P6 | Docs-only path filter for integration shards | M | ~13 min on docs-only PRs | touches required-gate logic — needs careful `skipped`-aware aggregate; proposal only |

## 3. Design of the implemented slice (P1 + P2 + P3)

### `.github/workflows/ci.yml`

1. **Concurrency** (top level):
   ```yaml
   concurrency:
     group: ci-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}
     cancel-in-progress: ${{ github.event_name == 'pull_request' }}
   ```
   PR pushes cancel the superseded run. Push (main) runs get a **unique group per run** — GitHub
   allows only one running + one pending run per group regardless of `cancel-in-progress`, so a
   shared main group would queue-cancel the middle run of three back-to-back merges (diff-critic
   finding).
2. **Caching**: bump `astral-sh/setup-uv@v3` → `@v8.2.0` (exact pin — setup-uv stopped publishing
   floating major tags at v8; a bare `@v8` does not resolve, diff-critic CRITICAL) in `api`,
   `migrations`, `integration-shards` with `enable-cache: true` +
   `cache-dependency-glob: "apps/api/uv.lock"`. Add `cache: npm` +
   `cache-dependency-path: apps/web/package-lock.json` to the `web` job's setup-node. (`contracts`
   stays as-is — a 20 s npx job, not worth a cache key.)
3. **Durations stay fresh** (the refresh pipeline): each shard's pytest invocation gains
   `--store-durations --clean-durations` (pytest-split 0.11: at session finish the file is replaced
   with **exactly the tests this shard ran**, freshly timed; the *input* split still reads the
   committed file at collection). A subsequent `actions/upload-artifact@v4` step (success-only,
   `retention-days: 7`) publishes `test-durations-{group}` — with **`include-hidden-files: true`**
   (v4 excludes dotfiles by default: without it the upload silently matches zero files and stays
   green — diff-critic MAJOR) and **`if-no-files-found: error`** so that failure mode stays loud.
   Every green run is now a refresh source; storing timings adds no measurable test time.

### `scripts/refresh-test-durations.sh` (new)

Downloads the 4 artifacts from a given (or latest green main) run via `gh run download`, merges the
disjoint JSON dicts with the uv-managed Python, and writes `apps/api/.test_durations`. Re-run any
time shard balance drifts; commit the result. (Plain bash, Git-Bash-safe, mirroring
`scripts/demo-user.sh` conventions.)

### `apps/api/.test_durations` (refreshed in-PR)

This PR's own first CI run produces the four artifacts; the merged fresh file is committed to the
same branch, and the next CI run validates the new balance (~11–13 min/shard expected).

### Explicitly NOT changed

Test code/markers, shard count, the aggregate-gate logic, required check names, `fail-fast: false`,
the pytest flags that affect selection or execution order (we keep `duration_based_chunks`;
`least_duration` packs tighter but reorders execution within a shard — the suite *should* be
order-independent per the delta-based-assertion rule, but latent order coupling is a known class of
trap here, so reordering is not worth the noise risk for ~1 min of variance).

## 4. Risks & mitigations

- **Artifact from a red run pollutes a refresh** → upload is `if: success()` only.
- **A shard crash before pytest's session-finish** → no durations file write; success-gated upload
  means no artifact, refresh script fails loudly on a missing artifact.
- **Cache poisoning/staleness** → caches are keyed on `uv.lock` / `package-lock.json`; a lockfile
  change gets a cold cache automatically.
- **cancel-in-progress kills a run someone is watching** → only ever cancels a run of the *same PR
  ref* that a newer push has superseded; `main` is exempt.

## 5. Owner actions (not implementable from the repo)

- **Codex** (chatgpt.com/codex/settings/code-review): switch *Automatic reviews* off (use
  `@codex review` once a branch is green + diff-critic'd) or set the trigger preference to
  on-PR-open. Optionally add an `AGENTS.md` **Review guidelines** section to focus its findings.
- Decide whether P5 (6 shards) is wanted after P1's balance is proven, and whether P6 (docs-only
  path filter) is worth its gate-logic complexity.
