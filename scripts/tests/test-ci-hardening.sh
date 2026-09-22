#!/usr/bin/env bash
# Structural regression for .github/workflows/ci.yml, the gate (R86).
#
# Two kinds of assertion live here. The first is policy that has held across three hosting moves:
# the hard-fail discipline, the exact test-tree collection, the shard topology, the ordering of
# the cheap guards ahead of dependency hydration. The second encodes a bug a CI port actually hit —
# each is pinned because the symptom never named the cause, and because nothing else looks here.
#
# Bash + grep only, so it runs before project dependencies are hydrated and can guard the workflow
# that hydrates them. The parsed-YAML semantics live in apps/api/tests/unit/test_ci_workflow.py.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKFLOW="$ROOT/.github/workflows/ci.yml"
PATHS="$ROOT/.github/ci-paths.yml"
FILTER="$ROOT/scripts/ci-changed-paths.py"
WEB_CONFIG="$ROOT/apps/web/vite.config.ts"
WEB_PACKAGE="$ROOT/apps/web/package.json"
JUSTFILE="$ROOT/justfile"
SEMANTIC_TEST="$ROOT/apps/api/tests/unit/test_ci_workflow.py"
PIP_AUDIT_RUNNER="$ROOT/scripts/run-pip-audit.sh"
PASS=0
FAIL=0

ok()  { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

assert_contains() {
  local label="$1" file="$2" needle="$3"
  if grep -Fq -- "$needle" "$file"; then ok "$label"; else bad "$label (missing: $needle)"; fi
}
assert_not_contains() {
  local label="$1" file="$2" needle="$3"
  if grep -Fq -- "$needle" "$file"; then bad "$label (unexpected: $needle)"; else ok "$label"; fi
}
job_block() {
  local job="$1"
  awk -v heading="  $job:" '
    $0 == heading { inside = 1 }
    inside && $0 ~ /^  [[:alnum:]_-]+:$/ && $0 != heading { exit }
    inside { print }
  ' "$WORKFLOW"
}
assert_text_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) ok "$label" ;;
    *) bad "$label (missing from job: $needle)" ;;
  esac
}
assert_text_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) bad "$label (unexpected in job: $needle)" ;;
    *) ok "$label" ;;
  esac
}
assert_before() {
  local label="$1" haystack="$2" first="$3" second="$4" first_line second_line
  first_line="$(grep -nF -m1 -- "$first" <<<"$haystack" | cut -d: -f1)"
  second_line="$(grep -nF -m1 -- "$second" <<<"$haystack" | cut -d: -f1)"
  if [ -n "$first_line" ] && [ -n "$second_line" ] && [ "$first_line" -lt "$second_line" ]; then
    ok "$label"
  else
    bad "$label"
  fi
}

printf '== ci hardening contract ==\n'

CHANGES_BLOCK="$(job_block changes)"
CONTRACTS_BLOCK="$(job_block contracts)"
COMPOSE_LOCK_BLOCK="$(job_block compose-images-lock)"
API_BLOCK="$(job_block api)"
MIGRATIONS_BLOCK="$(job_block migrations)"
SECURITY_BLOCK="$(job_block security)"
DOCS_BLOCK="$(job_block docs-tests)"
CONTRACT_RESPONSES_BLOCK="$(job_block contract-responses)"
INTEGRATION_SHARDS_BLOCK="$(job_block integration-shards)"
WEB_SHARDS_BLOCK="$(job_block web-shards)"
WEB_BROWSER_BLOCK="$(job_block web-browser)"
RELEASE_BLOCK="$(job_block release-gate)"
GATE_BLOCK="$(job_block gate)"

# ---- hard-fail discipline -------------------------------------------------------------------------
assert_not_contains "no job or step may continue on error" "$WORKFLOW" "continue-on-error"
assert_not_contains "no command may suppress its own failure" "$WORKFLOW" "|| true"
assert_not_contains "no GitLab escape hatch survives the port" "$WORKFLOW" "allow_failure"
assert_not_contains "the unprivileged runner needs no privilege drop" "$WORKFLOW" "runuser"
assert_not_contains "no step retries a flaky suite into green" "$WORKFLOW" "retries:"
assert_not_contains "no retired hosting CLI in the workflow" "$WORKFLOW" "glab "

# ---- triggers, cancellation, permissions ---------------------------------------------------------
assert_contains "pull requests are the only branch trigger" "$WORKFLOW" $'on:\n  push:\n    branches: [main]'
assert_contains "version tags trigger the release gate" "$WORKFLOW" "    tags: ['v*']"
assert_contains "manual runs exist so main can get a full run on demand" "$WORKFLOW" "  workflow_dispatch:"
assert_contains "only pull-request runs are superseded by a newer commit" "$WORKFLOW" \
  "cancel-in-progress: \${{ github.event_name == 'pull_request' }}"
assert_contains "main runs each get a unique concurrency group" "$WORKFLOW" \
  "group: ci-\${{ github.event_name == 'pull_request' && github.ref || github.run_id }}"
assert_contains "the token is read-only" "$WORKFLOW" $'permissions:\n  contents: read'

# ---- the path filter ------------------------------------------------------------------------------
assert_text_contains "the filter job needs full history to diff against the base" \
  "$CHANGES_BLOCK" "fetch-depth: 0"
assert_text_contains "the filter job runs the tracked decision script" \
  "$CHANGES_BLOCK" "run: python3 scripts/ci-changed-paths.py"
assert_text_contains "the filter job passes the event, base ref and ref" \
  "$CHANGES_BLOCK" "EVENT: \${{ github.event_name }}"
assert_contains "the decision script reads the tracked path lists" "$FILTER" '.github" / "ci-paths.yml'
assert_contains "the path lists file exists with a code list" "$PATHS" $'code:\n  - .claude/**'
assert_contains "a workflow edit exercises the API suites" "$PATHS" $'api_suites:\n  - .github/**'
assert_contains "a workflow edit exercises the web suites" "$PATHS" $'web_suites:\n  - .github/**'
for job in api contract-responses integration-shards web-shards web-browser docs-tests migrations security; do
  block="$(job_block "$job")"
  assert_text_contains "$job depends on the filter job" "$block" "    needs: changes"
  assert_text_contains "$job is gated on a filter output" "$block" "    if: needs.changes.outputs."
done
assert_text_contains "api runs for any non-docs change" "$API_BLOCK" "if: needs.changes.outputs.code == 'true'"
assert_text_contains "migrations also runs as a post-merge backstop" \
  "$MIGRATIONS_BLOCK" "if: needs.changes.outputs.code == 'true' || needs.changes.outputs.main_push == 'true'"
assert_text_contains "security also runs as a post-merge backstop" \
  "$SECURITY_BLOCK" "if: needs.changes.outputs.code == 'true' || needs.changes.outputs.main_push == 'true'"
assert_text_contains "the docs lane runs exactly when nothing else is owed" \
  "$DOCS_BLOCK" "if: needs.changes.outputs.docs_only == 'true'"
for job in contracts compose-images-lock; do
  block="$(job_block "$job")"
  assert_text_not_contains "$job runs on every run (no needs)" "$block" "    needs:"
  assert_text_not_contains "$job runs on every run (no if)" "$block" "    if:"
done

# ---- the one required check -------------------------------------------------------------------------
assert_text_contains "gate always evaluates" "$GATE_BLOCK" "    if: \${{ always() }}"
for job in changes contracts compose-images-lock api migrations security docs-tests contract-responses integration-shards web-shards web-browser release-gate; do
  assert_text_contains "gate needs $job" "$GATE_BLOCK" "      - $job"
  assert_text_contains "gate judges $job" "$GATE_BLOCK" "owed $job"
done
assert_text_contains "gate fails on a job that owed a result and did not succeed" \
  "$GATE_BLOCK" 'echo "::error::$job owed a result and reported $result"; failed=1'
assert_text_contains "gate fails on any non-skipped failure even when not owed" \
  "$GATE_BLOCK" 'echo "::error::$job reported $result"; failed=1'
assert_text_contains "gate exits with its verdict" "$GATE_BLOCK" 'exit "$failed"'

# ---- each suite collects only its authoritative tree ------------------------------------------------
assert_text_contains "unit job collects only the authoritative unit tree" "$API_BLOCK" \
  $'      - name: unit tests\n        working-directory: apps/api\n        env:\n          EASYSYNQ_IMAGE_PROOF: "1"\n        run: uv run pytest tests/unit -m unit'
assert_text_contains "api runs the mandatory runtime acceptance after the unit suite" \
  "$API_BLOCK" "run: python3 scripts/run-audit-external-acceptance.py"
assert_before "the built-image proof precedes the runtime acceptance" "$API_BLOCK" \
  "      - name: unit tests" "      - name: mandatory runtime acceptance (built image)"
assert_text_contains "api runs the hosting and tooling contracts" "$API_BLOCK" \
  $'          bash scripts/tests/test-contributor-hosting.sh\n          python3 scripts/tests/test-refresh-test-durations.py'
assert_text_contains "integration shards collect only the authoritative integration tree" \
  "$INTEGRATION_SHARDS_BLOCK" \
  $'          uv run pytest tests/integration -m integration --splits 4 --group ${{ matrix.group }}\n          --durations-path .test_durations --store-durations --clean-durations'
assert_text_contains "integration keeps exactly four shards" "$INTEGRATION_SHARDS_BLOCK" "        group: [1, 2, 3, 4]"
assert_text_contains "contract job collects only the response-contract module" "$CONTRACT_RESPONSES_BLOCK" \
  "run: uv run pytest tests/integration/test_contract_response_schemas.py -m contract --tb=short"
assert_text_contains "migrations runs the whole migration suite, not one file" \
  "$MIGRATIONS_BLOCK" "run: uv run pytest tests/migration"
assert_text_not_contains "migrations does not narrow the suite to a single module" \
  "$MIGRATIONS_BLOCK" "pytest tests/migration/"
assert_text_contains "migrations proves the round trip" "$MIGRATIONS_BLOCK" \
  "run: uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head"
assert_text_contains "migrations checks model/migration drift" "$MIGRATIONS_BLOCK" "run: uv run alembic check"
assert_text_contains "migrations reaches its service on localhost (GitHub services)" \
  "$MIGRATIONS_BLOCK" "@localhost:5432/easysynq"
assert_text_contains "the docs lane selects tests by content" "$DOCS_BLOCK" \
  'files="$(grep -rlE "$DOCS_TEST_PATTERN" tests/unit --include="test_*.py" | sort)"'
assert_text_contains "the docs lane fails closed on an empty selection" "$DOCS_BLOCK" 'test -n "$files"'
assert_text_contains "the docs lane materializes the env file the Compose renderers need" "$DOCS_BLOCK" \
  "run: cp .env.example .env"
assert_before "the docs lane materializes .env before its unit tests" "$DOCS_BLOCK" \
  "run: cp .env.example .env" "uv run pytest \$files -m unit"

# ---- the postgres client trap (hit twice) -------------------------------------------------------------
assert_text_contains "integration installs the matching postgres client major" \
  "$INTEGRATION_SHARDS_BLOCK" "postgresql-client-18"
assert_text_contains "integration pins the versioned bin dir ahead of pg_wrapper" \
  "$INTEGRATION_SHARDS_BLOCK" 'echo /usr/lib/postgresql/18/bin >> "$GITHUB_PATH"'
assert_text_contains "integration asserts the client actually wins PATH in its own step" \
  "$INTEGRATION_SHARDS_BLOCK" "run: pg_dump --version | grep -qE '\\) 18\\.' || { pg_dump --version; exit 1; }"

# ---- web suites ----------------------------------------------------------------------------------------
assert_text_contains "web suite has exactly two hard-fail shards" "$WEB_SHARDS_BLOCK" \
  $'    strategy:\n      fail-fast: false\n      matrix:\n        shard: [1, 2]'
assert_text_not_contains "web matrix has no include override" "$WEB_SHARDS_BLOCK" "        include:"
assert_text_not_contains "web matrix has no exclude override" "$WEB_SHARDS_BLOCK" "        exclude:"
assert_text_not_contains "web shards cannot select changed files only" "$WEB_SHARDS_BLOCK" "--changed"
assert_text_contains "each web shard runs an unconditional complete Vitest partition" "$WEB_SHARDS_BLOCK" \
  $'      - name: Vitest shard ${{ matrix.shard }}/2\n        working-directory: apps/web\n        run: npm test -- --shard=${{ matrix.shard }}/2'
assert_text_contains "exactly shard 2 keeps the post-test lint and build gate" "$WEB_SHARDS_BLOCK" \
  $'      - name: lint and build\n        if: ${{ !cancelled() && matrix.shard == 2 }}\n        working-directory: apps/web\n        run: npm run lint && npm run build'
assert_before "web tests run before lint and build" "$WEB_SHARDS_BLOCK" \
  '      - name: Vitest shard ${{ matrix.shard }}/2' "      - name: lint and build"
assert_text_contains "browser job preserves its stable Chromium display name" "$WEB_BROWSER_BLOCK" \
  "    name: web browser (Chromium)"
assert_text_contains "browser job installs Chromium and required Linux packages" "$WEB_BROWSER_BLOCK" \
  "run: npx playwright install --with-deps chromium"
assert_text_contains "browser job runs the locked browser suite" "$WEB_BROWSER_BLOCK" "run: npm run test:browser"
assert_text_not_contains "browser job cannot override the locked zero-retry policy" "$WEB_BROWSER_BLOCK" "--retries"
assert_text_not_contains "browser job cannot select changed files only" "$WEB_BROWSER_BLOCK" "--changed"
assert_text_contains "browser diagnostics upload only on failure" "$WEB_BROWSER_BLOCK" \
  $'      - name: upload browser diagnostics\n        if: ${{ failure() }}'
assert_not_contains "web static gate does not run TypeScript twice" "$WORKFLOW" "npm run typecheck"
assert_contains "web build remains the single TypeScript gate" "$WEB_PACKAGE" '"build": "tsc --noEmit && vite build"'

# ---- contracts job ordering (guards before hydration) --------------------------------------------------
assert_text_contains "contracts runs authority and Claude compatibility contracts" "$CONTRACTS_BLOCK" \
  $'          bash scripts/tests/test-agent-authority.sh\n          bash scripts/tests/test-claude-hooks.sh\n          ./scripts/check-repo-authority.sh'
assert_text_contains "contracts runs the R61 backstop harness then the backstop" "$CONTRACTS_BLOCK" \
  $'      - name: R61 backstop regression harness\n        run: bash scripts/tests/test-check-no-site-data.sh\n      - name: R61 site-data backstop (check-no-site-data)\n        run: ./scripts/check-no-site-data.sh'
assert_text_contains "contracts runs the doctor shell contracts" "$CONTRACTS_BLOCK" "run: bash scripts/tests/test-doctor.sh"
assert_text_contains "contracts proves the PostgreSQL MCP path stays disabled" "$CONTRACTS_BLOCK" \
  "run: node --test scripts/tests/test-postgres-mcp-disabled.mjs"
assert_text_contains "contracts runs THIS workflow's regression" "$CONTRACTS_BLOCK" \
  $'      - name: CI workflow contract\n        run: |\n          bash scripts/tests/test-ci-hardening.sh\n          bash scripts/tests/test-check-compose-images-lock.sh'
assert_text_contains "contracts installs locked tools without lifecycle scripts" "$CONTRACTS_BLOCK" \
  "run: npm ci --prefix packages/contracts --ignore-scripts"
assert_text_contains "contracts proves the locked toolchain before linting" "$CONTRACTS_BLOCK" \
  $'          bash scripts/tests/test-run-contract-tool.sh\n          node --test scripts/tests/test-contract-lock.mjs\n          bash scripts/tests/test-gen-contracts.sh'
assert_text_contains "contracts lints through the locked wrapper" "$CONTRACTS_BLOCK" \
  "run: bash scripts/run-contract-tool.sh redocly lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml"
assert_text_contains "contracts audits the locked dependency graph" "$CONTRACTS_BLOCK" \
  "run: npm --prefix packages/contracts audit --package-lock-only --audit-level=high"
assert_text_contains "contracts checks the committed contract lock" "$CONTRACTS_BLOCK" "run: bash scripts/gen-contracts.sh --check"
assert_text_not_contains "contracts does not run floating npx contract tools" "$CONTRACTS_BLOCK" "npx"
assert_before "authority contracts run before contract tool hydration" "$CONTRACTS_BLOCK" \
  "      - name: Agent authority and Claude compatibility contracts" "      - uses: actions/setup-node@v7"
assert_before "R61 regression runs before contract tool hydration" "$CONTRACTS_BLOCK" \
  "      - name: R61 backstop regression harness" "      - uses: actions/setup-node@v7"
assert_before "site-data backstop stays ahead of the doctor contracts" "$CONTRACTS_BLOCK" \
  "      - name: R61 site-data backstop (check-no-site-data)" "      - name: doctor shell contracts"
assert_before "workflow regression runs before contract tool hydration" "$CONTRACTS_BLOCK" \
  "      - name: CI workflow contract" "      - name: install locked contract tools"
assert_before "contract audit runs before generated-lock verification" "$CONTRACTS_BLOCK" \
  "      - name: audit locked contract tools" "      - name: generated contract lock"
assert_text_contains "compose lock guard runs the digest-aware checker" "$COMPOSE_LOCK_BLOCK" \
  "run: bash scripts/check-compose-images-lock.sh"

# ---- security (gated; built artifacts, not base refs) ---------------------------------------------------
assert_text_contains "security runs the pip-audit regression then the locked runner" "$SECURITY_BLOCK" \
  $'      - name: pip-audit runner regressions\n        run: bash scripts/tests/test-pip-audit-runner.sh\n      - name: pip-audit (Python deps, resolved from uv.lock)\n        run: bash scripts/run-pip-audit.sh'
assert_text_contains "security installs the frozen web tree without lifecycle scripts" "$SECURITY_BLOCK" \
  $'      - name: install frozen web dependencies for npm policy\n        working-directory: apps/web\n        run: npm ci --ignore-scripts'
assert_text_contains "security runs the exact npm advisory regression matrix" "$SECURITY_BLOCK" \
  $'            scripts/tests/test-web-security-lock.mjs \\\n            scripts/tests/test-npm-audit-runner.mjs \\\n            scripts/tests/test-check-npm-audit.mjs \\\n            scripts/tests/test-npm-audit-policy.mjs \\\n            scripts/tests/test-router-rsc-policy.mjs'
assert_text_contains "security runs the live web-lock policy gate" "$SECURITY_BLOCK" "run: node scripts/check-npm-audit.mjs"
assert_text_not_contains "security does not invoke raw npm audit on the web lock" "$SECURITY_BLOCK" "npm audit "
assert_text_not_contains "security does not write an audit report under a runner temp" "$SECURITY_BLOCK" "RUNNER_TEMP"
assert_text_contains "security uses the pinned trivy binary" "$SECURITY_BLOCK" "docker create aquasec/trivy:0.74.0"
assert_text_not_contains "security does not use the trivy action (its env is not ours to control)" \
  "$SECURITY_BLOCK" "aquasecurity/trivy-action"
assert_text_contains "the filesystem scan stays advisory" "$SECURITY_BLOCK" \
  "run: trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --exit-code 0 --format table ."
assert_text_contains "security runs the built-image behavioral regression" "$SECURITY_BLOCK" \
  "run: bash scripts/tests/test-built-image-security.sh"
assert_text_contains "security builds the API artifact with its production context" "$SECURITY_BLOCK" \
  "docker build -f apps/api/Dockerfile -t easysynq-api:scan ."
assert_text_contains "security builds the web artifact with its production context" "$SECURITY_BLOCK" \
  "docker build -f apps/web/Dockerfile -t easysynq-web:scan apps/web"
assert_text_not_contains "security cannot substitute an extracted base for the built web artifact" \
  "$SECURITY_BLOCK" "grep -E '^FROM '"
assert_text_contains "security gates both built images through the report-validating runner" \
  "$SECURITY_BLOCK" "run: bash scripts/check-built-image-security.sh"
assert_before "the built-image regression runs before the gate" "$SECURITY_BLOCK" \
  "      - name: built-image gate regressions" "      - name: built-image fixed-version threshold (gated)"
assert_before "the live npm gate completes before the first trivy scan" "$SECURITY_BLOCK" \
  "      - name: npm advisory policy (web lock)" "      - name: install the pinned trivy"
assert_contains "pip-audit runner exports the frozen default graph without its security tool group" \
  "$PIP_AUDIT_RUNNER" 'uv export --frozen --no-group security --no-emit-project \'
assert_contains "pip-audit executes only through the frozen security group" "$PIP_AUDIT_RUNNER" \
  'uv run --frozen --only-group security pip-audit \'
assert_not_contains "active workflow rejects floating pip-audit" "$WORKFLOW" "uvx pip-audit"

# ---- release gate ----------------------------------------------------------------------------------------
assert_text_contains "release gate stays tag-only" "$RELEASE_BLOCK" "if: startsWith(github.ref, 'refs/tags/v')"
assert_text_contains "release gate sets the digest-pin opt-in" "$RELEASE_BLOCK" 'EASYSYNQ_RELEASE: "1"'
assert_text_contains "release gate runs the pin check" "$RELEASE_BLOCK" "test_images_lock_pinned.py"

# ---- local mirrors -------------------------------------------------------------------------------------------
assert_contains "local CI mirror keeps the direct unit root" "$JUSTFILE" "uv run pytest tests/unit -m unit"
assert_contains "local CI mirror keeps one TypeScript pass before web tests" "$JUSTFILE" "npm run lint && npm run build && npm test"
assert_contains "local browser command invokes the locked web script" "$JUSTFILE" $'test-browser:\n    cd apps/web && npm run test:browser'
assert_contains "local contract mirror collects only the response-contract module" "$JUSTFILE" \
  "uv run pytest tests/integration/test_contract_response_schemas.py -m contract"
assert_contains "dependency-free gate retains the parsed workflow regression" "$SEMANTIC_TEST" \
  "def test_ci_workflow_preserves_complete_hard_fail_gates"
assert_contains "web shards retain deterministic one-worker isolation" "$WEB_CONFIG" "    maxWorkers: 1,"
assert_contains "web shards retain fork-process isolation" "$WEB_CONFIG" '    pool: "forks",'
assert_not_contains "web shards do not disable per-file isolation" "$WEB_CONFIG" "isolate: false"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
