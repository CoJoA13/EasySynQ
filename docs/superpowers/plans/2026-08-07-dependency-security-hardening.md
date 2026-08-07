# Dependency Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock every ad hoc dependency tool, remediate the currently patchable web advisories, and make every unapproved high or critical npm advisory fail CI.

**Architecture:** Contract and Python audit tools move into ecosystem-native manifests and committed locks, with root-aware wrappers as the only execution paths. A dependency-free Node policy layer interprets npm's lock-only audit report, applies one atomic expiring Router exception, and invokes an AST-based first-party usage guard before accepting it. CI wiring remains thin and is protected by Bash and parsed-YAML structural regressions.

**Tech Stack:** Bash, npm 10.9.x on Node 22, ECMAScript modules with `node:test`, TypeScript compiler API, uv/Python 3.12, GitHub Actions YAML, Dependabot, GitHub REST API.

## Global Constraints

- Pin `@redocly/cli` exactly to `2.46.0`, `openapi-typescript` exactly to `7.13.0`, and `pip-audit` exactly to `2.10.1`.
- Generate npm locks with Node 22 and npm 10.9.x; the npm policy accepts only `/^10\.9\.\d+$/`, audit report version `2`, and lockfile version `3`.
- Upgrade only compatible web selections: `brace-expansion` to `1.1.18`/`5.0.9`, `undici` to `7.29.0`, and `react-router`/`react-router-dom` to `7.18.2`.
- Do not use `npm audit fix --force`, dependency overrides, a Router downgrade, a jsdom major upgrade, `npx`, or a network fallback.
- Fail npm policy on every unapproved high/critical advisory and every execution/schema/lock mismatch.
- The only exception is `GHSA-qwww-vcr4-c8h2`; its two Router records are atomic, require exact 7.18.2 lock nodes plus the `router-rsc-absent` usage policy, and expire exclusively at `2026-08-22T00:00:00Z`.
- Keep Python vulnerability findings and every Trivy finding report-only; operational failures must still fail the security job.
- Keep Dependabot security updates individual: no security grouping, auto-merge, or edits to existing Dependabot PRs.
- Resolve tool roots from the script location, not the caller's current Git repository.
- Preserve the committed contract hash and functional generated API types; add `--disable-timestamp` solely to remove generator metadata nondeterminism.
- Keep Action SHA pinning, exact Node runtime pinning, Trivy baselining, web image restructuring, and branch rulesets out of this branch.
- Preserve the owner-controlled untracked `.codex/` directory and run the R61 repository backstop before publication.

## File Structure

New contract-tool files:

- `packages/contracts/package.json` — exact direct contract-tool requirements.
- `packages/contracts/package-lock.json` — npm integrity and transitive resolution.
- `scripts/run-contract-tool.sh` — allowlisted, local-only contract binary launcher.
- `scripts/tests/test-run-contract-tool.sh` — isolated wrapper behavior regression.
- `scripts/tests/test-contract-lock.mjs` — manifest, lock, and installed-version regression.
- `scripts/tests/test-gen-contracts.sh` — fake-tool generator routing and determinism regression.

New Python-audit files:

- `scripts/run-pip-audit.sh` — frozen export, locked audit execution, and report/status validation.
- `scripts/tests/test-pip-audit-runner.sh` — stubbed clean, finding, and operational-failure cases.

New npm-policy files:

- `.github/security/npm-audit-exceptions.json` — the single reviewed exception record.
- `scripts/check-npm-audit.mjs` — no-argument production entry point.
- `scripts/lib/npm-audit-runner.mjs` — npm subprocess and isolated-cache lifecycle.
- `scripts/lib/npm-audit-policy.mjs` — pure schema, lock, advisory, and expiry policy.
- `scripts/lib/router-rsc-policy.mjs` — tracked-source selection and TypeScript AST inspection.
- `scripts/tests/test-npm-audit-runner.mjs` — subprocess boundary tests.
- `scripts/tests/test-check-npm-audit.mjs` — production orchestration and exit-code tests.
- `scripts/tests/test-npm-audit-policy.mjs` — pure policy mutation tests.
- `scripts/tests/test-router-rsc-policy.mjs` — first-party usage-policy tests.
- `scripts/tests/fixtures/npm-audit/audit-clean.json` — valid empty v2 report.
- `scripts/tests/fixtures/npm-audit/audit-router-7.18.2.json` — current two-record Router report.
- `scripts/tests/fixtures/npm-audit/audit-unexpected-high.json` — one synthetic unapproved high.
- `scripts/tests/fixtures/npm-audit/package-lock.json` — minimal lock v3 for policy tests.
- `scripts/tests/test-web-security-lock.mjs` — exact patched-version regression over the real web lock.

New semantic regression:

- `apps/api/tests/unit/test_dependency_tooling.py` — parsed manifests, locks, workflow, and Dependabot assertions.

Existing integration surfaces:

- `scripts/gen-contracts.sh`, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.pre-commit-config.yaml`, `justfile`.
- `scripts/tests/test-ci-hardening.sh`, `apps/api/tests/unit/test_ci_workflow.py`.
- `.claude/commands/check-contracts.md`, `.claude/commands/pr.md`, `.claude/hooks/contract-lock-drift.sh`.
- `CLAUDE.md`, `README.md`, `docs/dev-workflow.md`, `docs/runbooks/fresh-linux-setup.md`.

---

### Task 1: Add the root-aware contract-tool launcher

**Files:**
- Create: `scripts/run-contract-tool.sh`
- Create: `scripts/tests/test-run-contract-tool.sh`

**Interfaces:**
- Consumes: `packages/contracts/node_modules/.bin/{redocly,openapi-typescript}`
- Produces: `bash scripts/run-contract-tool.sh {redocly|openapi-typescript} [arguments]`
- Exit contract: `64` for missing/unknown tool selection, `127` for a missing local executable, otherwise the selected executable's status

- [ ] **Step 1: Write the isolated failing wrapper regression**

  Follow the guarded `mktemp -d`/trap pattern in `scripts/tests/test-check-no-site-data.sh`. Copy the
  production wrapper into a fixture `scripts/` directory, create fake executables only under the
  fixture's `packages/contracts/node_modules/.bin`, and assert all of these cases:

  ```text
  no argument -> 64
  unknown tool -> 64
  known tool without local binary -> 127 and exact setup command
  same-named PATH executable -> never called
  argument containing spaces -> preserved as one argument
  redocly -> both telemetry variables present
  openapi-typescript -> exact local binary selected
  caller inside unrelated initialized Git repository -> fixture root remains CWD
  ```

  The required missing-binary diagnostic is:

  ```text
  Run: npm ci --prefix packages/contracts --ignore-scripts
  ```

- [ ] **Step 2: Run the wrapper test to prove RED**

  Run: `bash scripts/tests/test-run-contract-tool.sh`

  Expected: nonzero because `scripts/run-contract-tool.sh` does not exist.

- [ ] **Step 3: Implement the minimal allowlisted wrapper**

  Use this control flow; do not use `command -v`, npm, or npx:

  ```bash
  #!/usr/bin/env bash
  set -euo pipefail

  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  if [ "$#" -eq 0 ]; then
    echo "usage: run-contract-tool.sh {redocly|openapi-typescript} [arguments]" >&2
    exit 64
  fi

  tool="$1"
  shift
  case "$tool" in
    redocly|openapi-typescript) ;;
    *) echo "unsupported contract tool: $tool" >&2; exit 64 ;;
  esac

  binary="$ROOT/packages/contracts/node_modules/.bin/$tool"
  if [ ! -x "$binary" ]; then
    echo "contract tool is not installed: $tool" >&2
    echo "Run: npm ci --prefix packages/contracts --ignore-scripts" >&2
    exit 127
  fi

  if [ "$tool" = "redocly" ]; then
    export REDOCLY_TELEMETRY=off
    export REDOCLY_SUPPRESS_UPDATE_NOTICE=true
  fi
  cd "$ROOT"
  exec "$binary" "$@"
  ```

  Mark both shell files executable.

- [ ] **Step 4: Run syntax and behavior checks**

  Run:

  ```bash
  bash -n scripts/run-contract-tool.sh scripts/tests/test-run-contract-tool.sh
  bash scripts/tests/test-run-contract-tool.sh
  ```

  Expected: syntax clean and every wrapper assertion passes.

- [ ] **Step 5: Commit the wrapper boundary**

  ```bash
  git add scripts/run-contract-tool.sh scripts/tests/test-run-contract-tool.sh
  git commit -m "build: add locked contract tool launcher"
  ```

### Task 2: Add the exact contract manifest and integrity lock

**Files:**
- Create: `packages/contracts/package.json`
- Create: `packages/contracts/package-lock.json`
- Create: `scripts/tests/test-contract-lock.mjs`

**Interfaces:**
- Consumes: npm registry metadata under Node 22/npm 10.9.x
- Produces: exact installed binaries for Task 1 and one committed lock root for Dependabot

- [ ] **Step 1: Write the failing lock regression**

  The Node test reads the manifest, lock, and installed package metadata and asserts:

  ```js
  assert.equal(manifest.private, true);
  assert.deepEqual(manifest.devDependencies, {
    "@redocly/cli": "2.46.0",
    "openapi-typescript": "7.13.0",
  });
  assert.deepEqual(lock.packages[""].devDependencies, manifest.devDependencies);
  assert.equal(lock.packages["node_modules/@redocly/cli"].version, "2.46.0");
  assert.equal(lock.packages["node_modules/openapi-typescript"].version, "7.13.0");
  assert.equal(installedRedocly.version, "2.46.0");
  assert.equal(installedOpenapiTypescript.version, "7.13.0");
  ```

- [ ] **Step 2: Run the lock test to prove RED**

  Run: `node --test scripts/tests/test-contract-lock.mjs`

  Expected: nonzero because the contract manifest and lock do not exist.

- [ ] **Step 3: Add the exact manifest**

  Create:

  ```json
  {
    "name": "@easysynq/contracts-toolchain",
    "version": "0.1.0",
    "private": true,
    "devDependencies": {
      "@redocly/cli": "2.46.0",
      "openapi-typescript": "7.13.0"
    }
  }
  ```

- [ ] **Step 4: Generate and hydrate the lock under the supported runtime**

  First require `node --version` to report `v22.*` and `npm --version` to match `10.9.*`. Then run:

  ```bash
  npm install --prefix packages/contracts --package-lock-only --ignore-scripts --no-audit --no-fund
  npm ci --prefix packages/contracts --ignore-scripts
  ```

- [ ] **Step 5: Verify direct versions, signatures, and lock consistency**

  Run:

  ```bash
  node --test scripts/tests/test-contract-lock.mjs
  bash scripts/run-contract-tool.sh redocly --version
  bash scripts/run-contract-tool.sh openapi-typescript --version
  npm --prefix packages/contracts audit signatures
  ```

  Expected versions: `2.46.0` and `7.13.0`; signature verification succeeds.

- [ ] **Step 6: Commit the contract dependency lock**

  ```bash
  git add packages/contracts/package.json packages/contracts/package-lock.json scripts/tests/test-contract-lock.mjs
  git commit -m "build: lock contract generation tools"
  ```

### Task 3: Make full contract generation deterministic and CWD-independent

**Files:**
- Modify: `scripts/gen-contracts.sh:7-49`
- Create: `scripts/tests/test-gen-contracts.sh`

**Interfaces:**
- Consumes: Task 1 launcher, Task 2 installed tools, uv-locked `datamodel-code-generator`
- Produces: CWD-independent lint/bundle/generate behavior and byte-stable generated outputs

- [ ] **Step 1: Write the fake-tool generator regression**

  Build a guarded temporary fixture containing the real generator and wrapper, fake Redocly,
  OpenAPI TypeScript, and uv executables, a minimal contract, and its expected lock. The fake tools
  must record their CWD/argv and write deterministic output. Assert:

  - root, `packages/contracts`, and an unrelated initialized Git repository all resolve the fixture;
  - lint and bundle each receive `--config packages/contracts/redocly.yaml`;
  - OpenAPI TypeScript receives `packages/contracts/dist/openapi.json` and
    `apps/web/src/api/_generated/schema.d.ts`;
  - datamodel-codegen receives `--disable-timestamp`;
  - two full runs produce identical hashes for bundle, Python model, and TypeScript declaration;
  - neither Redocly nor OpenAPI TypeScript is invoked through npx.

- [ ] **Step 2: Run the generator test to prove RED**

  Run: `bash scripts/tests/test-gen-contracts.sh`

  Expected: nonzero because the current generator trusts the caller's Git root, omits config on its
  internal Redocly calls, uses npx, and emits a Python timestamp.

- [ ] **Step 3: Replace caller-dependent root and floating commands**

  Resolve the root only from the script:

  ```bash
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  RUN_CONTRACT_TOOL="$ROOT/scripts/run-contract-tool.sh"
  ```

  Run the tools with repository-root-relative arguments because the wrapper changes CWD to `ROOT`:

  ```bash
  "$RUN_CONTRACT_TOOL" redocly lint \
    --config packages/contracts/redocly.yaml \
    packages/contracts/openapi.yaml
  "$RUN_CONTRACT_TOOL" redocly bundle \
    --config packages/contracts/redocly.yaml \
    packages/contracts/openapi.yaml \
    -o packages/contracts/dist/openapi.json
  "$RUN_CONTRACT_TOOL" openapi-typescript \
    packages/contracts/dist/openapi.json \
    -o apps/web/src/api/_generated/schema.d.ts
  ```

  Add `--disable-timestamp` to the existing datamodel-codegen invocation. Keep the checksum gate and
  its `--check` early exit unchanged.

- [ ] **Step 4: Run fake and real contract checks**

  Run:

  ```bash
  bash -n scripts/gen-contracts.sh scripts/tests/test-gen-contracts.sh
  bash scripts/tests/test-gen-contracts.sh
  bash scripts/gen-contracts.sh --check
  (cd packages/contracts && ../../scripts/gen-contracts.sh --check)
  ```

  Expected: all pass and `.contract.lock` remains unchanged.

- [ ] **Step 5: Commit deterministic generation**

  ```bash
  git add scripts/gen-contracts.sh scripts/tests/test-gen-contracts.sh
  git commit -m "build: make contract generation deterministic"
  ```

### Task 4: Route every active contract entry point through the lock

**Files:**
- Modify: `.github/workflows/ci.yml:16-34`
- Modify: `.github/dependabot.yml:24-40`
- Modify: `.pre-commit-config.yaml:48-59`
- Modify: `.claude/commands/check-contracts.md:1-14`
- Modify: `.claude/commands/pr.md:13`
- Modify: `justfile:9-21`
- Modify: `scripts/tests/test-ci-hardening.sh:175-187`
- Modify: `apps/api/tests/unit/test_ci_workflow.py:75-102`
- Create: `apps/api/tests/unit/test_dependency_tooling.py`

**Interfaces:**
- Consumes: Tasks 1-3 contract toolchain
- Produces: one locked path for CI, pre-commit, Claude commands, Just, and Dependabot

- [ ] **Step 1: Add failing structural and semantic assertions**

  Extend the Bash workflow regression to reject active `npx` Redocly/OpenAPI TypeScript calls and
  assert the exact contract install and wrapper lint. In the parsed tests, assert step order and these
  exact commands:

  ```text
  npm ci --prefix packages/contracts --ignore-scripts
  bash scripts/tests/test-run-contract-tool.sh
  node --test scripts/tests/test-contract-lock.mjs
  bash scripts/tests/test-gen-contracts.sh
  bash scripts/run-contract-tool.sh redocly lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml
  bash scripts/gen-contracts.sh --check
  ```

  `test_dependency_tooling.py` must parse JSON/TOML/YAML and assert exact manifest/lock versions plus
  exactly one weekly `/packages/contracts` npm Dependabot entry with limit five and this group:

  ```yaml
  contract-tools-minor-patch:
    applies-to: version-updates
    update-types: ["minor", "patch"]
  ```

  Assert there is no security-update group, `target-branch`, or auto-merge setting.

- [ ] **Step 2: Run focused regressions to prove RED**

  Run:

  ```bash
  bash scripts/tests/test-ci-hardening.sh
  (cd apps/api && uv run pytest tests/unit/test_ci_workflow.py tests/unit/test_dependency_tooling.py -m unit -q)
  ```

  Expected: failures for the floating workflow/pre-commit command and missing Dependabot entry.

- [ ] **Step 3: Wire the contracts CI job in hard-fail order**

  Keep the dependency-free R61 and workflow tests before hydration, then add:

  ```yaml
  - uses: actions/setup-node@v7
    with:
      node-version: "22"
      cache: npm
      cache-dependency-path: packages/contracts/package-lock.json
  - name: install locked contract tools
    run: npm ci --prefix packages/contracts --ignore-scripts
  - name: contract toolchain regressions
    run: |
      bash scripts/tests/test-run-contract-tool.sh
      node --test scripts/tests/test-contract-lock.mjs
      bash scripts/tests/test-gen-contracts.sh
  - name: lint OpenAPI
    run: bash scripts/run-contract-tool.sh redocly lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml
  - name: generated contract lock
    run: bash scripts/gen-contracts.sh --check
  ```

  Do not add `continue-on-error`, `|| true`, or a fallback installer.

- [ ] **Step 4: Wire local and update-automation entry points**

  - Pre-commit retains `language: system`, `pass_filenames: false`, and uses the exact wrapper lint.
  - `.claude/commands/check-contracts.md` removes `Bash(npx:*)` and shows wrapper lint plus generator check.
  - `.claude/commands/pr.md` names the wrapper-based contract command.
  - `just setup` uses `npm ci --prefix apps/web`, installs contract tools with `--ignore-scripts`, then
    runs `just contracts` and installs pre-commit.
  - Dependabot adds the exact `/packages/contracts` version-update entry from Step 1.

- [ ] **Step 5: Run contract wiring verification**

  Run:

  ```bash
  bash scripts/tests/test-ci-hardening.sh
  bash scripts/tests/test-run-contract-tool.sh
  node --test scripts/tests/test-contract-lock.mjs
  bash scripts/tests/test-gen-contracts.sh
  (cd apps/api && uv run pytest tests/unit/test_ci_workflow.py tests/unit/test_dependency_tooling.py -m unit -q)
  pre-commit run contracts-lint --all-files
  ```

- [ ] **Step 6: Commit every active contract consumer together**

  ```bash
  git add .github/workflows/ci.yml .github/dependabot.yml .pre-commit-config.yaml .claude/commands/check-contracts.md .claude/commands/pr.md justfile scripts/tests/test-ci-hardening.sh apps/api/tests/unit/test_ci_workflow.py apps/api/tests/unit/test_dependency_tooling.py
  git commit -m "ci: enforce locked contract tooling"
  ```

### Task 5: Lock pip-audit and preserve report-only Python findings

**Files:**
- Modify: `apps/api/pyproject.toml:39-52`
- Modify: `apps/api/uv.lock`
- Create: `scripts/run-pip-audit.sh`
- Create: `scripts/tests/test-pip-audit-runner.sh`
- Modify: `.github/workflows/ci.yml:243-273`
- Modify: `scripts/tests/test-ci-hardening.sh`
- Modify: `apps/api/tests/unit/test_dependency_tooling.py`

**Interfaces:**
- Consumes: frozen `apps/api/uv.lock`, `uv`, `jq`, and an optional runner-provided `RUNNER_TEMP`
- Produces: `bash scripts/run-pip-audit.sh`; zero for either a valid clean report or a valid report containing findings, nonzero for every execution/report disagreement

- [ ] **Step 1: Write the failing stubbed runner regression**

  Put a fake `uv` first on a guarded temporary `PATH`. It must distinguish `uv export` from
  `uv run --frozen --only-group security pip-audit`, write the requested files, and select behavior
  through a fixture-only environment variable consumed by the fake executable. Cover:

  ```text
  status 0 + valid zero-vulnerability report -> pass
  status 1 + valid vulnerability-bearing report -> pass and print IDs
  status 0 + vulnerabilities -> fail
  status 1 + zero vulnerabilities -> fail
  malformed JSON -> fail
  missing report -> fail
  dependencies not an array -> fail
  vulnerability without a string id -> fail
  status 2 -> fail
  export failure -> fail
  ```

  Assert the production runner passes `--no-group security` to export and executes pip-audit only via
  `uv run --frozen --only-group security`. Both commands must run with `apps/api` as their current
  working directory even when the runner is invoked from elsewhere.

- [ ] **Step 2: Run the runner regression to prove RED**

  Run: `bash scripts/tests/test-pip-audit-runner.sh`

  Expected: nonzero because the production runner does not exist.

- [ ] **Step 3: Add the exact security dependency group and refresh the lock**

  Add this sibling of `dev`:

  ```toml
  security = [
    "pip-audit==2.10.1",
  ]
  ```

  From `apps/api`, run `uv lock`, then `uv lock --check`. Do not add pip-audit to the default `dev`
  group. Extend `test_dependency_tooling.py` to assert the pyproject group is exactly the one entry,
  the lock contains `pip-audit` version `2.10.1`, and the editable root's security metadata references it.

- [ ] **Step 4: Implement the report/status runner**

  `scripts/run-pip-audit.sh` resolves the repository from `BASH_SOURCE`, creates its own directory with
  `mktemp -d` beneath `${RUNNER_TEMP:-/tmp}`, and removes only that created directory in an EXIT trap.
  It changes to `$ROOT/apps/api` before running either uv command, so uv always consumes the committed
  API project and lock regardless of the caller's CWD:

  ```bash
  cd "$ROOT/apps/api"

  uv export --frozen --no-group security --no-emit-project \
    --format requirements-txt \
    -o "$audit_tmp/py-requirements.txt"

  set +e
  uv run --frozen --only-group security pip-audit \
    -r "$audit_tmp/py-requirements.txt" \
    --format json \
    -o "$audit_tmp/pip-audit.json"
  audit_status=$?
  set -e
  ```

  Validate with jq that the root is an object, `dependencies` is an array, every dependency has string
  `name`/`version` and array `vulns`, and every vulnerability has a string `id`. Count vulnerabilities,
  require `(status 0, count 0)` or `(status 1, count > 0)`, and reject every other pairing. Print only
  package, version, and vulnerability IDs; never print the exported requirements or environment.

- [ ] **Step 5: Replace the floating workflow command**

  The security job's Python step becomes:

  ```yaml
  - name: pip-audit runner regressions
    run: bash scripts/tests/test-pip-audit-runner.sh
  - name: pip-audit (Python deps, resolved from uv.lock)
    run: bash scripts/run-pip-audit.sh
  ```

  Update the preamble to say pip-audit and Trivy findings remain report-only while operational failures
  fail. Add exact Bash/YAML regression assertions and reject `uvx pip-audit` in active files.

- [ ] **Step 6: Prove frozen-lock input reaches pip-audit**

  In the shell regression, copy `apps/api/pyproject.toml` and `uv.lock` to a temporary project, export
  once, mutate one selected package version only in the copied lock, export again, and assert the
  requirements line changes to the copied lock's version. No network install is part of this proof.

- [ ] **Step 7: Run focused verification**

  Run:

  ```bash
  bash -n scripts/run-pip-audit.sh scripts/tests/test-pip-audit-runner.sh
  bash scripts/tests/test-pip-audit-runner.sh
  bash scripts/tests/test-ci-hardening.sh
  (cd apps/api && uv lock --check)
  (cd apps/api && uv run --frozen --only-group security pip-audit --version)
  (cd apps/api && uv run pytest tests/unit/test_dependency_tooling.py tests/unit/test_ci_workflow.py -m unit -q)
  ```

  Expected pip-audit version: `2.10.1`.

- [ ] **Step 8: Commit the Python audit boundary**

  ```bash
  git add apps/api/pyproject.toml apps/api/uv.lock scripts/run-pip-audit.sh scripts/tests/test-pip-audit-runner.sh .github/workflows/ci.yml scripts/tests/test-ci-hardening.sh apps/api/tests/unit/test_dependency_tooling.py
  git commit -m "ci: lock the Python audit runner"
  ```

### Task 6: Refresh only the vulnerable web patch selections

**Files:**
- Modify: `apps/web/package.json:17-25`
- Modify: `apps/web/package-lock.json`
- Create: `scripts/tests/test-web-security-lock.mjs`

**Interfaces:**
- Consumes: the existing web semver ranges and npm 10.9.x resolver
- Produces: patched exact lock nodes without an override, downgrade, or major upgrade

- [ ] **Step 1: Write the failing real-lock regression**

  Read `apps/web/package.json` and `package-lock.json`, iterate every package-lock path by final
  `node_modules/` segment, and assert:

  ```js
  assert.equal(manifest.dependencies["react-router-dom"], "^7.18.2");
  assert.deepEqual(versions("brace-expansion"), new Set(["1.1.18", "5.0.9"]));
  assert.deepEqual(versions("undici"), new Set(["7.29.0"]));
  assert.deepEqual(versions("react-router"), new Set(["7.18.2"]));
  assert.deepEqual(versions("react-router-dom"), new Set(["7.18.2"]));
  assert.equal(manifest.overrides["react-router"], undefined);
  assert.equal(manifest.overrides["react-router-dom"], undefined);
  ```

  Preserve the existing ESLint compatibility override as the only override entry.

- [ ] **Step 2: Run the lock regression to prove RED**

  Run: `node --test scripts/tests/test-web-security-lock.mjs`

  Expected: it reports the current 1.1.15/5.0.7, 7.28.0, and 7.18.1 selections.

- [ ] **Step 3: Raise the Router floor and update only compatible packages**

  Change the manifest floor to `^7.18.2`, then under Node 22/npm 10.9.x run:

  ```bash
  npm update --prefix apps/web brace-expansion undici react-router-dom \
    --package-lock-only --ignore-scripts --no-audit --no-fund
  npm ci --prefix apps/web
  ```

  Inspect the lock diff and reject unrelated direct dependency or major-version movement.

- [ ] **Step 4: Verify the patched tree and unchanged web behavior**

  Run:

  ```bash
  node --test scripts/tests/test-web-security-lock.mjs
  (cd apps/web && npm test)
  (cd apps/web && npm run lint)
  (cd apps/web && npm run build)
  (cd apps/web && npm audit --package-lock-only --audit-level=high --json)
  ```

  Expected: web gates pass; the live audit contains only the Router root and inherited DOM records.

- [ ] **Step 5: Commit the compatible remediation**

  ```bash
  git add apps/web/package.json apps/web/package-lock.json scripts/tests/test-web-security-lock.mjs
  git commit -m "build: apply patched web dependency selections"
  ```

### Task 7: Implement the pure npm advisory policy

**Files:**
- Create: `.github/security/npm-audit-exceptions.json`
- Create: `scripts/lib/npm-audit-policy.mjs`
- Create: `scripts/tests/test-npm-audit-policy.mjs`
- Create: `scripts/tests/fixtures/npm-audit/audit-clean.json`
- Create: `scripts/tests/fixtures/npm-audit/audit-router-7.18.2.json`
- Create: `scripts/tests/fixtures/npm-audit/audit-unexpected-high.json`
- Create: `scripts/tests/fixtures/npm-audit/package-lock.json`

**Interfaces:**
- Consumes: npm version string, audit status/stdout, package-lock v3 object, exception object, injected `Date`
- Produces: `assessNpmAudit({ npmVersion, exitCode, stdout, lockfile, exceptionPolicy, now })`
  accepted/blocked result or a coded fail-closed `NpmAuditPolicyError`

- [ ] **Step 1: Commit realistic synthetic fixtures in the failing test change**

  `audit-router-7.18.2.json` copies the current npm v2 shape: `react-router` has the direct advisory
  object and effect `react-router-dom`; `react-router-dom` has string cause `react-router`. Metadata
  `high` and `total` are both two. The minimal lock has lockfile version three and exact Router/DOM
  nodes plus the synthetic package used by `audit-unexpected-high.json`.

- [ ] **Step 2: Write the complete failing policy suite**

  Export this stable interface:

  ```js
  export class NpmAuditPolicyError extends Error {
    code;
  }

  export function assertSupportedNpmVersion(npmVersion) {}

  export function assessNpmAudit({
    npmVersion,
    exitCode,
    stdout,
    lockfile,
    exceptionPolicy,
    now,
  }) {}
  ```

  Tests must cover clean status zero, ignored low/moderate records, unexpected high/critical records,
  exact Router success, both version mutations, missing inherited record, additional object/string
  causes, additional effects, missing lock nodes, unrelated high beside Router, unused exception,
  immediately-before/exactly-at/after expiry, unsupported npm/audit/lock versions, malformed JSON,
  wrong field types, status two, and status/report contradictions.

- [ ] **Step 3: Run the policy suite to prove RED**

  Run: `node --test scripts/tests/test-npm-audit-policy.mjs`

  Expected: module-not-found failure for the policy implementation.

- [ ] **Step 4: Add the single exception data record**

  Create exactly:

  ```json
  {
    "schemaVersion": 1,
    "exceptions": [
      {
        "advisoryId": "GHSA-qwww-vcr4-c8h2",
        "rootPackage": "react-router",
        "advisoryUrl": "https://github.com/remix-run/react-router/security/advisories/GHSA-qwww-vcr4-c8h2",
        "reason": "The maintainer identifies 7.18.2 as patched while the global feed still models the v7 and v8 ranges continuously.",
        "expiresAt": "2026-08-22T00:00:00Z",
        "usagePolicy": "router-rsc-absent",
        "records": [
          {
            "package": "react-router",
            "version": "7.18.2",
            "isDirect": false,
            "causes": ["GHSA-qwww-vcr4-c8h2"],
            "effects": ["react-router-dom"]
          },
          {
            "package": "react-router-dom",
            "version": "7.18.2",
            "isDirect": true,
            "causes": ["react-router"],
            "effects": []
          }
        ]
      }
    ]
  }
  ```

- [ ] **Step 5: Implement strict schema, lock, atomic-record, and expiry evaluation**

  Validate before exception matching:

  - npm matches `/^10\.9\.\d+$/`, audit report is v2, lockfile is v3, and status is zero or one;
  - status one occurs exactly when at least one high/critical record exists;
  - metadata counts agree with vulnerability record severities;
  - every required record field has the documented type and every node resolves to a versioned lock path;
  - `fixAvailable` is boolean or npm's documented object shape;
  - duplicate-free causes/effects match the two exception records as sets;
  - both exception records apply together, every node is exactly 7.18.2, and `now < expiresAt`.

  Unknown extra fields may pass; missing or wrongly typed required fields fail closed. Return accepted
  records with `usagePolicy`, blocked records with stable reasons, and ignored info/low/moderate counts.

- [ ] **Step 6: Run policy checks and syntax validation**

  Run:

  ```bash
  node --check scripts/lib/npm-audit-policy.mjs
  node --test scripts/tests/test-npm-audit-policy.mjs
  ```

- [ ] **Step 7: Commit the reviewable policy core**

  ```bash
  git add .github/security/npm-audit-exceptions.json scripts/lib/npm-audit-policy.mjs scripts/tests/test-npm-audit-policy.mjs scripts/tests/fixtures/npm-audit
  git commit -m "ci: define the npm advisory policy"
  ```

### Task 8: Add the Router RSC usage policy

**Files:**
- Create: `scripts/lib/router-rsc-policy.mjs`
- Create: `scripts/tests/test-router-rsc-policy.mjs`

**Interfaces:**
- Consumes: frozen web TypeScript compiler, web manifest, Git-tracked first-party source files
- Produces: sorted `RouterRscViolation[]` for the `router-rsc-absent` policy

- [ ] **Step 1: Write the failing AST and file-selection suite**

  Require this interface:

  ```js
  export const ROUTER_RSC_USAGE_POLICY_ID = "router-rsc-absent";
  export function isTrackedWebSourcePath(repoRelativePath) {}
  export function inspectRouterRscInputs({ typescript, manifest, sources }) {}
  export function checkRouterRscUsage({ repoRoot, typescript, execFileSyncImpl }) {}
  ```

  A violation has stable code, path, one-based line/column, and optional symbol/specifier; diagnostics
  never contain source text. Test both forbidden manifest packages in dependencies and devDependencies,
  all six RSC APIs, alias import, named/star re-export, namespace/property/element access, CommonJS
  destructuring/property access, and every forbidden literal dynamic-import form. Also prove comments,
  strings, local names, unrelated modules, tests, fixtures, `_generated`, build output, node_modules, and
  untracked files do not match; parse/read/Git/TypeScript-resolution failures fail closed.

- [ ] **Step 2: Run the usage suite to prove RED**

  Run: `node --test scripts/tests/test-router-rsc-policy.mjs`

  Expected: module-not-found failure.

- [ ] **Step 3: Implement tracked-file selection and AST inspection**

  Use `git -C "$REPO_ROOT" ls-files -z -- apps/web/src`; admit only `.ts`, `.tsx`, `.js`, `.jsx`, `.mts`,
  `.mjs`, `.cts`, and `.cjs`; exclude all test/fixture/generated/build/install paths from the approved
  design. Load TypeScript with:

  ```js
  createRequire(path.join(repoRoot, "apps/web/package.json"))("typescript")
  ```

  Match the six named APIs by imported name (`propertyName ?? name`), Router namespace/CommonJS access,
  named/star re-exports, and forbidden literal dynamic imports. Sort by path, line, column, then code.

- [ ] **Step 4: Run the AST suite and inspect diagnostics**

  Run:

  ```bash
  node --check scripts/lib/router-rsc-policy.mjs
  node --test scripts/tests/test-router-rsc-policy.mjs
  ```

  Expected: all cases pass and failure diagnostics contain locations but no source bodies.

- [ ] **Step 5: Commit the usage-policy boundary**

  ```bash
  git add scripts/lib/router-rsc-policy.mjs scripts/tests/test-router-rsc-policy.mjs
  git commit -m "ci: guard the Router advisory exception"
  ```

### Task 9: Add the isolated npm runner and no-argument production CLI

**Files:**
- Create: `scripts/lib/npm-audit-runner.mjs`
- Create: `scripts/check-npm-audit.mjs`
- Create: `scripts/tests/test-npm-audit-runner.mjs`
- Create: `scripts/tests/test-check-npm-audit.mjs`

**Interfaces:**
- Consumes: Tasks 7-8 policy modules, fixed web lock/exception paths, production UTC clock
- Produces: `node scripts/check-npm-audit.mjs`; exit zero accepted, one blocked policy/RSC, two operational/contract failure

- [ ] **Step 1: Write the failing subprocess-boundary tests**

  Require these exported interfaces:

  ```js
  export class NpmAuditExecutionError extends Error {
    code;
  }

  export function getNpmVersion({
    npmExecutable = "npm",
    cwd,
    spawnSyncImpl,
  }) {}

  export function runNpmAudit({
    npmExecutable = "npm",
    webDirectory,
    cacheParent,
    spawnSyncImpl,
  }) {}
  ```

  Tests assert `npm --version` first, then exact argv
  `audit --package-lock-only --audit-level=high --json`; web CWD; a unique `npm_config_cache`;
  update-notifier suppression; bounded stdout/stderr; status one captured as data; spawn error, signal,
  and output overflow rejected; and cache removal after success and every failure.

- [ ] **Step 2: Run the runner suite to prove RED**

  Run: `node --test scripts/tests/test-npm-audit-runner.mjs`

  Expected: module-not-found failure.

- [ ] **Step 3: Implement subprocess execution without a shell**

  Use `spawnSync` argument arrays and a `mkdtemp` directory under the supplied cache parent. Validate
  that the cleanup target is the directory returned by `mkdtemp`, remove it in `finally`, and return
  `{ exitCode, stdout, stderr }` only for a normal status zero or one. Convert spawn errors, signals,
  absent statuses, and bounded-buffer failures into stable `NpmAuditExecutionError.code` values.

- [ ] **Step 4: Write a failing CLI orchestration test**

  Export an internal `main({ spawnSyncImpl, now, stdout, stderr })` for module tests, but expose no CLI
  arguments, path override, or time override. Assert production orchestration:

  1. resolves the repository from `import.meta.url`;
  2. reads only `apps/web/package-lock.json` and `.github/security/npm-audit-exceptions.json`;
  3. validates npm version before audit;
  4. assesses the report;
  5. runs `router-rsc-absent` only when the atomic exception is actually accepted;
  6. emits concise package/advisory/expiry summaries without source or environment data.

- [ ] **Step 5: Implement the production CLI and exit mapping**

  The executable entry calls `main` with the real `new Date()`. Map policy rejection, expiry, or an RSC
  violation to exit one; npm execution, JSON/schema, filesystem, Git, TypeScript resolution, or
  unsupported-version errors to exit two. Treat an unknown `usagePolicy` as operational failure.

- [ ] **Step 6: Run pure runner/CLI/policy checks**

  Run:

  ```bash
  node --check scripts/check-npm-audit.mjs
  node --check scripts/lib/npm-audit-runner.mjs
  node --test scripts/tests/test-npm-audit-runner.mjs scripts/tests/test-check-npm-audit.mjs scripts/tests/test-npm-audit-policy.mjs scripts/tests/test-router-rsc-policy.mjs
  ```

- [ ] **Step 7: Run the live lock-only gate under Node 22/npm 10.9.x**

  Run: `node scripts/check-npm-audit.mjs`

  Expected: exit zero with exactly one accepted advisory, the two atomic Router records, exact
  7.18.2 versions, expiry `2026-08-22T00:00:00Z`, and a clean RSC usage check.

- [ ] **Step 8: Commit the production audit entry point**

  ```bash
  git add scripts/lib/npm-audit-runner.mjs scripts/check-npm-audit.mjs scripts/tests/test-npm-audit-runner.mjs scripts/tests/test-check-npm-audit.mjs
  git commit -m "ci: enforce high severity npm policy"
  ```

### Task 10: Integrate the npm gate and reconcile active documentation

**Files:**
- Modify: `.github/workflows/ci.yml:243-293`
- Modify: `scripts/tests/test-ci-hardening.sh`
- Modify: `apps/api/tests/unit/test_ci_workflow.py`
- Modify: `apps/api/tests/unit/test_dependency_tooling.py`
- Modify: `justfile`
- Modify: `.claude/hooks/contract-lock-drift.sh:8-12,38`
- Modify: `CLAUDE.md:33,53,66-71`
- Modify: `README.md:89-130`
- Modify: `docs/dev-workflow.md:18-34`
- Modify: `docs/runbooks/fresh-linux-setup.md:49-55`

**Interfaces:**
- Consumes: Tasks 5-9 runners/policies and both committed npm locks
- Produces: hard-fail npm policy in the `security` job, local `just security-npm`, accurate contributor guidance

- [ ] **Step 1: Add failing exact workflow assertions**

  Extend Bash and parsed-YAML tests to require, in order after setup-node:

  ```yaml
  - name: install frozen web dependencies for npm policy
    working-directory: apps/web
    run: npm ci --ignore-scripts
  - name: npm advisory policy regressions
    run: |
      node --test \
        scripts/tests/test-web-security-lock.mjs \
        scripts/tests/test-npm-audit-runner.mjs \
        scripts/tests/test-check-npm-audit.mjs \
        scripts/tests/test-npm-audit-policy.mjs \
        scripts/tests/test-router-rsc-policy.mjs
  - name: npm advisory policy (web lock)
    run: node scripts/check-npm-audit.mjs
  ```

  Assert the npm steps have no `if`, `continue-on-error`, `|| true`, inline exception logic, or jq
  filter. Reject the old inline `npm audit` block. Assert the security preamble states that npm
  high/critical is gated while pip-audit and Trivy findings remain report-only.

- [ ] **Step 2: Run workflow tests to prove RED**

  Run:

  ```bash
  bash scripts/tests/test-ci-hardening.sh
  (cd apps/api && uv run pytest tests/unit/test_ci_workflow.py tests/unit/test_dependency_tooling.py -m unit -q)
  ```

  Expected: failures for missing install/tests/gate and stale warn-only wording.

- [ ] **Step 3: Replace the inline npm audit block**

  Keep `actions/setup-node@v7` with Node 22 and the web lock cache. Add the three exact steps from
  Step 1 and remove the old `set +e`, report redirection, and jq summary. Do not change Trivy's
  `exit-code: "0"` in this branch.

- [ ] **Step 4: Add the local npm security mirror**

  Add a `security-npm` Just recipe that runs:

  ```bash
  node --test scripts/tests/test-web-security-lock.mjs scripts/tests/test-npm-audit-runner.mjs scripts/tests/test-check-npm-audit.mjs scripts/tests/test-npm-audit-policy.mjs scripts/tests/test-router-rsc-policy.mjs
  node scripts/check-npm-audit.mjs
  ```

  It assumes `just setup` has already hydrated the frozen web and contract locks.

- [ ] **Step 5: Reconcile every active instruction and stale claim**

  - `CLAUDE.md` describes the contract lock and the mixed security posture rather than calling the
    entire job warn-only.
  - `docs/dev-workflow.md` names `packages/contracts/package-lock.json`, frozen setup, and
    `just security-npm`.
  - `.claude/hooks/contract-lock-drift.sh` keeps the early local reminder but removes both false claims
    that CI omits `gen-contracts.sh`.
  - `README.md` and the fresh-Linux runbook say `just setup` hydrates the separate contract lock.
  - Historical plans/specifications remain unchanged except the approved design's status and technical
    clarifications already made before this plan.

- [ ] **Step 6: Run integrated fast verification**

  Run:

  ```bash
  bash scripts/tests/test-ci-hardening.sh
  bash scripts/tests/test-run-contract-tool.sh
  bash scripts/tests/test-gen-contracts.sh
  bash scripts/tests/test-pip-audit-runner.sh
  node --test scripts/tests/test-contract-lock.mjs
  just security-npm
  (cd apps/api && uv run pytest tests/unit/test_ci_workflow.py tests/unit/test_dependency_tooling.py -m unit -q)
  bash scripts/check-no-site-data.sh
  ```

- [ ] **Step 7: Commit CI integration and active documentation**

  ```bash
  git add .github/workflows/ci.yml scripts/tests/test-ci-hardening.sh apps/api/tests/unit/test_ci_workflow.py apps/api/tests/unit/test_dependency_tooling.py justfile .claude/hooks/contract-lock-drift.sh CLAUDE.md README.md docs/dev-workflow.md docs/runbooks/fresh-linux-setup.md
  git commit -m "ci: integrate dependency security gates"
  ```

### Task 11: Prove real generation, run broad verification, and review the diff

**Files:**
- Verify: every changed file, generated ignored output, branch diff, and repository status
- Do not modify: `.codex/`, existing Dependabot pull requests, production hosts, or unrelated files

**Interfaces:**
- Consumes: integrated Tasks 1-10
- Produces: fresh acceptance evidence and a review-ready local branch

- [ ] **Step 1: Hydrate every frozen environment**

  Under Node 22/npm 10.9.x, run:

  ```bash
  npm ci --prefix packages/contracts --ignore-scripts
  npm ci --prefix apps/web
  (cd apps/api && uv sync --frozen)
  ```

- [ ] **Step 2: Prove real full-generation determinism from an unrelated Git repository**

  Run one full generation from the repository root. Hash these three files:

  ```text
  packages/contracts/dist/openapi.json
  apps/api/src/easysynq_api/_generated/models.py
  apps/web/src/api/_generated/schema.d.ts
  ```

  Create a guarded temporary directory, initialize an unrelated Git repository inside it, invoke the
  absolute `scripts/gen-contracts.sh` path from there, and hash the same outputs again. Require all
  three hashes to match. Then run:

  ```bash
  (cd apps/api && uv run python -m py_compile src/easysynq_api/_generated/models.py)
  (cd apps/web && npm run typecheck -- --listFiles)
  ```

  Require the TypeScript file list to contain `src/api/_generated/schema.d.ts`; require no diff in
  `packages/contracts/.contract.lock`.

- [ ] **Step 3: Run every focused tool and policy regression**

  Run:

  ```bash
  bash scripts/tests/test-run-contract-tool.sh
  bash scripts/tests/test-gen-contracts.sh
  bash scripts/tests/test-pip-audit-runner.sh
  bash scripts/tests/test-ci-hardening.sh
  node --test scripts/tests/test-contract-lock.mjs scripts/tests/test-web-security-lock.mjs scripts/tests/test-npm-audit-runner.mjs scripts/tests/test-check-npm-audit.mjs scripts/tests/test-npm-audit-policy.mjs scripts/tests/test-router-rsc-policy.mjs
  node scripts/check-npm-audit.mjs
  npm --prefix packages/contracts audit signatures
  pre-commit run contracts-lint --all-files
  ```

- [ ] **Step 4: Run affected project gates**

  Run:

  ```bash
  (cd apps/api && uv run ruff check .)
  (cd apps/api && uv run ruff format --check --diff .)
  (cd apps/api && uv run mypy src)
  (cd apps/api && uv run pytest tests/unit -m unit)
  (cd apps/web && npm test)
  (cd apps/web && npm run lint)
  (cd apps/web && npm run build)
  bash scripts/gen-contracts.sh --check
  bash scripts/tests/test-check-no-site-data.sh
  bash scripts/check-no-site-data.sh
  ```

- [ ] **Step 5: Inspect scope, locks, and ignored output**

  Run `git diff --check`, inspect every commit from the design base, and require:

  - no active floating Redocly, OpenAPI TypeScript, or pip-audit execution;
  - only the approved web package selections moved;
  - no generated output or contract `node_modules` is tracked;
  - no R61/site-specific content is present;
  - `.codex/` remains the sole pre-existing untracked path;
  - no Action SHA, Trivy baseline, web image, or branch-rule change entered the diff.

- [ ] **Step 6: Request independent code and spec review**

  Invoke `superpowers:requesting-code-review` plus the repository's read-only diff critic. Resolve every
  material finding through the originating task's implementer, rerun the smallest distinguishing test,
  then rerun the affected broad gate.

- [ ] **Step 7: Stop at the publication boundary**

  Report the branch, commits, exact test counts/results, accepted Router exception/expiry, and any
  environment-limited check. Do not push or open a pull request until the owner authorizes publication.

### Task 12: Enable Dependabot vulnerability automation after publication is green

**Files:**
- External repository settings only; no source file changes

**Interfaces:**
- Consumes: owner-authorized pushed branch/PR with green CI and repository-admin GitHub credentials
- Produces: vulnerability alerts enabled and automated security fixes `{ enabled: true, paused: false }`

- [ ] **Step 1: Confirm the publication precondition**

  Require the implementation PR's full CI run to be green. Reconfirm that existing Dependabot PRs are
  not being edited and that security-update grouping/auto-merge remain disabled.

- [ ] **Step 2: Enable alerts and security updates with the current REST version**

  Run:

  ```bash
  gh api --method PUT --silent \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    repos/CoJoA13/EasySynQ/vulnerability-alerts

  gh api --method PUT --silent \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    repos/CoJoA13/EasySynQ/automated-security-fixes
  ```

  Both calls must return success; do not retry with broader credentials or another endpoint after an
  authorization failure.

- [ ] **Step 3: Read both settings back fail-closed**

  Run the vulnerability-alerts GET and require HTTP 204. Read automated security fixes and require:

  ```json
  {
    "enabled": true,
    "paused": false
  }
  ```

  Then query open Dependabot alerts/PRs only for reporting. Do not close, group, merge, or modify them.

- [ ] **Step 4: Report the external effect**

  Record that alerts and automated fixes are enabled, list any newly opened security PR numbers without
  changing them, and call out that disabling the settings would require a separate explicit owner action.
