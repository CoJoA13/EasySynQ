# GitHub repository setup

The active repository is [CoJoA13/EasySynQ](https://github.com/CoJoA13/EasySynQ) (R86). GitHub
settings complement `.github/workflows/ci.yml`; they are not stored in Git. Check both when moving
the project or diagnosing a green check that did not exercise the intended job.

## Branches, merges and releases

Rulesets, not classic branch protection, govern `main` (Settings > Rules > Rulesets). The `main`
ruleset carries:

- **Require a pull request before merging.** Status checks alone are not enough: a writer could
  start a `workflow_dispatch` run on a branch, obtain a green `gate` for that commit, and push the
  same SHA straight to `main`. With this rule every change to `main` arrives through a reviewed
  pull request, and the merge-method and conversation rules below apply to it.
- **Require status checks to pass**, with exactly one required check: **`gate`**, from the `ci`
  workflow. `gate` needs every other job and fails when any job failed, was cancelled, or was skipped
  while its own path condition said it should run. Never add a path-conditional job (`api`,
  `integration-shards`, `web-browser`, …) as a required check: a skipped required check blocks
  merging forever.
- **Require branches to be up to date before merging.** A pull-request run tests the merge commit of
  the head onto `main`; this rule refuses the merge once `main` has moved, forcing a rebase and a
  fresh run.
- **Require conversation resolution before merging.**
- **Block force pushes** and **restrict deletions**.
- Merge methods: **squash only** (allow squash merge on; merge commits and rebase merging off).
  Rebase merging would replay commits the run never saw. The squash commit message is the pull
  request body, so write the body as the commit message. Auto-merge and delete-branch-on-merge stay
  on. **Always suggest updating pull request branches** is on (enabled 2026-09-22), so the
  up-to-date rule above is one click rather than a local rebase.

A second ruleset targets tags `v*`: **restrict creation** to repository administrators, **block
force pushes** and **restrict deletions**. A protected tag still needs its `release-gate` job to
pass; tag protection is not release acceptance.

**Merge evidence convention.** With the up-to-date rule and squash-only merging, the pull
request's final check run on its head SHA tests the exact tree `main` receives and is the merge
evidence to cite (the run URL and the head SHA). The post-merge `main` run keeps only the cheap
backstops (the guards, `migrations`, `security`); it is a backstop, not the evidence. Tags and
manually started runs execute every suite.

Work on scoped branches and use reviewed pull requests. Plain branch pushes run no workflow.

## Actions

Actions are enabled for the repository with one tracked workflow, `ci`. CodeQL's default setup
and Dependabot also start their own dynamic runs, which are managed from settings. Workflow permissions are
read-only for `contents` (the workflow declares its own `permissions:`); do not raise the
repository default. Pull requests from forks run with a read-only token and no secrets, which is
sufficient because the gate needs none.

Every action is pinned to a full commit SHA with a `# vX.Y.Z` comment, and every checkout sets
`persist-credentials: false`; both are asserted by `test_ci_workflow.py` and
`scripts/tests/test-ci-hardening.sh`, and zizmor enforces them from the workflow side. The
repository setting **Require actions to be pinned to a full-length commit SHA** is on (enabled and
read back through the Actions permissions API on 2026-09-24). This enforces the same rule
server-side; keep action version comments and Dependabot updates alongside the immutable pins.

Two jobs keep the workflow and the history honest on every run:

- `workflow-and-secrets` runs actionlint (with shellcheck over each `run:` script), zizmor over
  `.github/` (the one reviewed exception is in `.github/zizmor.yml`), and a **gated** gitleaks scan
  of the whole history reachable from the checked-out commit. The scan reads the same
  `.gitleaks.toml` and `.gitleaksignore` as the pre-commit hook. The three tools run from upstream
  images pinned by tag and digest in the workflow; like trivy's binary in `security`, they are
  **manual pins** that no updater reads.
- `dependency-review` runs on pull requests only and fails when the change introduces a high or
  critical advisory.

A weekly scheduled run (Mondays 06:17 UTC) executes every suite against an unchanged `main`, so an
advisory published against locked dependencies or a base image turns `main` red without waiting for
a push. Treat a red scheduled run as the alert it is: open a pull request that fixes it. GitHub
disables scheduled workflows after 60 days without repository activity; re-enable it under
Actions if that happens.

**No secrets are required by CI today.** Every guard, suite and built-image scan runs from the
tracked source, public registries and the runner's own Docker. An empty Actions secrets list is the
expected configuration; adding one is a reviewed change to the workflow, not a settings-only step.

Concurrency cancels an in-flight run of the same pull request when a newer commit arrives; `main`,
tag and manual runs are never auto-cancelled.

## Dependabot

Dependency updates come from Dependabot, configured by the **tracked** `.github/dependabot.yml`.
There is no settings-side copy to keep in sync: enable **Dependabot version updates** under
Settings > Code security and leave grouping, schedules, ignores and version ceilings to the file.
Dependabot refreshes `uv.lock` and the npm lockfiles itself as part of each update pull request,
and every update still requires review and a green `gate`.

Each entry waits out a seven-day `cooldown` before proposing a newly published version (zizmor
holds this at seven days or more). Security updates ignore the cooldown.

What Dependabot does not do, and the maintainer does by hand (R86):

- `infra/images.lock` digests are not tracked. A Compose image update reddens `compose-images-lock`
  until `just images-update` (`scripts/images-update.sh`) re-resolves every lock entry to its current
  digest; it fails loudly on a partial result. Run it on the update branch, on a connected host with
  Docker, and paste its output into the lock before merging.
- The `uv==` pin in `apps/api/Dockerfile` and the `npm install -g npm@…` pin in the web Dockerfile
  are not tracked; bump them deliberately under the existing version guards
  (`test_deploy_configuration`).
- `overrides` in `package.json` are not edited, so an advisory against a pinned override (the
  contract tools' `js-yaml`) is a manual change.
- Container images referenced from workflow shell steps (trivy, actionlint, zizmor, gitleaks) and
  the `postgres:18` service image in the `migrations` job are not tracked; bump tag and digest
  together, deliberately.

**Dependabot alerts** and **Dependabot security updates** are on (enabled 2026-09-22). Security
update pull requests go through the same gate as version updates. There is no scheduled pipeline to inspect after a settings change; verify the configuration by
the next weekly run opening at least one grouped pull request, and read the Dependabot logs under
Insights > Dependency graph > Dependabot when a group is silent.

## Security evidence

The required `gate` check includes `security`. The npm audit policy and scanner operational
failures block merging. `scripts/check-built-image-security.sh` scans **both built API and web images**
and blocks HIGH/CRITICAL vulnerabilities with an available fixed version. Image findings without fixes
remain open; pip-audit findings, the filesystem scan, and image-secret findings remain advisory.
A green check run is not complete image-security clearance. Review the retained results and the
current follow-up record in
[`../open-residuals.md`](../open-residuals.md#res-container-security-triage).

Code security settings as of 2026-09-22 (Settings > Code security):

| Setting | State | Why |
| --- | --- | --- |
| Private vulnerability reporting | On | The destination `SECURITY.md` and `CONTRIBUTING.md` send reporters to. |
| Dependency graph, Dependabot alerts, Dependabot security updates | On | Advisory alerts and fix pull requests. |
| Secret scanning and push protection | On | Blocks known provider tokens at push time. |
| Secret scanning non-provider patterns, validity checks | Off | Not available for this repository without GitHub Secret Protection; the API accepted the request and left both disabled. The gated gitleaks scan covers generic patterns. |
| Code scanning (CodeQL default setup) | On | Python, JavaScript/TypeScript and Actions, on pull requests, pushes and a weekly schedule. Not a required check: results appear under Security > Code scanning and as pull request annotations. Triage alerts there; a dismissed alert needs a reason. |

CodeQL's default setup is configured in settings, not in a tracked workflow, so it does not appear
in `ci.yml` and `gate` does not wait for it. Promote it to a tracked advanced-setup workflow only if
it needs to become merge-blocking.

## Project front door and collaboration

Keep the repository description and topics aligned with the README. The repository is public;
do not infer broader rights from repository visibility or a dependency's license. Project
licensing is PolyForm Shield 1.0.0; the [license](../../LICENSE) and
[licensing guidance](../../LICENSING.md) cover internal use, permitted paid consulting, competing
offerings, and third-party terms. Describe it as source-available.

The README links the installation, user, and administrator manuals; the reviewed documentation in
this repository remains authoritative. Contribution and confidential-reporting instructions live in
`CONTRIBUTING.md`; `.github/ISSUE_TEMPLATE/` and `.github/pull_request_template.md` provide the
native report/review entry points. They are versioned in Git; do not create a second copy in the
repository settings, which can silently drift from the reviewed files.

Issues `#420`–`#436` from before the 2026-09-08 move stay open and are only annotated; closing any of
them is separate work.

### Wiki and project maintenance

The [Wiki](https://github.com/CoJoA13/EasySynQ/wiki) is a navigation index into reviewed repository
documents. Keep instructions, current facts and closure contracts in their existing authority homes;
use Wiki links instead of copying them. The actual
[EasySynQ project](https://github.com/CoJoA13/EasySynQ/projects) holds Status, Priority and Ledger record
fields. An issue's `RES-*` prefix and Ledger record field identify its authoritative residual.

At triage, reconcile every open issue and pull request with project membership, verify closed items
are Done, and preserve blocked reasons and owner priorities. Done on a closed, unmerged PR means
triage is complete, not that the code shipped. Close a residual issue only with its ledger closure
evidence; moving a card is not sufficient. New dependency PRs still require their own review and gate.

Native project workflows were enabled and verified on 2026-09-24:

| Event | Result |
| --- | --- |
| New/updated open issue or PR in this repository | Auto-add to the project (repository selector: EasySynQ; filter: `is:issue,pr is:open`) |
| Item added (issue or PR) | Status → Todo |
| Item closed (issue or PR, including unmerged PRs) | Status → Done |
| PR merged | Status → Done |
| Issue or PR reopened | Status → Todo for triage |

The pre-existing auto-add-sub-issues workflow remains enabled. Auto-close issue remains **off**:
card movement cannot substitute for ledger closure evidence. Native intake uses one repository
filter and was accepted without a quota upgrade or extra credentials. Auto-add is forward-looking;
backfill existing items explicitly when changing the filter.

At triage, move ready-for-review PRs from Todo to In review; there is no ready-for-review workflow
configured here. For reopened items, reapply a Blocked status when the retained `blocked:*` label
still applies, and restore In review for PRs ready for review. Status workflows do not change
Priority, Ledger record or labels. Closed-unmerged PRs use Done to mean disposition is complete,
not shipped. The [native workflow documentation](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-built-in-automations)
and [dated acceptance evidence](../slice-history.md#s-project-native-automation--verified-intake-and-status-lifecycle)
explain the controls and their verification.

### CI coverage and release boundary

The workflow covers API lint/types/unit tests and built-image runtime acceptance; real PostgreSQL,
MinIO and Redis integration; authenticated response contracts; populated migration transitions and
model drift; web lint/build/unit tests and Chromium; Compose rendering and image-lock consistency;
authority, site-data, workflow, secrets and dependency checks. The semantic CI tests also exercise
changed-path selection and the fail-closed aggregate gate. Consult the executable workflow for exact
commands, and `current-status.md` for dated counts.

Known coverage gaps retain their existing closure contracts: real Tika OCR behavior
([#592](https://github.com/CoJoA13/EasySynQ/issues/592)), a live Celery/Beat and long-lived SSE proof
([#598](https://github.com/CoJoA13/EasySynQ/issues/598)), integration order dependence
([#567](https://github.com/CoJoA13/EasySynQ/issues/567)), and the browser harness race
([#595](https://github.com/CoJoA13/EasySynQ/issues/595)). Sharded green runs do not close these gaps.

CI does not publish release assets or deploy a site. Version tags run the full test matrix and the
digest-pin release check; operators still build the air-gap bundle and follow the release/install
runbooks. An automatic deployment would not supply the missing source-independent recovery proof
([#557](https://github.com/CoJoA13/EasySynQ/issues/557)). Release publishing, provenance/attestations and
environment approval gates need a defined artifact and deployment contract before being enabled.

The live `main` ruleset requires a PR and resolved conversations but **zero approving reviews**.
Review remains a contributor obligation rather than an enforced independent approval. Requiring an
approval needs an eligible reviewer other than the PR author; do not silently impose that on a
single-maintainer repository. CODEOWNERS, a merge queue, Discussions and external webhooks should be
adopted for a concrete owner/team workflow, not enabled merely because they exist.

## Optional integrations and observability

The `ci` workflow and Dependabot need no app, webhook or integration. Enable an external integration
or webhook only for an actual workflow with a selected recipient, minimal events, and a tested
delivery path. An empty integration list is a valid configuration. AI review of pull requests has
no configured replacement for the retired GitLab Duo review and is out of scope for R86.

CI status and deployed-application health are separate. EasySynQ supplies structured JSON/stdout
logs, `/healthz`, dependency readiness at `/readyz`, Compose health, and configured out-of-band
alarm channels. Follow the
[administrator monitoring procedures](../manuals/administrator-it-manual.md#8-health-logs-and-monitoring).
A bundled Prometheus/Grafana/Loki overlay is still reserved architecture.

## The archived GitLab project

`gitlab.com/synqsuite-group/EasySynQ` hosted the project from 2026-09-08 until R86. It is
**archived, not deleted** (owner action: Settings > General > Advanced > Archive project), after the
migration pull request has merged and `git rev-parse origin/main` agrees on both remotes. Archiving
makes the project read-only while its 64 merge requests, pipelines, jobs and issues stay readable,
so the hundreds of dated `gitlab.com` evidence URLs in `current-status.md`, `slice-history.md` and
`open-residuals.md` keep resolving. Do not rewrite those links, and do not delete the project.

For the record, the GitLab-era settings that this runbook previously documented were: protected
`main` (no direct or force pushes, Maintainers merge), the successful-pipeline and resolved-discussion
merge checks with skipped pipelines rejected, protected `v*` tags, and a weekly protected-branch
Renovate schedule authenticated by a masked project variable `RENOVATE_TOKEN`. That token has no
consumer after R86; it needs no rotation and must not be re-created. The GitLab wiki was a navigation
index only. Nothing there is kept in sync once CI moves.

References: [rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets),
[required status checks](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/defining-the-mergeability-of-pull-requests/about-protected-branches#require-status-checks-before-merging),
[Dependabot version updates](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file),
and [archiving a GitLab project](https://docs.gitlab.com/user/project/working_with_projects/#archive-a-project).
