# Codex Fedora Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `S-codex-fedora-foundation`: vendor-neutral repository ownership, deterministic Fedora Workstation bootstrap and diagnostics, and a genuinely least-privilege, reproducible local PostgreSQL MCP path without changing application or production behavior.

**Architecture:** Repository authority is split into stable contributor guidance (`AGENTS.md`), one structured execution snapshot (`docs/current-status.md`), one current residual ledger (`docs/open-residuals.md`), and historical evidence (`docs/slice-history.md`); executable gates prevent those roles drifting. A dependency-light shell doctor drives an explicit Fedora package bootstrap and a disposable libvirt Fedora proof. The optional MCP connector runs only through a locked local Node installation and a dev-overlay-only PostgreSQL role whose grants, role attributes, and prohibited operations are integration-tested.

**Tech Stack:** Bash 5, Git, Fedora 44/DNF, Node.js 22/npm, uv-managed CPython 3.12, Docker Engine with Compose 2.24.4+, PostgreSQL 16, libvirt/virt-install, GitHub Actions, pytest, Node test runner, pre-commit, Markdown.

## Global Constraints

- Baseline is `main` at `c15541f`, the squash merge of PR #447.
- This slice changes no application behavior, database schema, production Compose topology, production configuration, or security guarantee.
- Keep the Ubuntu production-host bootstrap and all live Ubuntu links unchanged except for neutral authority-link corrections.
- Keep `.claude/`; slim `CLAUDE.md` only after all executable and prose consumers have moved and passed their contracts.
- Product authority stays in `docs/decisions-register.md` and approved slice designs. `AGENTS.md` owns only cross-agent contributor workflow.
- Every mutable fact has one canonical home: current slice, migration snapshot, suite baseline, and CI topology in `docs/current-status.md`; decision range in `docs/decisions-register.md`; permission catalog in `docs/07-authorization-model.md` plus its executable authorization test.
- `alembic heads` is runtime truth; the migration value in `docs/current-status.md` is a dated coordination snapshot.
- The current residual ledger uses stable `RES-*` identifiers. Historical slice prose remains evidence and is not bulk-rewritten.
- Fedora developer support targets Fedora Workstation 44 on x86_64 first. Docker Engine is supported; Podman compatibility is not claimed.
- The bootstrap is non-mutating by default, enumerates commands before privilege elevation, never starts services, changes firewalld, disables SELinux, or adds a user to the `docker` group.
- Preserve the system Python. EasySynQ Python is CPython 3.12 installed and selected by uv.
- Node.js major 22 is selected through tracked `.node-version`; Python remains `>=3.12,<3.13`.
- Compose must be Docker Compose 2.24.4 or newer.
- Doctor exit codes are `0` ready for the selected profile, `1` selected-profile blockers, and `2` invalid invocation or internal contract failure.
- Doctor output never prints secret values; tests cover missing, placeholder, and configured states with synthetic values.
- Do not install project dependencies during fixture/unit tests. The disposable Fedora proof is the only automation in this slice allowed to hydrate the full toolchain and project dependencies.
- The MCP path is opt-in, local-development-only, forbidden for production/site data, never uses owner credentials, and never falls back to `npx` or an unpinned package fetch.
- Pin `@modelcontextprotocol/server-postgres` to `0.6.2` in a private lockfile while documenting that the package is deprecated and replacement is a named follow-up; do not silently upgrade or substitute it during this slice.
- Shell tests use isolated temporary fixtures/stub `PATH`s and must not inspect or mutate the contributor's real host state.
- All tasks land in one atomic PR; task commits are review checkpoints, not independently mergeable partial authority states.

---

## File map and locked interfaces

| Boundary | Files | Responsibility |
|---|---|---|
| Repository authority | `AGENTS.md`, `docs/current-status.md`, `docs/open-residuals.md`, `docs/slice-history.md`, `CLAUDE.md` | Non-overlapping stable guidance, current snapshot, current residuals, history, and Claude compatibility. |
| Authority enforcement | `scripts/check-repo-authority.sh`, `scripts/repo-authority-live-paths.txt`, `scripts/tests/test-agent-authority.sh`, `scripts/tests/test-claude-hooks.sh` | Global negative scan, reviewed live-consumer manifest, fixture proofs, and hook behavior. |
| Host diagnostics | `scripts/doctor.sh`, `scripts/tests/test-doctor.sh`, `justfile` | Read-only profile-aware checks with stable reason IDs. |
| Fedora setup | `.node-version`, `scripts/bootstrap-fedora-dev.sh`, `scripts/tests/test-bootstrap-fedora-dev.sh`, `docs/runbooks/fresh-linux-setup.md` | Check-first DNF/bootstrap flow and exact operator guidance. |
| Fedora acceptance | `infra/dev/fedora-proof/ks.cfg`, `scripts/run-fedora-proof.sh`, `scripts/inside-fedora-proof.sh`, `scripts/tests/test-fedora-proof-contract.sh`, `docs/runbooks/fedora-proof.md` | Disposable local libvirt Workstation proof, SELinux-enforcing checks, second-run idempotence, and bounded repository verification. |
| MCP dependency | `tools/mcp-postgres/package.json`, `tools/mcp-postgres/package-lock.json`, `scripts/run-postgres-mcp.sh` | Exact optional package resolution with no network-on-launch behavior. |
| MCP database boundary | `infra/compose/postgres/mcp-readonly-init.sh`, `infra/compose/compose.mcp.yml`, `.mcp.json`, `.env.example` | Dev-only role provisioning, loopback publication, and MCP-only credential injection. |
| CI and dependency upkeep | `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.pre-commit-config.yaml`, `apps/api/tests/unit/test_ci_workflow.py`, `scripts/tests/test-ci-hardening.sh` | Required fast contracts and locked-tool maintenance. |

The following interfaces are fixed across tasks:

```text
scripts/check-repo-authority.sh
  arguments: none
  stdout: one PASS summary or deterministic failures whose defined reason ID starts with AUTHORITY_
  exits: 0 clean, 1 drift, 2 malformed manifest/internal contract

scripts/doctor.sh [contributor|test|stack]
  default profile: contributor
  output line fields: STATE, stable REASON_ID, and human guidance separated by one space
  STATE: PASS | WARN | FAIL | UNVERIFIED
  exits: 0 selected profile ready, 1 selected profile blocked, 2 usage/internal

scripts/bootstrap-fedora-dev.sh [--check|--apply]
  default mode: --check
  --check: no mutations; print missing packages and exact next command
  --apply: print complete transaction, obtain explicit terminal confirmation, then use sudo dnf
  exits: 0 verified, 1 missing/failed prerequisite, 2 unsupported invocation/platform

scripts/run-fedora-proof.sh --iso /absolute/path/Fedora-Workstation-Live-x86_64-44-1.4.iso
  creates one disposable libvirt VM under a mktemp-owned directory
  destroys only the exact VM/disk it created after target validation

scripts/run-postgres-mcp.sh
  execs tools/mcp-postgres/node_modules/.bin/mcp-server-postgres with one MCP-only DSN
  exits before launch if package files or MCP credentials are absent
```

### Task 1: Establish executable repository-authority contracts

**Files:**
- Create: `scripts/repo-authority-live-paths.txt`
- Create: `scripts/check-repo-authority.sh`
- Create: `scripts/tests/test-agent-authority.sh`
- Create: `scripts/tests/test-claude-hooks.sh`
- Modify: `.github/workflows/ci.yml:16-49`
- Modify: `.pre-commit-config.yaml`
- Modify: `scripts/tests/test-ci-hardening.sh:188-235`
- Modify: `apps/api/tests/unit/test_ci_workflow.py:104-150`
- Modify: `justfile`

**Interfaces:**
- Consumes: tracked repository text plus the authority roles and CLI contract in the file map.
- Produces: `./scripts/check-repo-authority.sh`, `just authority-check`, a complete reviewed live-path manifest, and fixture helpers reused by Tasks 2 and 3.

- [ ] **Step 1: Write the authority RED fixtures**

Create a harness that copies only named fixture files into a temporary Git repository, runs the guard with `AUTHORITY_ROOT` set to that fixture, and asserts exact reason identifiers:

```bash
run_bad duplicate_status_key AUTHORITY_DUPLICATE_STATUS_KEY
run_bad claude_current_heading AUTHORITY_CLAUDE_CURRENT_OWNER
run_bad slice_history_head AUTHORITY_HISTORY_MUTABLE_HEAD
run_bad live_claude_reference AUTHORITY_LIVE_CLAUDE_OWNER
run_bad duplicate_residual_id AUTHORITY_DUPLICATE_RESIDUAL_ID
run_bad unresolved_residual_ref AUTHORITY_UNKNOWN_RESIDUAL_ID
run_good neutral_authority_split
```

The good fixture contains one frontmatter block with these keys exactly once and ASCII integer values where numeric:

```yaml
easysynq_status_schema: 1
as_of: "2026-08-08"
baseline_commit: "c15541f"
last_shipped_slice: "S-upload-identity"
migration_head: "0085"
next_migration: "0086"
api_unit_tests: 1686
web_test_files: 249
web_tests: 1468
contract_tests: 283
integration_passed: 1051
integration_skipped: 2
ci_jobs: 10
ci_checks: 14
```

- [ ] **Step 2: Run the new tests against the current tree and record the expected RED**

Run:

```bash
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
```

Expected: fixture unit cases pass, while the live-tree cases fail because the three neutral documents do not exist, `CLAUDE.md` owns current facts, `docs/slice-history.md` owns a migration head, and `test-baseline.sh` emits no baseline.

- [ ] **Step 3: Implement the guard and manifest**

The manifest must enumerate every current consumer reviewed in this slice, one path per line, including root docs, numbered docs, manuals/runbooks, active `.claude/{agents,commands,hooks}`, and the current source/test comments found by `git grep`. The guard must scan beyond the manifest so a newly introduced consumer cannot evade it:

```bash
CURRENT_PATHS=(README.md AGENTS.md CLAUDE.md apps .claude docs/00-overview.md \
  docs/16-roadmap.md docs/17-gaps-and-open-questions.md \
  docs/18-mvp-implementation-plan.md docs/dev-workflow.md docs/manuals docs/runbooks)
HISTORICAL_EXCLUDES=(docs/superpowers docs/audit-2026-06-17.md \
  docs/review-2026-07-22.md docs/slice-history.md)
```

Implement explicit checks for required authority declarations, exact frontmatter keys, forbidden `CLAUDE.md` headings/facts, forbidden mutable head/open-ledger content in slice history, live claims that CLAUDE owns current status/head/residuals/rules, duplicate `RES-*` headings, unresolved current `RES-*` references, and hard-coded `R1–RNN` mirrors outside the decision register. Historical paths are excluded by exact path, not broad filename substring.

- [ ] **Step 4: Add required local and CI gates**

Add `just authority-check`, a local pre-commit hook calling the script, and this hard-fail contracts step before dependency hydration:

```yaml
- name: Agent authority and Claude compatibility contracts
  run: |
    bash scripts/tests/test-agent-authority.sh
    bash scripts/tests/test-claude-hooks.sh
    ./scripts/check-repo-authority.sh
```

Extend both CI structural tests to reject `continue-on-error`, `|| true`, or movement after Node setup.

- [ ] **Step 5: Prove the fixture implementation passes while live-tree RED remains diagnostic**

Run:

```bash
bash -n scripts/check-repo-authority.sh scripts/tests/test-agent-authority.sh scripts/tests/test-claude-hooks.sh
bash scripts/tests/test-agent-authority.sh --fixtures-only
```

Expected: fixture suite passes. Do not claim the live authority gate is green before Tasks 2 and 3.

- [ ] **Step 6: Commit the contract checkpoint**

```bash
git add scripts/repo-authority-live-paths.txt scripts/check-repo-authority.sh \
  scripts/tests/test-agent-authority.sh scripts/tests/test-claude-hooks.sh \
  .github/workflows/ci.yml .pre-commit-config.yaml \
  scripts/tests/test-ci-hardening.sh apps/api/tests/unit/test_ci_workflow.py justfile
git commit -m "test: define repository authority contracts"
```

### Task 2: Perform the atomic authority and residual migration

**Files:**
- Create: `AGENTS.md`
- Create: `docs/current-status.md`
- Create: `docs/open-residuals.md`
- Modify: `docs/slice-history.md:1-192`
- Modify: `README.md:109-149`
- Modify: `docs/00-overview.md:53-80,200-208`
- Modify: `docs/16-roadmap.md:55,131-132`
- Modify: `docs/17-gaps-and-open-questions.md:6`
- Modify: `docs/18-mvp-implementation-plan.md:4-45,311,391`
- Modify: `docs/dev-workflow.md:10`
- Modify: `docs/manuals/00-index.md:26`
- Modify: `docs/runbooks/fresh-linux-setup.md:122`
- Modify: `docs/decisions-register.md:1799-1800`
- Modify: `apps/api/src/easysynq_api/api/clauses.py:1-8`
- Modify: `apps/api/src/easysynq_api/services/similarity/detector.py:1-18`
- Modify: `apps/api/src/easysynq_api/tasks/backup.py:1-10`
- Modify: `apps/api/src/easysynq_api/services/ingestion/service.py:421`
- Modify: `apps/api/src/easysynq_api/api/audit.py:1-10,225-229`
- Modify: `apps/api/tests/integration/test_mgmt_review_pack.py:1-4`
- Modify: `apps/api/tests/integration/test_nfr_smoke.py:1-7`
- Modify: `apps/api/tests/unit/test_dependency_tooling.py:166-184`

**Interfaces:**
- Consumes: Task 1 guard contract and the approved design authority split.
- Produces: the neutral contributor guide, execution snapshot, residual ledger, and migrated non-Claude consumers; Task 3 performs the final compatibility cutover and slims `CLAUDE.md`.

- [ ] **Step 1: Add RED content assertions for the exact authority split**

Extend `test-agent-authority.sh` so the live tree must satisfy:

```text
AGENTS.md: stable commands, security links, migration rules, generated-file rules, handoff rules
docs/current-status.md: exact structured snapshot and no residual records
docs/open-residuals.md: stable ID/schema and no migration/test/CI snapshot
docs/slice-history.md: history-only header, no current head, no OPEN section
CLAUDE.md after Task 3: compatibility pointers and Claude-only behavior, no product/current facts
```

Assert current code/docs can reference `CLAUDE.md` only as a compatibility file, never as authority.

- [ ] **Step 2: Write the three authority homes**

Use the exact status frontmatter from Task 1. `AGENTS.md` links rather than copies mutable facts and includes these stable sections:

```markdown
## Authority and precedence
## Repository map
## Supported contributor workflow
## Tests and evidence
## Security and site-data boundaries
## Migrations and generated files
## Documentation truth
## Change handoff
## Tool-specific compatibility
```

`docs/open-residuals.md` defines each record as `## RES-…`, with `Status: OPEN`, owner/source, reason, closure contract, and last-reviewed date. Migrate the nine owner-acknowledged open records from the top of slice history verbatim under these IDs:

```text
RES-INGEST-PROGRESS
RES-INGEST-PARTIAL-OPTIN
RES-R10-RECONSTRUCTION
RES-CAPA-REJECT
RES-AUDIT-CHECKPOINT-LINEAGE
RES-AUDIT-VERIFY-ORCHESTRATOR
RES-AUDIT-LONG-SCOPE-REF
RES-UPGRADE-LOCK-TIMEOUT
RES-AUDIT-KEY-ROTATION
```

Add the three live-code residuals already expressed outside that top ledger so no current open claim is orphaned:

```text
RES-RISK-CLAUSE-PICKER
RES-RESTORE-SCRATCH-WORM-GUARD
RES-AUDIT-EXPORT
```

Treat older `Named residuals` prose inside dated slice entries as historical snapshot evidence. Keep the two struck-through closed items and the `S-upload-identity` completion record in slice history. Replace the former top section with a short index link to `docs/open-residuals.md` and link code comments to their stable IDs.

- [ ] **Step 3: Migrate every live prose and code consumer atomically**

Apply the exact file inventory above. Remove stale R63/R46 mirrors and use “all registered decisions” outside the register. Change R61 back-propagation to `AGENTS.md`. Point migration-writing guidance to `alembic heads`, with `docs/current-status.md` described only as a snapshot. Point permission-catalog guidance directly to `docs/07-authorization-model.md` and its executable catalog test.

- [ ] **Step 4: Preserve recent-learning evidence before the Task 3 cutover**

For all 13 current `Recent learnings` bullets, add or verify one direct slice-history/PR/SHA evidence location. Move only generally reusable engineering traps absent from `.claude/rules/engineering-patterns.md` into that patterns document; do not copy the 13-bullet feed into current status. Keep `CLAUDE.md` intact until Task 3 has migrated its executable consumers.

- [ ] **Step 5: Run the partial migration diagnostics**

Run the content-specific tests while explicitly expecting the remaining failures to name only `CLAUDE.md` and the executable `.claude` consumers assigned to Task 3:

```bash
bash scripts/tests/test-agent-authority.sh --neutral-docs-only
bash scripts/check-no-site-data.sh AGENTS.md docs/current-status.md docs/open-residuals.md
./scripts/check-repo-authority.sh || test "$?" -eq 1
```

Expected: neutral-document and site-data checks pass. The diagnostic guard exits 1 only for the deliberately uncut Claude compatibility boundary; any other reason blocks progress.

- [ ] **Step 6: Commit the neutral-document checkpoint**

```bash
git add AGENTS.md README.md docs .claude/rules/engineering-patterns.md \
  apps/api/src apps/api/tests/integration apps/api/tests/unit/test_dependency_tooling.py
git commit -m "docs: establish vendor-neutral repository authority"
```

### Task 3: Migrate and prove Claude hook and command behavior

**Files:**
- Modify: `.claude/hooks/test-baseline.sh`
- Modify: `.claude/hooks/register-range-guard.sh`
- Modify: `.claude/hooks/site-data-guard.sh:10`
- Modify: `.claude/commands/finish-slice.md`
- Modify: `.claude/commands/check-migrations.md`
- Modify: `.claude/commands/new-notification-event.md`
- Modify: `.claude/agents/docs-drift-reviewer.md`
- Modify: `.claude/agents/diff-critic.md`
- Modify: `.claude/claude-security-guidance.md`
- Modify: `CLAUDE.md`
- Test: `scripts/tests/test-claude-hooks.sh`

**Interfaces:**
- Consumes: Task 2 current-status frontmatter, residual IDs, register self-range, and historical slice contract.
- Produces: hooks that parse neutral authority only and commands that update the correct three documents.

- [ ] **Step 1: Extend executable hook fixtures**

Add six cases using temporary repositories:

```text
valid status frontmatter -> baseline output contains api=1686 and web=1468
conflicting parseable CLAUDE bullet -> ignored
missing/malformed/duplicate status key -> silent, never guessed
register gains R65 but self-range remains R64 -> warning
register and self-range both R65 while fixture CLAUDE says R64 -> silent
finish-slice contract -> writes status/history/residuals, never CLAUDE current/recent or history head
```

- [ ] **Step 2: Run the old hooks and verify the focused RED**

Run:

```bash
bash scripts/tests/test-claude-hooks.sh
```

Expected: baseline and register independence cases fail against the old scripts.

- [ ] **Step 3: Retarget the hooks**

`test-baseline.sh` reads exact frontmatter keys from `docs/current-status.md`, strips no commas because commas are invalid, and emits nothing on ambiguity. `register-range-guard.sh` compares the highest `## RNN` heading with the register's two self-declarations only. Neither reads `CLAUDE.md`.

- [ ] **Step 4: Retarget command and reviewer instructions**

`finish-slice.md` appends shipped narrative to history, updates current snapshot/baselines, closes or adds stable residual IDs, and promotes recurring traps only to engineering patterns. Migration commands call `alembic heads`. Docs review obtains migration/test/CI facts from current status, decision range from the register, and permission count from the authorization spec/executable catalog.

- [ ] **Step 5: Slim `CLAUDE.md` only after the executable consumers pass**

Its retained structure is limited to:

```markdown
# EasySynQ Claude compatibility
Read AGENTS.md before work. Current execution state is in docs/current-status.md; current residuals are in docs/open-residuals.md.
## Claude hooks and commands
## Claude memory behavior
```

List active `.claude` hook/command locations, session-start behavior, and Claude-specific memory conventions. Remove product invariants, repository-map duplication, current status, recent learnings, migration/test/CI/permission/decision counts, and residual ownership.

- [ ] **Step 6: Run hook, syntax, settings-wiring, and complete authority proofs**

Run:

```bash
bash -n .claude/hooks/*.sh scripts/tests/test-claude-hooks.sh
bash scripts/tests/test-claude-hooks.sh
./scripts/check-repo-authority.sh
```

Expected: all pass and `.claude/settings.json` still wires both affected hooks.

- [ ] **Step 7: Commit the compatibility cutover**

```bash
git add .claude CLAUDE.md scripts/tests/test-claude-hooks.sh
git commit -m "fix: retarget Claude automation to neutral authority"
```

### Task 4: Build the deterministic repository doctor

**Files:**
- Create: `scripts/doctor.sh`
- Create: `scripts/tests/test-doctor.sh`
- Modify: `justfile`
- Modify: `docs/dev-workflow.md`

**Interfaces:**
- Consumes: `.node-version`, `apps/api/pyproject.toml`, `scripts/require-compose-version.sh`, `.env.example`, and repository dependency-directory conventions.
- Produces: `scripts/doctor.sh [contributor|test|stack]`, `just doctor`, and `just doctor stack`.

- [ ] **Step 1: Write table-driven doctor RED cases**

Create stub binaries and synthetic files under a temporary root. Cover every required state/reason pair:

```text
OS_UNSUPPORTED, ARCH_UNSUPPORTED, SELINUX_DISABLED, SELINUX_UNVERIFIED
TOOL_MISSING_GIT, TOOL_MISSING_CURL, TOOL_MISSING_OPENSSL
NODE_MISSING, NODE_PATH_SHADOWED, NODE_UNSUPPORTED_VERSION, UV_MISSING, PYTHON_312_MISSING
JUST_MISSING, PRECOMMIT_MISSING, PG_DUMP_MISSING, PG_DUMP_UNSUPPORTED_VERSION
DOCKER_CLI_MISSING, DOCKER_COMPOSE_MISSING, DOCKER_COMPOSE_UNSUPPORTED_VERSION
DOCKER_SOCKET_MISSING, DOCKER_DAEMON_STOPPED, DOCKER_SOCKET_PERMISSION
DOCKER_GROUP_SESSION_INACTIVE, DOCKER_DAEMON_UNREACHABLE
API_DEPS_MISSING, WEB_DEPS_MISSING, CONTRACT_DEPS_MISSING
ENV_MISSING, ENV_PLACEHOLDER_SECRET, PORT_OCCUPIED, PORT_OWNED_BY_STACK, SELINUX_LABEL_UNVERIFIED
```

For every Docker category, assert it is reported distinctly; contributor exits 0 when only Docker/stack blockers exist, while test/stack exit 1 when their prerequisites fail.

- [ ] **Step 2: Add a secret non-disclosure falsifier**

Write `.env` with `POSTGRES_PASSWORD=DOCTOR_SENTINEL_9d77` and assert neither stdout nor stderr contains `DOCTOR_SENTINEL_9d77`. Assert only the key name and `ENV_PLACEHOLDER_SECRET` appear.

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
bash scripts/tests/test-doctor.sh
```

Expected: fails because `scripts/doctor.sh` does not exist.

- [ ] **Step 4: Implement the dependency-light doctor**

Use Bash plus `command -v`, `/etc/os-release`, `uname -m`, `getenforce`, `stat`, `id`, `/proc/net/tcp{,6}`, and tool `--version` output. When the active `node` is not major 22 but Fedora's `/usr/bin/node` is major 22, report `NODE_PATH_SHADOWED` and the exact current-session `PATH=/usr/bin:$PATH` remedy rather than calling the package missing. Add test seams only through explicit variables:

```bash
DOCTOR_ROOT=${DOCTOR_ROOT:-/}
DOCTOR_PATH=${DOCTOR_PATH:-$PATH}
DOCTOR_PROC_ROOT=${DOCTOR_PROC_ROOT:-/proc}
DOCTOR_DOCKER_SOCKET=${DOCTOR_DOCKER_SOCKET:-/var/run/docker.sock}
```

Reject any override when not running under `DOCTOR_TEST_MODE=1`. Never source `.env`; parse key names and compare values against the known `.env.example` placeholders without echoing them.

Profile blockers are exact:

```text
contributor: supported Fedora/Ubuntu, architecture, git, curl, openssl, Node 22, uv, Python 3.12, just, pre-commit, pg_dump 16
test: contributor + API/web/contracts dependencies + Docker CLI/Compose/daemon/socket access
stack: test + .env present/non-placeholder + project ports free or attributable to the current EasySynQ Compose project + SELinux bind-label verification
```

Always print all detected states, but calculate the exit verdict only from the selected profile. A foreign listener is `FAIL PORT_OCCUPIED`; a listener mapped by `docker compose ps` to the current EasySynQ project is `PASS PORT_OWNED_BY_STACK`.

- [ ] **Step 5: Add the just alias and documentation**

```make
doctor profile="contributor":
    ./scripts/doctor.sh "{{ profile }}"
```

Document direct-script use first so absence of `just` can still be diagnosed.

- [ ] **Step 6: Run all doctor contracts**

Run:

```bash
bash -n scripts/doctor.sh scripts/tests/test-doctor.sh
bash scripts/tests/test-doctor.sh
./scripts/doctor.sh contributor
```

Expected: fixtures pass. The real-host command may exit 1 but must report truthful stable reasons and exact next commands without mutation.

- [ ] **Step 7: Commit**

```bash
git add scripts/doctor.sh scripts/tests/test-doctor.sh justfile docs/dev-workflow.md
git commit -m "feat: add profile-aware repository doctor"
```

### Task 5: Add the Fedora Workstation developer bootstrap

**Files:**
- Create: `.node-version`
- Create: `scripts/bootstrap-fedora-dev.sh`
- Create: `scripts/tests/test-bootstrap-fedora-dev.sh`
- Modify: `docs/runbooks/fresh-linux-setup.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 4 doctor and the official Fedora/Docker package repositories.
- Produces: a check-first, explicit-confirmation Fedora 44 bootstrap that ends at the contributor doctor.

- [ ] **Step 1: Write bootstrap fixture RED cases**

Stub `dnf`, `rpm`, `sudo`, `uname`, and `/etc/os-release`. Verify:

```text
default and --check execute no mutating command
non-Fedora and unsupported Fedora release exit 2 with exact guidance
second --apply sees every package installed and performs no package transaction
missing packages produce one complete preview before sudo
nodejs22-bin/nodejs22-npm-bin provide unversioned Fedora node/npm while an earlier PATH Node is reported as shadowing, not missing
declining confirmation performs no transaction
--apply never calls systemctl, usermod, groupmod, firewall-cmd, setenforce, or edits /etc
successful flow ends with scripts/doctor.sh contributor
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
bash scripts/tests/test-bootstrap-fedora-dev.sh
```

Expected: fails because the bootstrap and Node pin do not exist.

- [ ] **Step 3: Implement detection and check mode**

Support Fedora 44/x86_64, read `/etc/os-release` without executing it, and inspect exact RPM package boundaries. The Fedora package set is:

```text
git curl openssl dnf-plugins-core nodejs22 nodejs22-bin nodejs22-npm nodejs22-npm-bin
uv just pre-commit postgresql16
```

Docker Engine comes from Docker's official Fedora repository and uses:

```text
docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Check mode prints missing RPMs, whether the Docker repository is configured, `uv python install 3.12`, and the exact operator actions for starting Docker and activating group membership. It does not execute them.

- [ ] **Step 4: Implement explicit apply mode**

Print the complete proposed `dnf install` and repository-add commands, require a terminal `yes`, then invoke only the approved transactions through `sudo`. After package verification, run `uv python install 3.12` as the unprivileged user and finish with `./scripts/doctor.sh contributor`. Do not enable/start libvirt or Docker; print those as separate operator commands.

- [ ] **Step 5: Add tracked runtime and Fedora documentation**

Write `22` to `.node-version`. Make standard Fedora Workstation the primary developer path, retain Fedora Atomic as a distinct unsupported/advanced note, preserve Ubuntu production-host links, explain Docker daemon/group session transition and firewalld implications, and state that `--apply` still requires the user to approve privilege changes.

- [ ] **Step 6: Run syntax, fixtures, and forbidden-command scan**

Run:

```bash
bash -n scripts/bootstrap-fedora-dev.sh scripts/tests/test-bootstrap-fedora-dev.sh
bash scripts/tests/test-bootstrap-fedora-dev.sh
! rg -n 'systemctl .*enable|usermod|groupmod|firewall-cmd|setenforce|setenforce 0' scripts/bootstrap-fedora-dev.sh
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add .node-version scripts/bootstrap-fedora-dev.sh \
  scripts/tests/test-bootstrap-fedora-dev.sh docs/runbooks/fresh-linux-setup.md README.md
git commit -m "feat: add Fedora developer bootstrap"
```

### Task 6: Prove Fedora Workstation and SELinux behavior in a disposable VM

**Files:**
- Create: `infra/dev/fedora-proof/ks.cfg`
- Create: `scripts/run-fedora-proof.sh`
- Create: `scripts/inside-fedora-proof.sh`
- Create: `scripts/tests/test-fedora-proof-contract.sh`
- Create: `docs/runbooks/fedora-proof.md`
- Modify: `infra/compose/compose.dev.yml`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`

**Interfaces:**
- Consumes: Tasks 4–5, a caller-supplied Fedora Workstation 44 ISO, libvirt, and the existing dev Compose stack.
- Produces: a reproducible local acceptance artifact and explicit SELinux-compatible dev bind mounts.

- [ ] **Step 1: Write structural and Compose RED tests**

Assert the proof scripts require an absolute ISO, validate its SHA-256 argument, generate an exact unique VM name, refuse cleanup outside their mktemp directory, and check these guest facts:

```text
VARIANT_ID=workstation
VERSION_ID=44
x86_64
getenforce == Enforcing
bootstrap --check, explicitly confirmed bootstrap --apply, explicitly confirmed bootstrap --apply again
doctor contributor, test, stack
Docker testcontainers probe
setup + fast API/web/contracts + Compose configuration
```

Add a deploy-configuration test requiring `:z` on every repository/host bind restated by the developer overlay; named volumes remain unchanged and production overlays do not gain SELinux options.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
bash scripts/tests/test-fedora-proof-contract.sh
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py -m unit --tb=short
```

Expected: fails because the proof artifacts and explicit SELinux labels are absent.

- [ ] **Step 3: Make only the bounded dev-compatible label changes**

Restate the developer stack's base-file bind targets in `compose.dev.yml` with `ro,z`, relying on Compose's target-path uniqueness merge rule so the labelled entry replaces the unlabelled base entry only when the dev overlay is selected. Do not alter `compose.yml`, named volumes, service users, production overlays, ports, or container behavior. Prove all current dev/S/M config combinations still render and production rendering contains no Fedora-dev override.

- [ ] **Step 4: Implement the guest proof script**

Inside the VM, copy the repository to a disposable working directory, run `--check`, pipe the literal confirmation `yes` to each of two `--apply` runs (the outer proof invocation is the operator's authorization for this disposable guest only), perform the documented Docker service/group session transition explicitly, assert SELinux remains enforcing, hydrate with `just setup`, and execute:

```bash
./scripts/doctor.sh contributor
./scripts/doctor.sh test
docker run --rm hello-world
cd apps/api && uv run pytest tests/unit -m unit --tb=short
cd apps/web && npm run lint && npm run typecheck && npm test -- --run
npm ci --prefix packages/contracts --ignore-scripts
bash scripts/tests/test-run-contract-tool.sh
cp .env.example .env
docker compose --env-file .env.example -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml -f infra/compose/compose.dev.yml config --quiet
docker compose --env-file .env -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml -f infra/compose/compose.dev.yml up -d
./scripts/doctor.sh stack
docker compose --env-file .env -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml -f infra/compose/compose.dev.yml down -v
```

Use only generated disposable `.env` secrets inside the VM; never copy host `.env` or site data.

- [ ] **Step 5: Implement the host VM lifecycle**

Use `virt-install --transient` with a copy-on-write disk under a validated mktemp directory and Kickstart from `infra/dev/fedora-proof/ks.cfg`. Require `--iso` and `--iso-sha256`; reject symlinks and non-regular files. Print the VM name/disk before creation. On cleanup, resolve and verify exact targets and stop on any mismatch or locked disk rather than broadening deletion.

- [ ] **Step 6: Document the manual acceptance invocation**

Document Fedora ISO acquisition/checksum verification, separate proof-host installation of `libvirt-daemon-kvm libvirt-client qemu-kvm virt-install guestfs-tools`, required libvirt service/group actions, expected duration, log location, safe rerun, and exact evidence block to paste into the PR. These virtualization packages are not installed by the contributor bootstrap. This proof is intentionally local/manual because GitHub-hosted Ubuntu runners do not provide a trustworthy SELinux-enforcing Fedora Workstation VM boundary.

- [ ] **Step 7: Run fixture/configuration verification**

Run:

```bash
bash -n scripts/run-fedora-proof.sh scripts/inside-fedora-proof.sh \
  scripts/tests/test-fedora-proof-contract.sh
bash scripts/tests/test-fedora-proof-contract.sh
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py -m unit --tb=short
docker compose --env-file .env.example -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml -f infra/compose/compose.dev.yml config --quiet
```

Expected: all fixture/configuration proofs pass. The full VM command is a required PR acceptance gate, not replaced by these structural tests.

- [ ] **Step 8: Commit**

```bash
git add infra/dev/fedora-proof infra/compose/compose.dev.yml scripts/run-fedora-proof.sh \
  scripts/inside-fedora-proof.sh scripts/tests/test-fedora-proof-contract.sh \
  docs/runbooks/fedora-proof.md apps/api/tests/unit/test_deploy_configuration.py
git commit -m "test: add Fedora Workstation acceptance proof"
```

### Task 7: Lock the optional PostgreSQL MCP launcher

**Files:**
- Create: `tools/mcp-postgres/package.json`
- Create: `tools/mcp-postgres/package-lock.json`
- Create: `scripts/run-postgres-mcp.sh`
- Create: `scripts/tests/test-postgres-mcp-lock.mjs`
- Create: `scripts/tests/test-run-postgres-mcp.sh`
- Modify: `.mcp.json`
- Modify: `.github/dependabot.yml`
- Modify: `justfile`
- Modify: `apps/api/tests/unit/test_dependency_tooling.py`

**Interfaces:**
- Consumes: Node 22 and MCP-only environment keys `MCP_POSTGRES_USER`, `MCP_POSTGRES_PASSWORD`, `MCP_POSTGRES_HOST`, `MCP_POSTGRES_PORT`, and `MCP_POSTGRES_DB`.
- Produces: `just setup-mcp` and a launcher that executes only the locally locked `mcp-server-postgres` binary.

- [ ] **Step 1: Write lock and launcher RED tests**

Assert private manifest, exact dependency `0.6.2`, lockfile version 3, exact installed package/binary, no ranges, and no lifecycle-script execution during install. Launcher cases must prove local binary execution, missing-install guidance, missing credential refusal, URL-safe DSN construction, and absence of `npm`, `npx`, `yarn`, or `pnpm` fallback.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
node --test scripts/tests/test-postgres-mcp-lock.mjs
bash scripts/tests/test-run-postgres-mcp.sh
```

Expected: fail because the manifest, lock, and launcher do not exist.

- [ ] **Step 3: Create the private locked package**

Use this manifest and generate its lock under Node 22:

```json
{
  "name": "@easysynq/mcp-postgres-tool",
  "private": true,
  "version": "0.0.0",
  "engines": { "node": "22.x" },
  "dependencies": { "@modelcontextprotocol/server-postgres": "0.6.2" }
}
```

Run:

```bash
npm install --prefix tools/mcp-postgres --package-lock-only --ignore-scripts
npm ci --prefix tools/mcp-postgres --ignore-scripts
```

- [ ] **Step 4: Implement the local-only launcher and MCP config**

Resolve the binary relative to repository root, require all MCP credential keys, percent-encode user/password/database components without printing them, and `exec` the local binary. `.mcp.json` invokes `./scripts/run-postgres-mcp.sh` with no owner variable, owner default, default password, `npx`, or floating spec.

- [ ] **Step 5: Wire optional setup and dependency maintenance**

Add `just setup-mcp` as `npm ci --prefix tools/mcp-postgres --ignore-scripts`. Add `/tools/mcp-postgres` to Dependabot. Keep this optional lock separate from the web audit policy and add an explicit contracts-job audit in Task 9.

- [ ] **Step 6: Run lock, launcher, and audit proofs**

Run:

```bash
node --test scripts/tests/test-postgres-mcp-lock.mjs
bash scripts/tests/test-run-postgres-mcp.sh
npm --prefix tools/mcp-postgres audit --package-lock-only --audit-level=high
cd apps/api && uv run pytest tests/unit/test_dependency_tooling.py -m unit --tb=short
```

Expected: all pass. If the pinned deprecated package has a high/critical advisory with no compatible fixed release, stop the slice and record the connector as disabled rather than weakening the audit gate.

- [ ] **Step 7: Commit**

```bash
git add tools/mcp-postgres scripts/run-postgres-mcp.sh \
  scripts/tests/test-postgres-mcp-lock.mjs scripts/tests/test-run-postgres-mcp.sh \
  .mcp.json .github/dependabot.yml justfile apps/api/tests/unit/test_dependency_tooling.py
git commit -m "build: lock the PostgreSQL MCP tool"
```

### Task 8: Provision and prove a dev-only read-only MCP role

**Files:**
- Create: `infra/compose/postgres/mcp-readonly-init.sh`
- Create: `apps/api/tests/integration/test_mcp_postgres_role.py`
- Modify: `infra/compose/compose.mcp.yml`
- Modify: `.env.example`
- Modify: `justfile`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `README.md`
- Modify: `docs/dev-workflow.md`

**Interfaces:**
- Consumes: Task 7 launcher and existing dev PostgreSQL/migration services.
- Produces: opt-in `easysynq_mcp_ro` with select-only grants and bounded session defaults; no Alembic or production role changes.

- [ ] **Step 1: Write source and Docker-backed RED tests**

Source assertions require loopback-only port publication, post-migration init dependency, required `MCP_POSTGRES_PASSWORD`, no default secret, and no production overlay reference. Integration tests provision the real artifact twice and assert:

```text
SELECT succeeds
INSERT, UPDATE, DELETE, TRUNCATE, CREATE, and sequence access fail
SET ROLE to owner, easysynq_app, or easysynq_linker fails
Keycloak schema access fails
SHOW default_transaction_read_only returns on
SHOW statement_timeout, lock_timeout, idle_in_transaction_session_timeout are bounded
owner-created table after provisioning is readable
easysynq_create_audit_partition cannot create a partition
second provisioning is idempotent and rotates only the MCP password
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py \
  tests/integration/test_mcp_postgres_role.py --tb=short
```

Expected: fails because the role provisioning service does not exist and the current MCP overlay exposes the owner only.

- [ ] **Step 3: Implement idempotent dev-only provisioning**

After migration completion, create/alter exactly `easysynq_mcp_ro` with:

```sql
LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
ALTER ROLE easysynq_mcp_ro SET default_transaction_read_only = on;
ALTER ROLE easysynq_mcp_ro SET statement_timeout = '15s';
ALTER ROLE easysynq_mcp_ro SET lock_timeout = '2s';
ALTER ROLE easysynq_mcp_ro SET idle_in_transaction_session_timeout = '30s';
GRANT CONNECT ON DATABASE easysynq TO easysynq_mcp_ro;
GRANT USAGE ON SCHEMA public TO easysynq_mcp_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO easysynq_mcp_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE easysynq IN SCHEMA public
  GRANT SELECT ON TABLES TO easysynq_mcp_ro;
```

Revoke schema creation, temporary database use, sequence access, and role memberships. PostgreSQL grants function execution to `PUBLIC` by default, so the dev-only initializer must execute `REVOKE EXECUTE ON FUNCTION easysynq_create_audit_partition(date) FROM PUBLIC`, then re-grant that exact function to `easysynq_app`; a role-local revoke alone is insufficient. Add a regression that the app retains the intended call while MCP cannot call it. Quote the password through `psql` variables rather than string interpolation. Keep the script and service in `compose.mcp.yml`; do not add an Alembic revision or production include.

- [ ] **Step 4: Wire opt-in operation**

Add required `.env.example` keys with obvious placeholders. `just up-mcp` validates the MCP password, starts the dev PostgreSQL/migration/init services, and prints `just setup-mcp` plus the client restart step. Document teardown and state that production/site data is forbidden.

- [ ] **Step 5: Run least-privilege and Compose proofs**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py \
  tests/integration/test_mcp_postgres_role.py --tb=short
docker compose --env-file .env.example -f infra/compose/compose.yml \
  -f infra/compose/compose.s.yml -f infra/compose/compose.dev.yml \
  -f infra/compose/compose.mcp.yml config --quiet
bash scripts/check-no-site-data.sh .mcp.json .env.example infra/compose/compose.mcp.yml \
  infra/compose/postgres/mcp-readonly-init.sh
```

Expected: all pass; the security-definer mutation probe fails for the MCP role without creating an audit partition.

- [ ] **Step 6: Commit**

```bash
git add infra/compose/postgres/mcp-readonly-init.sh infra/compose/compose.mcp.yml \
  apps/api/tests/integration/test_mcp_postgres_role.py \
  apps/api/tests/unit/test_deploy_configuration.py .env.example justfile README.md docs/dev-workflow.md
git commit -m "fix: enforce read-only PostgreSQL MCP access"
```

### Task 9: Close CI, documentation, and clean-Fedora acceptance

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/tests/test-ci-hardening.sh`
- Modify: `apps/api/tests/unit/test_ci_workflow.py`
- Modify: `AGENTS.md`
- Modify: `docs/current-status.md`
- Modify: `docs/dev-workflow.md`
- Modify: `docs/runbooks/fresh-linux-setup.md`
- Modify: `docs/runbooks/fedora-proof.md`

**Interfaces:**
- Consumes: all Tasks 1–8 and an approved Fedora Workstation ISO checksum.
- Produces: required fast CI contracts, recorded clean-VM evidence, and the complete Programme 0 handoff.

- [ ] **Step 1: Add CI RED assertions for all new fast gates**

Update exact workflow expectations so `contracts` includes, before dependency hydration:

```yaml
- name: Fedora/bootstrap/doctor shell contracts
  run: |
    bash scripts/tests/test-bootstrap-fedora-dev.sh
    bash scripts/tests/test-doctor.sh
    bash scripts/tests/test-fedora-proof-contract.sh
- name: install locked PostgreSQL MCP tool
  run: npm ci --prefix tools/mcp-postgres --ignore-scripts
- name: PostgreSQL MCP tool contracts
  run: |
    node --test scripts/tests/test-postgres-mcp-lock.mjs
    bash scripts/tests/test-run-postgres-mcp.sh
- name: audit locked PostgreSQL MCP tool
  run: npm --prefix tools/mcp-postgres audit --package-lock-only --audit-level=high
```

Assert all are hard-fail and the authority/R61 checks remain first.

- [ ] **Step 2: Run structural CI tests and verify RED**

Run:

```bash
bash scripts/tests/test-ci-hardening.sh
cd apps/api && uv run pytest tests/unit/test_ci_workflow.py -m unit --tb=short
```

Expected: fails until the workflow contains the exact new steps.

- [ ] **Step 3: Wire the fast gates and finish documentation**

Add the workflow steps without adding a misleading container-only Fedora job. In the docs, give each common doctor reason its exact next command; document MCP deprecation/opt-in boundary; state that the clean Fedora VM proof is a release/PR acceptance artifact; and link all commands from `AGENTS.md` without copying mutable results.

- [ ] **Step 4: Run the full focused local verification matrix**

Run:

```bash
bash -n scripts/*.sh scripts/tests/*.sh .claude/hooks/*.sh infra/compose/postgres/*.sh
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
bash scripts/tests/test-bootstrap-fedora-dev.sh
bash scripts/tests/test-doctor.sh
bash scripts/tests/test-fedora-proof-contract.sh
node --test scripts/tests/test-postgres-mcp-lock.mjs
bash scripts/tests/test-run-postgres-mcp.sh
bash scripts/tests/test-ci-hardening.sh
bash scripts/tests/test-check-no-site-data.sh
bash scripts/check-no-site-data.sh
npm --prefix tools/mcp-postgres audit --package-lock-only --audit-level=high
cd apps/api && uv run pytest tests/unit/test_dependency_tooling.py \
  tests/unit/test_deploy_configuration.py tests/unit/test_ci_workflow.py -m unit --tb=short
cd apps/api && uv run pytest tests/integration/test_mcp_postgres_role.py -m integration --tb=short
git diff --check
```

Expected: every command passes. Use a writable task-specific uv cache if the sandboxed environment requires it; do not alter global user configuration.

- [ ] **Step 5: Run the real disposable Fedora Workstation proof**

Place the Fedora Workstation 44 ISO and its published `CHECKSUM` file in `.fedora-proof/`, then resolve and validate the single matching pair before launching:

```bash
mapfile -t EASYSYNQ_FEDORA_ISOS < <(find "$PWD/.fedora-proof" -maxdepth 1 -type f \
  -name 'Fedora-Workstation-Live-44-*.x86_64.iso' -print)
mapfile -t EASYSYNQ_FEDORA_CHECKSUMS < <(find "$PWD/.fedora-proof" -maxdepth 1 -type f \
  -name 'Fedora-Workstation-44-*-x86_64-CHECKSUM' -print)
test "${#EASYSYNQ_FEDORA_ISOS[@]}" -eq 1
test "${#EASYSYNQ_FEDORA_CHECKSUMS[@]}" -eq 1
EASYSYNQ_FEDORA_ISO_SHA256="$(awk -v iso="$(basename "${EASYSYNQ_FEDORA_ISOS[0]}")" \
  '$0 ~ iso {print $NF; exit}' \
  "${EASYSYNQ_FEDORA_CHECKSUMS[0]}")"
test "${#EASYSYNQ_FEDORA_ISO_SHA256}" -eq 64
./scripts/run-fedora-proof.sh \
  --iso "${EASYSYNQ_FEDORA_ISOS[0]}" \
  --iso-sha256 "$EASYSYNQ_FEDORA_ISO_SHA256"
```

Expected: Fedora Workstation 44/x86_64, SELinux Enforcing, bootstrap apply twice, Docker/testcontainers access after the explicit session transition, setup, fast API/web/contracts, Compose configuration, live dev stack, and all three doctor profiles pass. The proof log contains no host `.env`, secrets, or site data.

- [ ] **Step 6: Record evidence without duplicating authority**

Add the proof date, Fedora release, ISO checksum, evidence commit, and pass/fail summary to `docs/current-status.md`. Store no passwords, VM disk, generated `.env`, or full machine log in Git. Re-run `./scripts/check-repo-authority.sh` to ensure the evidence did not create a second canonical fact.

- [ ] **Step 7: Run final repository review**

Review the diff against every Programme 0 acceptance criterion, confirm production Compose/application/migrations are untouched, confirm the SELinux bind labels exist only in the developer overlay, confirm Ubuntu source tests/links remain, and run:

```bash
git status --short
git diff --stat
git diff --check
./scripts/check-repo-authority.sh
bash scripts/check-no-site-data.sh
```

- [ ] **Step 8: Commit the acceptance closure**

```bash
git add .github/workflows/ci.yml scripts/tests/test-ci-hardening.sh \
  apps/api/tests/unit/test_ci_workflow.py AGENTS.md docs/current-status.md \
  docs/dev-workflow.md docs/runbooks/fresh-linux-setup.md docs/runbooks/fedora-proof.md
git commit -m "ci: enforce Fedora foundation acceptance"
```

---

## Final acceptance checklist

- [ ] `AGENTS.md` is the only cross-agent workflow guide; product and execution authorities remain separate and linked.
- [ ] The reviewed live-path manifest and global scan cover all current root/docs/source/test/Claude consumers, not only the originally enumerated files.
- [ ] No mutable migration, suite, CI, decision-range, permission-count, slice, or residual fact has two canonical homes.
- [ ] Hook/command behavior executes against fixtures; file-existence-only checks are insufficient.
- [ ] `.mcp.json` uses a dedicated dev-only role and locked local launcher; owner credentials and floating fetches are absent.
- [ ] MCP grants are proven by successful reads and failed DML/DDL/role-switch/security-definer operations.
- [ ] Fedora check/default mode is non-mutating and the second apply is idempotent.
- [ ] Doctor distinguishes every named Docker access state, does not disclose secrets, and gates only the selected profile.
- [ ] The clean Fedora Workstation proof is SELinux-enforcing and exercises setup, Docker/testcontainers, fast API/web/contracts, Compose, and the live dev stack.
- [ ] Ubuntu production bootstrap behavior and links remain intact.
- [ ] R61 site-data gates, shell syntax, focused tests, link/traceability checks, dependency audits, and `git diff --check` pass.
