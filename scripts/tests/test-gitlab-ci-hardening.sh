#!/usr/bin/env bash
# Structural regression for .gitlab-ci.yml, the sibling of test-ci-hardening.sh.
#
# Two kinds of assertion live here. The first kind is PORTED policy — the hard-fail discipline,
# the exact test-tree collection, and the shard topology that test-ci-hardening.sh has always
# pinned on the GitHub workflow, restated against the file that is now the gate.
#
# The second kind is new, and every one of it encodes a bug this port actually hit. Each is a
# GitLab-specific trap that produced a green-looking pipeline or a failure far from its cause:
# cwd and env persisting across script lines, root defeating permission tests, a CLI plugin
# installed where the unprivileged user cannot read it. They are pinned because nothing else in
# the repository can see them, and because each already happened once.
#
# Bash + grep only, like its sibling, so it runs before dependencies are hydrated.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PIPELINE="$ROOT/.gitlab-ci.yml"
PASS=0
FAIL=0

ok()  { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

# GitLab job keys are top-level, unlike GitHub's two-space-indented ones.
job_block() {
  awk -v heading="$1:" '
    $0 == heading { inside = 1; next }
    inside && $0 ~ /^[[:alnum:]_.-]+:/ { exit }
    inside { print }
  ' "$PIPELINE"
}

assert_text_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) ok "$label" ;;
    *) bad "$label (missing: $needle)" ;;
  esac
}

assert_text_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) bad "$label (unexpected: $needle)" ;;
    *) ok "$label" ;;
  esac
}

assert_file_contains()     { assert_text_contains     "$1" "$(cat "$PIPELINE")" "$2"; }
assert_file_not_contains() { assert_text_not_contains "$1" "$(cat "$PIPELINE")" "$2"; }

WHOLE="$(cat "$PIPELINE")"
API_BLOCK="$(job_block api)"
CONTRACTS_BLOCK="$(job_block contracts)"
CONTRACT_RESPONSES_BLOCK="$(job_block contract-responses)"
INTEGRATION_BLOCK="$(job_block integration-shards)"
MIGRATIONS_BLOCK="$(job_block migrations)"
WEB_TESTS_BLOCK="$(job_block web-tests)"
WEB_BROWSER_BLOCK="$(job_block web-browser)"
SECURITY_BLOCK="$(job_block security)"
RELEASE_BLOCK="$(job_block release-gate)"

printf '== gitlab pipeline hardening ==\n'

# ---- hard-fail discipline (ported) -------------------------------------------------------------
assert_file_not_contains "no job may be allowed to fail" "allow_failure: true"
assert_file_not_contains "no command may suppress its own failure" "|| true"
assert_file_not_contains "the GitHub-only escape hatch cannot appear here" "continue-on-error"

# ---- each suite collects only its authoritative tree (ported) -----------------------------------
assert_text_contains "api collects only the authoritative unit tree" \
  "$API_BLOCK" "pytest tests/unit -m unit"
assert_text_contains "integration shards collect only the authoritative integration tree" \
  "$INTEGRATION_BLOCK" "pytest tests/integration -m integration"
assert_text_contains "contract job collects only the response-contract module" \
  "$CONTRACT_RESPONSES_BLOCK" "pytest tests/integration/test_contract_response_schemas.py -m contract"

# ---- shard topology (ported; the GitHub matrix becomes `parallel`) -------------------------------
assert_text_contains "integration keeps exactly four shards" "$INTEGRATION_BLOCK" "parallel: 4"
assert_text_contains "web keeps exactly two shards" "$WEB_TESTS_BLOCK" "parallel: 2"
assert_text_contains "integration splits by the runner-provided index" \
  "$INTEGRATION_BLOCK" '--splits "$CI_NODE_TOTAL" --group "$CI_NODE_INDEX"'
assert_text_contains "web splits by the runner-provided index" \
  "$WEB_TESTS_BLOCK" '--shard=$CI_NODE_INDEX/$CI_NODE_TOTAL'

# ---- browser suite (ported) ----------------------------------------------------------------------
assert_text_contains "browser job runs the locked browser suite" \
  "$WEB_BROWSER_BLOCK" "npm run test:browser"
assert_text_contains "browser job installs Chromium with its Linux packages" \
  "$WEB_BROWSER_BLOCK" "playwright install --with-deps chromium"
assert_text_not_contains "browser job cannot override the locked zero-retry policy" \
  "$WEB_BROWSER_BLOCK" "--retries"
assert_text_not_contains "browser job cannot select changed files only" \
  "$WEB_BROWSER_BLOCK" "--changed"
assert_text_contains "browser diagnostics upload only on failure" \
  "$WEB_BROWSER_BLOCK" "when: on_failure"

# ---- contracts job (ported) ----------------------------------------------------------------------
assert_text_contains "contracts runs authority and Claude compatibility contracts" \
  "$CONTRACTS_BLOCK" "test-agent-authority.sh"
assert_text_contains "contracts runs the R61 site-data backstop" \
  "$CONTRACTS_BLOCK" "check-no-site-data.sh"
assert_text_contains "contracts runs the doctor shell contracts" \
  "$CONTRACTS_BLOCK" "test-doctor.sh"
assert_text_contains "contracts proves the PostgreSQL MCP path stays disabled" \
  "$CONTRACTS_BLOCK" "test-postgres-mcp-disabled.mjs"
assert_text_contains "contracts runs the GitHub workflow regression" \
  "$CONTRACTS_BLOCK" "test-ci-hardening.sh"
assert_text_contains "contracts runs THIS pipeline's regression" \
  "$CONTRACTS_BLOCK" "test-gitlab-ci-hardening.sh"
assert_text_contains "contracts installs locked tools without lifecycle scripts" \
  "$CONTRACTS_BLOCK" "npm ci --prefix packages/contracts --ignore-scripts"
assert_text_contains "contracts lints through the locked wrapper" \
  "$CONTRACTS_BLOCK" "run-contract-tool.sh redocly lint"
assert_text_contains "contracts audits the locked dependency graph" \
  "$CONTRACTS_BLOCK" "audit --package-lock-only --audit-level=high"
assert_text_contains "contracts checks the committed contract lock" \
  "$CONTRACTS_BLOCK" "gen-contracts.sh --check"
assert_file_not_contains "no job runs a floating npx contract tool" "npx redocly"

# ---- security job (ported) -----------------------------------------------------------------------
assert_text_contains "security runs the pip-audit regression then the locked runner" \
  "$SECURITY_BLOCK" "test-pip-audit-runner.sh"
assert_text_contains "security runs the root-aware locked runner" \
  "$SECURITY_BLOCK" "run-pip-audit.sh"
assert_text_contains "security runs the live web-lock policy gate" \
  "$SECURITY_BLOCK" "node scripts/check-npm-audit.mjs"
assert_text_contains "security runs the exact npm advisory regression matrix" \
  "$SECURITY_BLOCK" "test-router-rsc-policy.mjs"
assert_text_not_contains "security does not invoke raw npm audit on the web lock" \
  "$SECURITY_BLOCK" "npm audit --prefix apps/web"
assert_text_not_contains "security does not write an audit report under a runner temp" \
  "$SECURITY_BLOCK" "RUNNER_TEMP"

# ---- release gate (ported) -----------------------------------------------------------------------
assert_text_contains "release gate stays tag-only" "$RELEASE_BLOCK" 'CI_COMMIT_TAG =~ /^v/'
assert_text_contains "release gate sets the digest-pin opt-in" "$RELEASE_BLOCK" 'EASYSYNQ_RELEASE: "1"'

# ---- GitLab traps this port actually hit ---------------------------------------------------------
# Each of the next assertions failed for real during the port. They are pinned because the symptom
# never named the cause, and because nothing else in the repository looks at this file.

# cwd persists across script lines: `cd ..` from apps/api lands in apps/, not the repo root.
assert_file_not_contains "no script line may use a relative cd (cwd persists between lines)" "- cd .."
assert_file_contains "the api job returns to the repo root absolutely" \
  'cd "$CI_PROJECT_DIR" && uv run --project apps/api ruff check'

# env persists too: the compose block's exports reached pytest and broke test_smtp_defaults_are_safe.
assert_text_contains "compose overlay validation is subshelled so its exports cannot reach pytest" \
  "$API_BLOCK" "      ("

# root defeats every permission-denial test; the suites must drop privileges.
assert_text_contains "the api suite runs unprivileged" "$API_BLOCK" "runuser -u ci"
assert_text_contains "the security advisory matrix runs unprivileged" "$SECURITY_BLOCK" "runuser -u ci"
assert_file_not_contains "the CI user gets no personal home path (R61)" "HOME=/home/"

# a per-user plugin install is invisible to that unprivileged user.
assert_file_contains "the compose plugin installs system-wide, not into a user home" \
  "/usr/local/lib/docker/cli-plugins"
assert_file_not_contains "the compose plugin is not installed into root's home" \
  "/root/.docker/cli-plugins"

# testcontainers cannot reach a mapped port on localhost under DinD.
assert_file_contains "testcontainers is pointed at the DinD service alias" \
  "TESTCONTAINERS_HOST_OVERRIDE: docker"
assert_file_contains "the testcontainers reaper is disabled under DinD" \
  'TESTCONTAINERS_RYUK_DISABLED: "true"'

# a GitLab service is reached by alias; localhost silently fails to connect.
assert_text_contains "migrations reaches its service by alias, not localhost" \
  "$MIGRATIONS_BLOCK" "@postgres:5432/easysynq"
assert_text_not_contains "migrations must not address its service as localhost" \
  "$MIGRATIONS_BLOCK" "@localhost:5432"

# the pg client major must win PATH or 19 backup/restore tests fail naming a version, not a cause.
assert_text_contains "integration installs the matching postgres client major" \
  "$INTEGRATION_BLOCK" "postgresql-client-18"
assert_text_contains "integration asserts that client actually wins PATH" \
  "$INTEGRATION_BLOCK" "pg_dump --version | grep -qE"

# the runner's own report parser needs jq; without it 4 of 129 fixtures fail as policy bugs.
assert_text_contains "security installs the jq its report parser needs" "$SECURITY_BLOCK" "jq >/dev/null"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
