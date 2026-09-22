# GitHub repository setup

The active repository is [CoJoA13/EasySynQ](https://github.com/CoJoA13/EasySynQ) (R86). GitHub
settings complement `.github/workflows/ci.yml`; they are not stored in Git. Check both when moving
the project or diagnosing a green check that did not exercise the intended job.

## Branches, merges and releases

Rulesets, not classic branch protection, govern `main` (Settings > Rules > Rulesets). The `main`
ruleset carries:

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
  on.

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

Actions are enabled for the repository with the `ci` workflow only. Workflow permissions are
read-only for `contents` (the workflow declares its own `permissions:`); do not raise the
repository default. Pull requests from forks run with a read-only token and no secrets, which is
sufficient because the gate needs none.

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

Dependabot security updates and alerts may stay enabled; they open pull requests through the same
gate. There is no scheduled pipeline to inspect after a settings change; verify the configuration by
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

Enable **private vulnerability reporting** (Settings > Code security) so `CONTRIBUTING.md`'s
security-reporting instructions have a destination.

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
