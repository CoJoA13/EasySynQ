# GitHub as primary hosting again

Owner decision, 2026-09-21: primary hosting returns from `gitlab.com/synqsuite-group/EasySynQ` to
the existing public repository **`github.com/CoJoA13/EasySynQ`**; the GitLab project is
**archived** afterwards, not deleted. This reverses the 2026-09-08 decision that "repository
hosting and automation should not use GitHub services", which was recorded narratively
(`slice-history.md`, `current-status.md`, `RES-RENOVATE-GITHUB-METADATA`) and never as a register
entry. The reversal ships as **R86** so it is binding rather than narrative.

Scope is **minimum to be primary**: a gate on GitHub at parity with the current pipeline policy,
branch protection, dependency updates, and the guards and docs turned to face GitHub. AI review
(GitLab Duo today) has no like-for-like replacement and is a separate decision.

## Starting facts

- The GitHub repository is public, active (1,816 Actions runs; the `ci` workflow still enabled),
  has **no branch protection and no rulesets**, no Actions secrets, no webhooks, no tags. Merge
  settings already suit us: squash and rebase allowed, merge commits off, auto-merge on,
  delete-branch-on-merge on, and **the squash message is the PR body** — the title-only squash
  problem GitLab had does not exist here.
- Its `main` tip `7a4162b` (2026-09-04) is an ancestor of the current `main`: the 157 GitLab-era
  commits **fast-forward** onto it. No rewrite, no force push, history preserved on both sides.
- Dependabot never stopped: it opened eight PRs on 2026-09-15 against the stale tree, all failing.
- Left open on GitHub from before the move: eight Dependabot PRs (all superseded by GitLab-era
  merges), human PR #476 (superseded — its retention half shipped as #490 and its R27 half as the
  #360/#361 addenda; only `RES-WORM-EVENT-BASIS-REEXTENSION` survives), and twelve issues
  #420–#436, of which none is resolved, five are partial (#421, #423, #430, #431, #433) and seven
  open (#420, #422, #425, #432, #434, #435, #436). They stay open; the migration only annotates.

## Owner decisions

| Question | Decision | Rejected |
|---|---|---|
| Target | the existing `CoJoA13/EasySynQ` | a fresh org-owned repo (old PR references would not resolve) |
| GitLab afterwards | archive | keep as mirror (nothing to keep in sync once CI moves); delete (breaks every evidence URL) |
| Merge gate | **rulesets**: required checks, "require branches to be up to date", squash only, no force push, conversation resolution | merge queue (nearest analogue to merged-results, but a queue-triggered workflow variant for one maintainer at low volume) |
| Dependency updates | **Dependabot** (already active on the repo) | self-hosted Renovate in Actions; the Mend app |
| Register | **R86** | narrative only |

## What "up to date" buys, and what merged-results bought

GitLab's merged-results pipeline plus the semi-linear merge tested the exact tree `main` would
receive. On GitHub, `pull_request` runs test the **merge commit** of the PR onto its base at the
time of the run, and "require branches to be up to date before merging" refuses to merge once the
base has moved, forcing a rebase and a fresh run. The composition is the same guarantee as long as
squash is the only merge method (rebase-merge would replay commits the run never saw). The
evidence convention therefore becomes: **the PR's final check run on its head SHA is the merge
evidence**, and the post-merge `main` run keeps only the cheap backstops, exactly as
`.gitlab-ci.yml` does today.

## The workflow at parity

`.github/workflows/ci.yml` is rebuilt from `.gitlab-ci.yml`, not patched: the retained file is
missing eight guard scripts, the docs lane, the built-image scans and the runtime acceptance, and
still asserts that `security` is non-required. Parity means the same thirteen jobs and the same
policy:

| Policy | GitLab today | GitHub |
|---|---|---|
| plain branch pushes run nothing | `workflow: push → never` | `on: pull_request` + `push: [main]` + `tags: v*` + `workflow_dispatch`; no bare-branch trigger |
| suites selected by changed paths | `rules: changes: compare_to: refs/heads/main` | `dorny/paths-filter`-style job (a pinned action, or a `git diff --name-only $BASE...HEAD` step) producing outputs that gate jobs with `if:` — one filter job, not per-trigger `paths:`, so main/tag/manual runs stay unconditional |
| docs-only lane | `docs-tests`, `.rules-docs-only` | the same job, gated on "no code path changed" |
| `api`, `security`, `migrations` on any code change | `.rules-code*` | same `if:` |
| post-merge main = backstops only | `.rules-code-and-main` | `if: github.event_name != 'push' \|\| …` per job, mirroring the templates |
| tags and manual runs = everything | `$CI_COMMIT_TAG`, `web/api/trigger` | `startsWith(github.ref, 'refs/tags/v')` and `workflow_dispatch` |
| newer commit cancels older | `interruptible` + `auto_cancel` | `concurrency: { group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true }`, with `main` excluded |
| four integration shards, two web shards | `parallel:` | `strategy.matrix` |
| whole-pipeline gate | GitLab merge check | **a `gate` job** with `needs:` on every job and `if: always()` that fails if any needed job failed **or was skipped when it should have run** — the single required check. This is the aggregator `.gitlab-ci.yml:13-15` deliberately dropped; on GitHub it is the only way to require "everything that ran, passed" without listing path-conditional jobs as required checks (a skipped required check blocks merging forever) |
| release gate on `v*` | `release-gate` | same job, tag-conditional |
| services by alias | `postgres:5432` | `services:` with `localhost` ports, as the pre-move workflow did |
| root runner workaround | `useradd ci` + `runuser` | not needed: the ubuntu runner is unprivileged; the six fail-closed permission tests run natively |
| DinD for Testcontainers | `docker:29-dind` | the runner's own Docker; no `TESTCONTAINERS_HOST_OVERRIDE` |

The `security` job keeps its exact script, including both built-image scans and the fixed-version
threshold. Its **required** status is the change the retained file got wrong.

`scripts/tests/test-gitlab-ci-hardening.sh` becomes `test-github-ci-hardening.sh` with the same
58 assertions re-targeted (the `continue-on-error` ban stays; it is the GitHub escape hatch).
`apps/api/tests/unit/test_ci_workflow.py` keeps the policy tests — rule ordering, the code-path
coverage walk over `git ls-files`, the docs-lane selection pin, the unconditional jobs, cancellation
exempting `main` — re-expressed against the workflow's `if:` conditions and the filter job's
outputs. `.gitlab-ci.yml` is deleted in the same change; keeping two gates is how the retained
file went stale.

## Dependency updates: Dependabot, and what that costs

`.github/dependabot.yml` comes back live with the Renovate rules that survive re-expression:

| Renovate rule | Dependabot |
|---|---|
| python-minor-patch / web-minor-patch / contract-tools-minor-patch groups | `groups:` per ecosystem (already present) |
| reportlab major refused | `ignore: semver-major` (already present) |
| Mantine major refused | `ignore: semver-major` (already present) |
| python base `<3.13` | `ignore: versions: [">=3.13"]` (already present) |
| js-yaml major refused (closed !7) | `ignore` on `/packages/contracts`; moot in practice — Dependabot does not edit `overrides`, so the 4.3.2 pin never moves and a js-yaml advisory becomes a manual action |
| npm `<12` in the web Dockerfile | **not expressible**: Dependabot's `docker` ecosystem tracks `FROM`, not an `npm install -g npm@…` line. `test_deploy_configuration` still pins `<12`; bumps are manual |
| `uv==` pin in `apps/api/Dockerfile` | **not tracked**; manual |
| **`infra/images.lock` digests** | **not tracked.** Renovate's custom manager updated the compose tag and the lock digest together, and `scripts/tests/test-renovate-images.mjs` proved it. Dependabot's `docker-compose` ecosystem bumps `compose.yml` tags only, so **every compose image PR reddens `compose-images-lock` until the lock is refreshed by hand.** |

The last row is the material cost of the Dependabot decision. The mitigation in this change is
mechanical: a documented `just refresh-images-lock <service>` recipe (or `scripts/refresh-images-lock.sh`) that resolves the tag to its current digest and rewrites the lock line, run by the maintainer on each such PR before merging. The alternative — keeping Renovate for images only — was rejected as two updaters. Renovate's files, its two CI jobs, `scripts/run-renovate.sh`, `scripts/tests/test-renovate-images.mjs` and the Renovate-specific assertions in the guards are removed; `RES-RENOVATE-GITHUB-METADATA` is closed as **superseded by R86**, with its lockfile-refresh concern restated as a Dependabot fact (Dependabot updates lockfiles itself).

## Guards that flip

- `scripts/tests/test-gitlab-distribution.sh` → removed; its one platform-neutral assertion (the
  `python-version` datasource must not be queried) has no Dependabot analogue and is dropped with
  the rationale kept in the dependabot comment.
- `scripts/tests/test-contributor-hosting.sh` → inverted: `pr.md` and `triage-review.md` must use
  `gh`, not `glab`; the install docs must clone from `github.com/CoJoA13/EasySynQ`.
- `scripts/tests/test-refresh-test-durations.py` → the poisoned binary becomes `glab`; the fake
  becomes `gh`; `scripts/refresh-test-durations.sh` is rewritten against the Actions API
  (`actions/runs?branch=main&status=success`, artifact download by name).
- `scripts/check-no-site-data.sh` R61 owner-token guard re-activates automatically (it only ever
  recognised github.com remotes); its fixture already uses a github.com remote. Verify it reddens on
  a planted owner token before merging — it has been inert for two weeks.
- `.claude/commands/pr.md`, `triage-review.md` → `gh pr create`, `gh api repos/{owner}/{repo}/pulls/N/comments`
  + review-thread resolution via GraphQL (`resolveReviewThread`); `.claude/rules/engineering-patterns.md`
  drops the GitLab squash-message trap and notes `PR_BODY` is the squash message here.
- `scripts/doctor.sh` gains a `gh` check (it checks neither CLI today).
- `.gitlab/` templates → `.github/ISSUE_TEMPLATE/*.md` and `.github/pull_request_template.md`;
  `docs/runbooks/gitlab-repository-setup.md` → `github-repository-setup.md` (the out-of-git
  settings contract: rulesets, required check name, Dependabot, secrets, tag protection).

## Evidence URLs

Hundreds of `gitlab.com` pipeline, job, MR and issue URLs in the three authority docs are dated
historical evidence. They are **not rewritten**: archiving keeps them resolving, and a sweep over
dated snapshots is exactly what the engineering patterns forbid. New evidence cites GitHub check
runs. `README.md`, `CONTRIBUTING.md`, `dev-workflow.md` and the install docs point at GitHub.

## Sequence

1. Merge !64 on GitLab so `main` carries the last GitLab-era status record.
2. Fast-forward push `main` to GitHub. Verify `git rev-parse origin/main` on both remotes agree.
3. Open the migration PR **on GitHub** from a branch of that `main`. Its Actions run is the first
   proof of the ported workflow; iterate there until the `gate` check is green, including a
   deliberately docs-only commit to prove the lane and a planted owner token to prove R61.
4. Apply the rulesets (required check `gate`, up-to-date, squash only, no force push,
   conversation resolution, tag protection `v*`) — **after** the PR is green, or it cannot merge.
5. Merge. Close the eight Dependabot PRs and #476 as superseded (with the evidence above);
   Dependabot regenerates against the new tree on its next weekly run.
6. Switch `origin` locally; `/finish-slice` with the R86 entry.
7. Archive the GitLab project (owner action, Settings → General → Advanced). Its 64 MRs and the
   pipelines stay readable.

## Acceptance

The migration is complete when: the `gate` check is required on `main` and green on the merged
PR; a docs-only PR ran the docs lane only; a code PR ran the path-selected suites; the built-image
security thresholds reported the same 47/43 no-fix counts; `check-no-site-data.sh` reddened on a
planted owner token; Dependabot opened at least one grouped PR against the new tree; the GitLab
project is archived; and `current-status.md` names the GitHub check run as the baseline evidence.

## Deliberately not in scope

AI review of PRs. Merge queue. Renovate's images.lock automation (replaced by the manual recipe).
Rewriting historical gitlab.com evidence links. Closing any of the twelve carried-over issues.
