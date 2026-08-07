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
WEB_GATE_BLOCK="$(job_block web)"
CONTRACTS_BLOCK="$(job_block contracts)"
CONTRACT_RESPONSES_BLOCK="$(job_block contract-responses)"
API_BLOCK="$(job_block api)"
INTEGRATION_SHARDS_BLOCK="$(job_block integration-shards)"

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
  "stable named web gate runs after every shard result" \
  "$WEB_GATE_BLOCK" \
  '  web:
    name: web
    needs: web-shards
    if: ${{ always() }}
    runs-on: ubuntu-latest'
assert_text_not_contains "stable web gate cannot continue on error" "$WEB_GATE_BLOCK" "continue-on-error:"
assert_text_contains \
  "stable web gate rejects every non-success result with a failing exit" \
  "$WEB_GATE_BLOCK" \
  '          result='"'"'${{ needs.web-shards.result }}'"'"'
          if [ "$result" != "success" ]; then
            echo "web shards did not all pass (result=$result)"
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
        run: bash scripts/tests/test-ci-hardening.sh'
assert_text_not_contains \
  "contracts job does not run floating npx contract tools" \
  "$CONTRACTS_BLOCK" \
  "npx"
assert_text_contains \
  "contracts job pins Node and caches the contract lock" \
  "$CONTRACTS_BLOCK" \
  '      - uses: actions/setup-node@v7
        with:
          node-version: "22"
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
  "R61 regression runs before contract tool hydration" \
  "$CONTRACTS_BLOCK" \
  "      - name: R61 backstop regression harness" \
  "      - uses: actions/setup-node@v7"
assert_before \
  "workflow regression runs before contract tool hydration" \
  "$CONTRACTS_BLOCK" \
  "      - name: CI workflow contract" \
  "      - uses: actions/setup-node@v7"
assert_before \
  "contract audit runs before generated-lock verification" \
  "$CONTRACTS_BLOCK" \
  "      - name: audit locked contract tools" \
  "      - name: generated contract lock"
assert_text_not_contains "Python gates cannot continue on error" "$API_BLOCK$INTEGRATION_SHARDS_BLOCK$CONTRACT_RESPONSES_BLOCK" "continue-on-error:"
assert_text_not_contains "contract gates cannot continue on error" "$CONTRACTS_BLOCK" "continue-on-error:"
assert_text_not_contains "Python and contract commands cannot suppress failures" "$API_BLOCK$INTEGRATION_SHARDS_BLOCK$CONTRACT_RESPONSES_BLOCK$CONTRACTS_BLOCK" "|| true"

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
