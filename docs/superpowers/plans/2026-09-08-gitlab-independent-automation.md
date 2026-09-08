# GitLab independent automation implementation plan

> **For agentic workers:** Execute the CI and contributor tasks within their assigned file boundaries, then obtain an independent review of the combined change.

**Goal:** Run repository automation and supported contributor setup without GitHub services, hosting, credentials, or download fallbacks.

**Architecture:** Keep GitLab as the repository, CI, issue, and merge-request platform. Obtain tools from their official npm, PyPI, Docker Hub, Docker, Node.js, and Astral distributions; explicitly disable GitHub requests and automatic tool downloads in Renovate.

**Tech stack:** GitLab CI, Renovate, Bash, Python 3.12, Node 26, uv.

**Spec:** Owner instruction on 2026-09-08: no GitHub services or hosting; adjustments to repository setup are authorized. This changes distribution and automation paths, not application dependency versions or merge acceptance.

## Constraints

- Preserve Python 3.12, Node 26, Ubuntu 26.04/x86_64, frozen locks, non-root tests, offline image startup, and existing security gates.
- Keep protected main, protected credentials, weekly scheduling, reviewed MRs, and disabled dependency automerge.
- Preserve passive source attribution and historical evidence. Local security policy under `.github` remains active data.
- Do not merge dependency updates merely because Renovate can create a branch or exits successfully.

## Task 1: CI and Renovate

- [ ] Add executable checks for GitHub-free CI distribution paths, disabled Renovate GitHub hosts/managers, and preinstalled package-manager use; demonstrate failure on the baseline.
- [ ] Use Python Docker Hub images plus pinned PyPI uv wheels. Install Compose from Docker's package repository and Trivy from its official Docker Hub image with explicit non-GitHub databases.
- [ ] Install pinned Renovate from npm on its supported Node runtime; run npm updates with the project's Node 26 and uv updates with Python 3.12. Use `binarySource: global`, disabled GitHub host rules, and no changelog fetching.
- [ ] Track `infra/images.lock` with Renovate so container updates can change the Compose reference and air-gap image manifest together.
- [ ] Run focused shell guards, strict Renovate validation, and the full GitLab pipeline, including the API image startup proof.
- [ ] Run the protected main schedule and inspect actual dependency extraction, lockfile refreshes, lookup errors, and generated MRs.

## Task 2: Contributor paths

- [ ] Replace external pre-commit clones with equivalent local hooks using official non-GitHub distributions; preserve secret scanning.
- [ ] Replace GitHub CLI instructions and the timing-artifact downloader with GitLab equivalents; preserve selection of one successful pipeline and rejection of overlapping shards.
- [ ] Update doctor/setup guidance to install uv from PyPI and use Astral's explicit mirror-only setting for managed Python.
- [ ] Correct active clone instructions to GitLab; run relevant contributor and authority guards.

## Integration and handoff

- [ ] Update the setup runbook, current status, residual closure contract, and dated history with the owner decision and fresh evidence.
- [ ] Review the complete diff independently, pass required GitLab CI, and merge the setup MR through protected main.
- [ ] Report dependency MRs separately: refreshed locks do not bypass deliberate version-review tests or justify automatic major upgrades.
