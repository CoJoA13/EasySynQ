#!/usr/bin/env bash
# Structural regression for the CI orchestration that keeps the expensive suites complete while
# distributing their work. This intentionally uses only Bash + grep so it can run before project
# dependencies are hydrated and can guard the workflow that hydrates them.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKFLOW="$ROOT/.github/workflows/ci.yml"
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
  if grep -Fq -- "$needle" "$file"; then
    ok "$label"
  else
    bad "$label (missing: $needle)"
  fi
}

assert_not_contains() {
  local label="$1" file="$2" needle="$3"
  if grep -Fq -- "$needle" "$file"; then
    bad "$label (unexpected: $needle)"
  else
    ok "$label"
  fi
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

WEB_SHARDS_BLOCK="$(job_block web-shards)"
WEB_BROWSER_BLOCK="$(job_block web-browser)"
WEB_GATE_BLOCK="$(job_block web)"
CONTRACTS_BLOCK="$(job_block contracts)"
CONTRACT_RESPONSES_BLOCK="$(job_block contract-responses)"
API_BLOCK="$(job_block api)"
INTEGRATION_SHARDS_BLOCK="$(job_block integration-shards)"
SECURITY_BLOCK="$(job_block security)"

assert_text_contains \
  "web suite has exactly two hard-fail shards" \
  "$WEB_SHARDS_BLOCK" \
  '    strategy:
      fail-fast: false
      matrix:
        shard: [1, 2]'
assert_text_not_contains "web matrix has no include override" "$WEB_SHARDS_BLOCK" "        include:"
assert_text_not_contains "web matrix has no exclude override" "$WEB_SHARDS_BLOCK" "        exclude:"
assert_text_not_contains "web shards cannot continue on error" "$WEB_SHARDS_BLOCK" "continue-on-error:"
assert_text_not_contains "web shard commands cannot suppress failures" "$WEB_SHARDS_BLOCK" "|| true"
assert_text_not_contains "web shards cannot select changed files only" "$WEB_SHARDS_BLOCK" "--changed"
assert_text_contains \
  "each web shard runs an unconditional complete Vitest partition" \
  "$WEB_SHARDS_BLOCK" \
  '      - name: Vitest shard ${{ matrix.shard }}/2
        working-directory: apps/web
        run: npm test -- --shard=${{ matrix.shard }}/2'

assert_text_contains \
  "browser job preserves its stable Chromium display name" \
  "$WEB_BROWSER_BLOCK" \
  '  web-browser:
    name: web browser (Chromium)
    runs-on: ubuntu-latest'
assert_text_contains \
  "browser job checks out before selecting cached Node 22" \
  "$WEB_BROWSER_BLOCK" \
  '      - uses: actions/checkout@v7
      - uses: actions/setup-node@v7
        with:
          node-version: "26"
          cache: npm
          cache-dependency-path: apps/web/package-lock.json'
assert_text_contains \
  "browser job installs the frozen web dependency tree" \
  "$WEB_BROWSER_BLOCK" \
  '      - working-directory: apps/web
        run: npm ci'
assert_text_contains \
  "browser job installs Chromium and required Linux packages" \
  "$WEB_BROWSER_BLOCK" \
  '      - name: install Chromium
        working-directory: apps/web
        run: npx playwright install --with-deps chromium'
assert_text_contains \
  "browser job runs the locked browser suite unconditionally" \
  "$WEB_BROWSER_BLOCK" \
  '      - name: responsive browser evidence
        working-directory: apps/web
        run: npm run test:browser'
assert_text_not_contains \
  "browser job cannot continue on error" \
  "$WEB_BROWSER_BLOCK" \
  "continue-on-error:"
assert_text_not_contains \
  "browser job commands cannot suppress failures" \
  "$WEB_BROWSER_BLOCK" \
  "|| true"
assert_text_not_contains \
  "browser job cannot select changed files only" \
  "$WEB_BROWSER_BLOCK" \
  "--changed"
assert_text_not_contains \
  "browser job cannot override the locked zero-retry policy" \
  "$WEB_BROWSER_BLOCK" \
  "--retries"
assert_text_not_contains \
  "browser job cannot add workflow-level test retries" \
  "$WEB_BROWSER_BLOCK" \
  "retries:"
assert_text_contains \
  "browser diagnostics upload only on failure" \
  "$WEB_BROWSER_BLOCK" \
  '      - name: upload browser diagnostics
        if: ${{ failure() }}
        uses: actions/upload-artifact@v6
        with:
          name: playwright-report
          path: |
            apps/web/playwright-report
            apps/web/test-results
          if-no-files-found: ignore
          retention-days: 7'
assert_text_contains \
  "stable named web gate runs after every unit and browser result" \
  "$WEB_GATE_BLOCK" \
  '  web:
    name: web
    needs: [web-shards, web-browser]
    if: ${{ always() }}
    runs-on: ubuntu-latest'
assert_text_not_contains "stable web gate cannot continue on error" "$WEB_GATE_BLOCK" "continue-on-error:"
assert_text_contains \
  "stable web gate rejects every non-success result with a failing exit" \
  "$WEB_GATE_BLOCK" \
  '          shards_result='"'"'${{ needs.web-shards.result }}'"'"'
          browser_result='"'"'${{ needs.web-browser.result }}'"'"'
          if [ "$shards_result" != "success" ] || [ "$browser_result" != "success" ]; then
            echo "web checks did not all pass (web-shards=$shards_result, web-browser=$browser_result)"
            exit 1
          fi'
assert_text_contains \
  "exactly shard 2 keeps the post-test lint and build gate" \
  "$WEB_SHARDS_BLOCK" \
  '      - name: lint and build
        if: ${{ !cancelled() && matrix.shard == 2 }}
        working-directory: apps/web
        run: npm run lint && npm run build'
assert_before \
  "web tests run before lint and build" \
  "$WEB_SHARDS_BLOCK" \
  '      - name: Vitest shard ${{ matrix.shard }}/2' \
  "      - name: lint and build"
assert_not_contains \
  "web static gate does not run TypeScript twice" \
  "$WORKFLOW" \
  "npm run typecheck"
assert_contains \
  "web build remains the single TypeScript gate" \
  "$WEB_PACKAGE" \
  '"build": "tsc --noEmit && vite build"'
assert_contains \
  "local CI mirror keeps the direct unit root" \
  "$JUSTFILE" \
  "uv run pytest tests/unit -m unit"
assert_contains \
  "local CI mirror keeps one TypeScript pass before web tests" \
  "$JUSTFILE" \
  "npm run lint && npm run build && npm test"
assert_contains \
  "local browser command invokes the locked web script" \
  "$JUSTFILE" \
  $'test-browser:\n    cd apps/web && npm run test:browser'
assert_contains \
  "dependency-free gate retains the parsed workflow regression" \
  "$SEMANTIC_TEST" \
  "def test_ci_workflow_preserves_complete_hard_fail_gates"

assert_text_contains \
  "unit job collects only the authoritative unit tree" \
  "$API_BLOCK" \
  '      - name: unit tests
        working-directory: apps/api
        run: uv run pytest tests/unit -m unit'
assert_text_contains \
  "integration shards collect only the authoritative integration tree" \
  "$INTEGRATION_SHARDS_BLOCK" \
  '      - name: integration tests (shard ${{ matrix.group }}/4, testcontainers spin their own Postgres)
        working-directory: apps/api
        run: >-
          uv run pytest tests/integration -m integration --splits 4 --group ${{ matrix.group }}
          --durations-path .test_durations --store-durations --clean-durations'
assert_text_contains \
  "contract job collects only the response-contract module" \
  "$CONTRACT_RESPONSES_BLOCK" \
  '      - name: validate authenticated operational responses (disposable testcontainers only)
        working-directory: apps/api
        run: uv run pytest tests/integration/test_contract_response_schemas.py -m contract --tb=short'
assert_contains \
  "local contract mirror collects only the response-contract module" \
  "$JUSTFILE" \
  "uv run pytest tests/integration/test_contract_response_schemas.py -m contract"

assert_text_contains \
  "contracts job checks the committed contract lock" \
  "$CONTRACTS_BLOCK" \
  '      - name: generated contract lock
        run: bash scripts/gen-contracts.sh --check'
assert_text_contains \
  "contracts job runs this workflow regression" \
  "$CONTRACTS_BLOCK" \
  '      - name: CI workflow contract
        run: |
          bash scripts/tests/test-ci-hardening.sh
          bash scripts/tests/test-check-compose-images-lock.sh'
assert_text_contains \
  "contracts job runs authority and Claude compatibility contracts" \
  "$CONTRACTS_BLOCK" \
  '      - name: Agent authority and Claude compatibility contracts
        run: |
          bash scripts/tests/test-agent-authority.sh
          bash scripts/tests/test-claude-hooks.sh
          ./scripts/check-repo-authority.sh'
assert_text_contains \
  "contracts job runs the doctor shell contracts" \
  "$CONTRACTS_BLOCK" \
  '      - name: doctor shell contracts
        run: bash scripts/tests/test-doctor.sh'
assert_text_contains \
  "contracts job proves the PostgreSQL MCP path stays disabled" \
  "$CONTRACTS_BLOCK" \
  '      - name: PostgreSQL MCP disabled contract
        run: node --test scripts/tests/test-postgres-mcp-disabled.mjs'
assert_text_not_contains \
  "contracts job does not run floating npx contract tools" \
  "$CONTRACTS_BLOCK" \
  "npx"
assert_text_contains \
  "contracts job pins Node and caches the contract lock" \
  "$CONTRACTS_BLOCK" \
  '      - uses: actions/setup-node@v7
        with:
          node-version: "26"
          cache: npm
          cache-dependency-path: packages/contracts/package-lock.json'
assert_text_contains \
  "contracts job installs locked tools without lifecycle scripts" \
  "$CONTRACTS_BLOCK" \
  '      - name: install locked contract tools
        run: npm ci --prefix packages/contracts --ignore-scripts'
assert_text_contains \
  "contracts job proves the locked toolchain before linting" \
  "$CONTRACTS_BLOCK" \
  '      - name: contract toolchain regressions
        run: |
          bash scripts/tests/test-run-contract-tool.sh
          node --test scripts/tests/test-contract-lock.mjs
          bash scripts/tests/test-gen-contracts.sh'
assert_text_contains \
  "contracts job lints through the locked wrapper" \
  "$CONTRACTS_BLOCK" \
  '      - name: lint OpenAPI
        run: bash scripts/run-contract-tool.sh redocly lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml'
assert_text_contains \
  "contracts job audits the locked dependency graph" \
  "$CONTRACTS_BLOCK" \
  '      - name: audit locked contract tools
        run: npm --prefix packages/contracts audit --package-lock-only --audit-level=high'
assert_before \
  "authority and Claude contracts run before contract tool hydration" \
  "$CONTRACTS_BLOCK" \
  "      - name: Agent authority and Claude compatibility contracts" \
  "      - uses: actions/setup-node@v7"
assert_before \
  "R61 regression runs before contract tool hydration" \
  "$CONTRACTS_BLOCK" \
  "      - name: R61 backstop regression harness" \
  "      - uses: actions/setup-node@v7"
assert_before \
  "doctor shell contracts run before contract tool hydration" \
  "$CONTRACTS_BLOCK" \
  "      - name: doctor shell contracts" \
  "      - uses: actions/setup-node@v7"
assert_before \
  "Node 22 is selected before the disabled PostgreSQL MCP contract" \
  "$CONTRACTS_BLOCK" \
  "      - uses: actions/setup-node@v7" \
  "      - name: PostgreSQL MCP disabled contract"
assert_before \
  "disabled PostgreSQL MCP contract runs before dependency hydration" \
  "$CONTRACTS_BLOCK" \
  "      - name: PostgreSQL MCP disabled contract" \
  "      - name: install locked contract tools"
assert_before \
  "site-data backstop stays ahead of the doctor contracts" \
  "$CONTRACTS_BLOCK" \
  "      - name: R61 site-data backstop (check-no-site-data)" \
  "      - name: doctor shell contracts"
assert_before \
  "workflow regression runs before contract tool hydration" \
  "$CONTRACTS_BLOCK" \
  "      - name: CI workflow contract" \
  "      - name: install locked contract tools"
assert_before \
  "contract audit runs before generated-lock verification" \
  "$CONTRACTS_BLOCK" \
  "      - name: audit locked contract tools" \
  "      - name: generated contract lock"
assert_text_not_contains "Python gates cannot continue on error" "$API_BLOCK$INTEGRATION_SHARDS_BLOCK$CONTRACT_RESPONSES_BLOCK" "continue-on-error:"
assert_text_not_contains "contract gates cannot continue on error" "$CONTRACTS_BLOCK" "continue-on-error:"
assert_text_not_contains "Python and contract commands cannot suppress failures" "$API_BLOCK$INTEGRATION_SHARDS_BLOCK$CONTRACT_RESPONSES_BLOCK$CONTRACTS_BLOCK" "|| true"

assert_contains \
  "security preamble gates npm high/critical while keeping pip-audit and Trivy findings report-only" \
  "$WORKFLOW" \
  "npm high/critical findings are GATED; pip-audit and Trivy FINDINGS remain REPORT-ONLY."
assert_not_contains \
  "workflow guidance does not call the mixed security job warn-only" \
  "$WORKFLOW" \
  '`security` is warn-only'
assert_text_contains \
  "security job runs the pip-audit regression then the root-aware locked runner" \
  "$SECURITY_BLOCK" \
  '      - name: pip-audit runner regressions
        run: bash scripts/tests/test-pip-audit-runner.sh
      - name: pip-audit (Python deps, resolved from uv.lock)
        run: bash scripts/run-pip-audit.sh'
assert_before \
  "security installs uv before its runner regression" \
  "$SECURITY_BLOCK" \
  "      - name: install uv" \
  "      - name: pip-audit runner regressions"
assert_before \
  "security exercises the runner before its live audit" \
  "$SECURITY_BLOCK" \
  "      - name: pip-audit runner regressions" \
  "      - name: pip-audit (Python deps, resolved from uv.lock)"
assert_before \
  "locked Python audit completes before Node audit setup" \
  "$SECURITY_BLOCK" \
  "      - name: pip-audit (Python deps, resolved from uv.lock)" \
  "      - uses: actions/setup-node@v7"
assert_text_contains \
  "security pins Node 22 and caches the web lock" \
  "$SECURITY_BLOCK" \
  '      - uses: actions/setup-node@v7
        with:
          node-version: "26"
          cache: npm
          cache-dependency-path: apps/web/package-lock.json'
assert_text_contains \
  "security installs the frozen web tree without lifecycle scripts" \
  "$SECURITY_BLOCK" \
  '      - name: install frozen web dependencies for npm policy
        working-directory: apps/web
        run: npm ci --ignore-scripts'
assert_text_contains \
  "security runs the exact npm advisory regression matrix" \
  "$SECURITY_BLOCK" \
  '      - name: npm advisory policy regressions
        run: |
          node --test \
            scripts/tests/test-web-security-lock.mjs \
            scripts/tests/test-npm-audit-runner.mjs \
            scripts/tests/test-check-npm-audit.mjs \
            scripts/tests/test-npm-audit-policy.mjs \
            scripts/tests/test-router-rsc-policy.mjs'
assert_text_contains \
  "security runs the live web-lock policy gate" \
  "$SECURITY_BLOCK" \
  '      - name: npm advisory policy (web lock)
        run: node scripts/check-npm-audit.mjs'
assert_before \
  "security sets up Node before frozen web install" \
  "$SECURITY_BLOCK" \
  "      - uses: actions/setup-node@v7" \
  "      - name: install frozen web dependencies for npm policy"
assert_before \
  "security installs the frozen web tree before npm regressions" \
  "$SECURITY_BLOCK" \
  "      - name: install frozen web dependencies for npm policy" \
  "      - name: npm advisory policy regressions"
assert_before \
  "security runs npm regressions before the live policy gate" \
  "$SECURITY_BLOCK" \
  "      - name: npm advisory policy regressions" \
  "      - name: npm advisory policy (web lock)"
assert_before \
  "security completes the live npm gate before the first Trivy scan" \
  "$SECURITY_BLOCK" \
  "      - name: npm advisory policy (web lock)" \
  "      - name: trivy filesystem scan (vuln + secret + IaC misconfig; HIGH/CRITICAL)"
assert_text_not_contains \
  "security removes the old inline npm audit step" \
  "$SECURITY_BLOCK" \
  "      - name: npm audit (web deps, from package-lock.json)"
assert_text_not_contains \
  "security does not invoke raw npm audit" \
  "$SECURITY_BLOCK" \
  "npm audit "
assert_text_not_contains \
  "security does not write an npm audit report under RUNNER_TEMP" \
  "$SECURITY_BLOCK" \
  'RUNNER_TEMP/npm-audit.json'
assert_text_not_contains "security npm policy does not use jq" "$SECURITY_BLOCK" "jq"
assert_text_not_contains "security npm policy does not disable errexit" "$SECURITY_BLOCK" "set +e"
assert_text_not_contains "security npm policy does not suppress failures" "$SECURITY_BLOCK" "|| true"
assert_text_not_contains "security job does not run floating pip-audit" "$SECURITY_BLOCK" "uvx pip-audit"
assert_text_not_contains "security job cannot continue on operational pip-audit failures" "$SECURITY_BLOCK" "continue-on-error:"
assert_text_contains \
  "Trivy findings remain report-only" \
  "$SECURITY_BLOCK" \
  '          exit-code: "0"'
assert_contains \
  "pip-audit runner exports the frozen default graph without its security tool group" \
  "$PIP_AUDIT_RUNNER" \
  'uv export --frozen --no-group security --no-emit-project \'
assert_contains \
  "pip-audit executes only through the frozen security group" \
  "$PIP_AUDIT_RUNNER" \
  'uv run --frozen --only-group security pip-audit \'
assert_not_contains \
  "active workflow rejects floating pip-audit" \
  "$WORKFLOW" \
  "uvx pip-audit"
assert_not_contains \
  "runner keeps uv lock validation enabled" \
  "$PIP_AUDIT_RUNNER" \
  "UV_SKIP_WHEEL_FILENAME_CHECK"

assert_contains \
  "web shards retain deterministic one-worker isolation" \
  "$WEB_CONFIG" \
  "    maxWorkers: 1,"
assert_contains \
  "web shards retain fork-process isolation" \
  "$WEB_CONFIG" \
  '    pool: "forks",'
assert_not_contains \
  "web shards do not disable per-file isolation" \
  "$WEB_CONFIG" \
  "isolate: false"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
