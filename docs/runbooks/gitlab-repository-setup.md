# GitLab repository setup

The active repository is [synqsuite-group/EasySynQ](https://gitlab.com/synqsuite-group/EasySynQ).
GitLab settings complement `.gitlab-ci.yml`; they are not stored in Git. Check both when moving
the project or diagnosing a green pipeline that did not exercise the intended job.

## Branches, merges and releases

- Default branch: `main`, protected, with **Allowed to push and merge: No one**, **Allowed to
  merge: Maintainers**, and force pushes disabled.
- Merge checks: **Pipelines must succeed** and **All discussions must be resolved** enabled;
  **Skipped pipelines are considered successful** disabled.
- Release tags: protect `v*`, with **Allowed to create: Maintainers**. A protected tag still
  needs its `release-gate` job to pass; tag protection is not release acceptance.
- Work on scoped branches and use reviewed merge requests. GitLab gates the whole pipeline;
  the old GitHub aggregator check names do not need recreating.

## Renovate

Use a project access token with the `api` scope and a bot role compatible with the branch merge
policy (Maintainer for this project). Store it only in the project CI/CD variable `RENOVATE_TOKEN`:
**masked**, **protected**, type **Variable**, environment scope `*`. Disable variable expansion.
Project variables already reach eligible jobs; do not add a YAML self-reference for the token.
Keep the schedule on protected `main` so the protected secret is available.

The active weekly schedule is `0 5 * * 1`, timezone `America/Chicago`, targeting `main`.
`renovate.json` owns update grouping and version ceilings; automerge is disabled. The GitLab
schedule is the sole timing control, so avoid a second Renovate schedule that silently skips work.

The owner-selected setup uses no GitHub services, hosting, credentials, or download fallbacks.
`RENOVATE_TOKEN` is the only platform credential needed. `scripts/run-renovate.sh` installs the
pinned bot from npm, uses its supported Node 24 runtime, and supplies Node 26/npm plus uv and
Python 3.12 for updating this project's locks. Node binaries come from nodejs.org with checksum
verification; uv comes from PyPI and Python is supplied by the Docker Hub base image. Managed
Python downloads are disabled in CI.

`renovate-self-hosted.cjs` selects preinstalled tools (`binarySource: global`), blocks GitHub hosts,
and passes only the named Python settings to child package managers. `renovate.json` disables
release-note fetching and the retired GitHub Actions manager. Python interpreter-version metadata
also uses GitHub, so interpreter upgrades remain deliberate; Python Docker base updates within
3.12 still flow. npm, PyPI, container registries, and Node's distribution service supply updates.
Passive upstream source links may still appear in MR descriptions; those links do not move the
repository or create a GitHub integration.

A regex manager tracks `infra/images.lock` alongside Compose references, so container updates can
preserve the air-gap image manifest. Other regex managers keep the new uv, Renovate, and Node tool
pins visible to the updater. Every dependency change still requires review and a successful pipeline.

After changing the token or pipeline, run the schedule and inspect the **renovate job itself**.
A passing branch pipeline cannot validate it because the updater runs only for scheduled pipelines.
Check for dependency extraction, lookup warnings and lockfile/artifact errors as well as its exit
status. The first authenticated run opened merge requests despite failing tool lookups and leaving
some lockfiles unrefreshed. A successful job or an open update MR alone does not prove automation
is fully operational. See `RES-RENOVATE-GITHUB-METADATA` in the residual ledger.
The `renovate-config` job validates repository and self-hosted configuration on ordinary branch pipelines as well
as schedules. Keep explanatory prose in this runbook: arbitrary JSON keys such as `_comment` are
invalid options. Renovate may report a repository config error and still exit successfully, which
is why the separate strict validator is required.

An empty-token diagnostic means to check protection, environment scope and variable presence.
Do not unprotect the credential to get past it. An authentication error with a present token
requires checking expiry, revocation, `api` scope and the effective platform/endpoint settings.
Never echo the token or place it in a command argument, commit, screenshot or report.

Review the token expiry in Settings > Access tokens and rotate it before expiration, immediately
replacing the CI/CD value while preserving masking and protection. Re-run the schedule afterward.
Do not treat an active-token listing as proof that the stored CI value or job is working.

## Security evidence

The successful-pipeline requirement includes `security`. Its npm high/critical audit gate and
scanner operational failures block merging. **pip-audit and Trivy findings are report-only**,
so a green job is not a clean-image assertion. Review the actual reports before release.
The built API image is scanned; the current web command scans its final base image, not a built
web application image. Current follow-up work belongs in
[`../open-residuals.md`](../open-residuals.md#res-container-security-triage).

References: [GitLab variable precedence and protection](https://docs.gitlab.com/ci/variables/),
[protected branches](https://docs.gitlab.com/user/project/repository/branches/protected/), and
[Renovate GitLab authentication](https://docs.renovatebot.com/modules/platform/gitlab/).
[Preinstalled Renovate tools](https://docs.renovatebot.com/self-hosted-configuration/#binarysource)
and [host rules](https://docs.renovatebot.com/configuration-options/#hostrules) describe the deployment controls.
