#!/usr/bin/env bash
# Behavioral regression for scripts/run-pip-audit.sh. The production runner executes against a
# guarded fake uv so its public status, output, arguments, CWD, and temporary-directory lifecycle
# are observable without installing or auditing anything from the network.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
RUNNER="$ROOT/scripts/run-pip-audit.sh"
PROVENANCE_FIXTURES="$ROOT/scripts/tests/fixtures/uv-lock-provenance"
REAL_UV="$(command -v uv 2>/dev/null || true)"
PASS=0
FAIL=0
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/easysynq-pip-audit-test.XXXXXX")"

cleanup() {
  local expected_parent
  expected_parent="$(cd "${TMPDIR:-/tmp}" && pwd -P)" || return
  case "$TEST_ROOT" in
    "$expected_parent"/easysynq-pip-audit-test.*)
      if [ -d "$TEST_ROOT" ] && [ ! -L "$TEST_ROOT" ]; then
        rm -rf -- "$TEST_ROOT"
      fi
      ;;
  esac
}
trap cleanup EXIT

ok()  { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

assert_zero() {
  local label="$1" got="$2"
  if [ "$got" -eq 0 ]; then ok "$label"; else bad "$label (got exit $got)"; fi
}

assert_nonzero() {
  local label="$1" got="$2"
  if [ "$got" -ne 0 ]; then ok "$label"; else bad "$label (unexpected exit 0)"; fi
}

assert_equals() {
  local label="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then ok "$label"; else bad "$label (unexpected output)"; fi
}

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in *"$needle"*) ok "$label" ;; *) bad "$label (missing expected text)" ;; esac
}

assert_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in *"$needle"*) bad "$label (leaked fixture data)" ;; *) ok "$label" ;; esac
}

assert_file_lines() {
  local label="$1" file="$2"
  shift 2
  local expected actual
  expected="$(printf '%s\n' "$@")"
  actual="$(cat "$file" 2>/dev/null || true)"
  if [ "$actual" = "$expected" ]; then ok "$label"; else bad "$label"; fi
}

has_exact_requirement_pin() {
  local expected="$1" file="$2"
  awk -v expected="$expected" '$1 == expected { found = 1 } END { exit(found ? 0 : 1) }' "$file"
}

mkdir -p "$TEST_ROOT/fake-bin" "$TEST_ROOT/caller"
cat >"$TEST_ROOT/fake-bin/uv" <<'FAKE_UV'
#!/usr/bin/env bash
set -u

fixture_dir="${PIP_AUDIT_FIXTURE_DIR:?}"
scenario="${PIP_AUDIT_FIXTURE_SCENARIO:?}"
requirements_sentinel="${PIP_AUDIT_FIXTURE_REQUIREMENTS_SENTINEL:?}"
environment_sentinel="${PIP_AUDIT_FIXTURE_ENV_SECRET:?}"
command_name="${1-}"

case "$command_name" in
  export)
    pwd -P >"$fixture_dir/export.cwd"
    printf '%s\n' "$@" >"$fixture_dir/export.argv"
    if [ "$scenario" = "export_failure" ]; then
      exit 42
    fi
    output=""
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "-o" ] && [ "$#" -ge 2 ]; then output="$2"; break; fi
      shift
    done
    [ -n "$output" ] || exit 96
    printf '%s\n' "$output" >"$fixture_dir/export.output"
    printf '%s\n' 'fixture-package==1.2.3' "$requirements_sentinel" >"$output"
    printf '%s\n' "$requirements_sentinel" "$environment_sentinel"
    ;;
  run)
    pwd -P >"$fixture_dir/audit.cwd"
    printf '%s\n' "$@" >"$fixture_dir/audit.argv"
    input=""
    output=""
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "-r" ] && [ "$#" -ge 2 ]; then input="$2"; shift 2; continue; fi
      if [ "$1" = "-o" ] && [ "$#" -ge 2 ]; then output="$2"; break; fi
      shift
    done
    expected_input="$(cat "$fixture_dir/export.output" 2>/dev/null || true)"
    [ -n "$input" ] && [ "$input" = "$expected_input" ] || exit 93
    [ -f "$input" ] || exit 92
    grep -Fxq -- "$requirements_sentinel" "$input" || exit 91
    [ -n "$output" ] || exit 95
    printf '%s\n' 'DO_NOT_PRINT_SOURCE'
    case "$scenario" in
      clean|status1_clean|status2)
        printf '%s\n' '{"dependencies":[{"name":"demo-package","version":"1.2.3","vulns":[]}],"fixture_secret":"DO_NOT_PRINT_SOURCE"}' >"$output"
        ;;
      findings|status0_findings)
        printf '%s\n' '{"dependencies":[{"name":"demo-package","version":"1.2.3","vulns":[{"id":"CVE-2026-0001"},{"id":"GHSA-DEMO-0002"}]}],"fixture_secret":"DO_NOT_PRINT_SOURCE"}' >"$output"
        ;;
      malformed)
        printf '%s\n' '{not-json' >"$output"
        ;;
      missing_report)
        ;;
      dependencies_not_array)
        printf '%s\n' '{"dependencies":{},"fixture_secret":"DO_NOT_PRINT_SOURCE"}' >"$output"
        ;;
      vulnerability_without_string_id)
        printf '%s\n' '{"dependencies":[{"name":"demo-package","version":"1.2.3","vulns":[{"id":7}]}],"fixture_secret":"DO_NOT_PRINT_SOURCE"}' >"$output"
        ;;
      *) exit 94 ;;
    esac
    case "$scenario" in
      clean|status0_findings) exit 0 ;;
      findings|status1_clean|malformed|missing_report|dependencies_not_array|vulnerability_without_string_id) exit 1 ;;
      status2) exit 2 ;;
    esac
    ;;
  *)
    printf '%s\n' "$@" >"$fixture_dir/unexpected.argv"
    exit 97
    ;;
esac
FAKE_UV
chmod +x "$TEST_ROOT/fake-bin/uv"

RUN_CODE=0
RUN_OUTPUT=""
RUN_CASE_DIR=""

run_case() {
  local scenario="$1"
  local requirements_sentinel="DO_NOT_PRINT_REQUIREMENTS_${scenario}"
  RUN_CASE_DIR="$TEST_ROOT/case-$scenario"
  local runner_temp="$RUN_CASE_DIR/runner-temp"
  mkdir -p "$RUN_CASE_DIR" "$runner_temp"
  printf '%s\n' preserve >"$runner_temp/caller-owned"
  RUN_CODE=0
  RUN_OUTPUT="$(
    cd "$TEST_ROOT/caller" &&
      env \
        PATH="$TEST_ROOT/fake-bin:/usr/bin:/bin" \
        RUNNER_TEMP="$runner_temp" \
        PIP_AUDIT_FIXTURE_DIR="$RUN_CASE_DIR" \
        PIP_AUDIT_FIXTURE_SCENARIO="$scenario" \
        PIP_AUDIT_FIXTURE_REQUIREMENTS_SENTINEL="$requirements_sentinel" \
        PIP_AUDIT_FIXTURE_ENV_SECRET="DO_NOT_PRINT_ENVIRONMENT" \
        bash "$RUNNER" 2>&1
  )" || RUN_CODE=$?
}

assert_common_runner_contract() {
  local scenario="$1"
  local expected_cwd="$ROOT/apps/api"
  local export_output audit_input audit_output audit_tmp runner_temp
  assert_file_lines "$scenario export argv is exact" "$RUN_CASE_DIR/export.argv" \
    export --frozen --no-group security --no-emit-project --format requirements-txt -o \
    "$(sed -n '9p' "$RUN_CASE_DIR/export.argv" 2>/dev/null || true)"
  export_output="$(sed -n '9p' "$RUN_CASE_DIR/export.argv" 2>/dev/null || true)"
  if [ "$scenario" = "export_failure" ]; then
    if [ ! -e "$RUN_CASE_DIR/audit.argv" ]; then ok "export failure does not run pip-audit"; else bad "export failure does not run pip-audit"; fi
  else
    audit_input="$(sed -n '7p' "$RUN_CASE_DIR/audit.argv" 2>/dev/null || true)"
    audit_output="$(sed -n '11p' "$RUN_CASE_DIR/audit.argv" 2>/dev/null || true)"
    assert_file_lines "$scenario audit argv is exact" "$RUN_CASE_DIR/audit.argv" \
      run --frozen --only-group security pip-audit -r "$export_output" --format json -o "$audit_output"
    if [ "$audit_input" = "$export_output" ]; then ok "$scenario audits the exported requirements"; else bad "$scenario audits the exported requirements"; fi
  fi
  audit_tmp="$(dirname "$export_output")"
  runner_temp="$RUN_CASE_DIR/runner-temp"
  case "$audit_tmp" in
    "$runner_temp"/easysynq-pip-audit.*) ok "$scenario temp directory is runner-owned" ;;
    *) bad "$scenario temp directory is runner-owned" ;;
  esac
  if [ ! -e "$audit_tmp" ]; then ok "$scenario runner-owned temp directory is removed"; else bad "$scenario runner-owned temp directory is removed"; fi
  if [ "$(cat "$RUN_CASE_DIR/export.cwd" 2>/dev/null || true)" = "$expected_cwd" ]; then ok "$scenario export CWD is the API project"; else bad "$scenario export CWD is the API project"; fi
  if [ "$scenario" = "export_failure" ] || [ "$(cat "$RUN_CASE_DIR/audit.cwd" 2>/dev/null || true)" = "$expected_cwd" ]; then ok "$scenario audit CWD is the API project"; else bad "$scenario audit CWD is the API project"; fi
  if [ -f "$RUN_CASE_DIR/runner-temp/caller-owned" ]; then ok "$scenario preserves caller-owned temp data"; else bad "$scenario preserves caller-owned temp data"; fi
  assert_not_contains "$scenario does not print requirements" "$RUN_OUTPUT" "DO_NOT_PRINT_REQUIREMENTS_${scenario}"
  assert_not_contains "$scenario does not print environment" "$RUN_OUTPUT" "DO_NOT_PRINT_ENVIRONMENT"
  assert_not_contains "$scenario does not print unselected report data" "$RUN_OUTPUT" "DO_NOT_PRINT_SOURCE"
}

printf '== pip-audit runner ==\n'

run_case clean
assert_zero "status 0 plus zero vulnerabilities passes" "$RUN_CODE"
assert_equals "clean report prints no package lines" "$RUN_OUTPUT" ""
assert_common_runner_contract clean

run_case findings
assert_zero "status 1 plus vulnerability findings remains report-only" "$RUN_CODE"
assert_equals "findings print only the selected package fields" "$RUN_OUTPUT" "demo-package 1.2.3: CVE-2026-0001, GHSA-DEMO-0002"
assert_common_runner_contract findings

for scenario in status0_findings status1_clean malformed missing_report dependencies_not_array vulnerability_without_string_id status2; do
  run_case "$scenario"
  assert_nonzero "$scenario fails closed" "$RUN_CODE"
  assert_common_runner_contract "$scenario"
done

run_case export_failure
assert_nonzero "export failure is operationally fatal" "$RUN_CODE"
assert_common_runner_contract export_failure

printf '\n== frozen lock provenance ==\n'
if [ -z "$REAL_UV" ]; then
  bad "real uv is available for copied-lock provenance"
else
  provenance_uv="$TEST_ROOT/provenance-uv"
  cat >"$provenance_uv" <<'PROVENANCE_UV'
#!/usr/bin/env bash
if [ "${1-}" = "--version" ]; then
  printf '%s\n' 'uv 9.99.0 (fixture)'
  exit 0
fi
exec "${PIP_AUDIT_INSTALLED_UV:?}" "$@"
PROVENANCE_UV
  chmod +x "$provenance_uv"
  export PIP_AUDIT_INSTALLED_UV="$REAL_UV"
  assert_equals \
    "provenance shim reports a different well-formed uv version" \
    "$($provenance_uv --version 2>/dev/null || true)" \
    "uv 9.99.0 (fixture)"

  false_positive_output="$TEST_ROOT/provenance-false-positive.txt"
  printf '%s\n' 'pypdf==6.13.20 \' >"$false_positive_output"
  if ! has_exact_requirement_pin "pypdf==6.13.2" "$false_positive_output"; then
    ok "pypdf 6.13.20 cannot satisfy the exact 6.13.2 provenance oracle"
  else
    bad "pypdf 6.13.20 cannot satisfy the exact 6.13.2 provenance oracle"
  fi
  for selected_version in 6.13.2 6.14.2; do
    project="$TEST_ROOT/provenance-$selected_version"
    output="$TEST_ROOT/provenance-$selected_version.txt"
    mkdir -p "$project"
    cp "$PROVENANCE_FIXTURES/pyproject.toml" "$project/pyproject.toml"
    cp "$PROVENANCE_FIXTURES/uv.lock.pypdf-$selected_version" "$project/uv.lock"
    provenance_status=0
    (
      cd "$project" &&
        env UV_OFFLINE=1 UV_NO_CACHE=1 "$provenance_uv" export \
          --frozen --no-group security --no-emit-project \
          --format requirements-txt -o "$output"
    ) >/dev/null 2>&1 || provenance_status=$?
    assert_zero "artifact-consistent pypdf $selected_version lock exports offline" "$provenance_status"
    if has_exact_requirement_pin "pypdf==$selected_version" "$output" 2>/dev/null; then
      ok "exported requirement follows pypdf $selected_version fixture lock"
    else
      bad "exported requirement follows pypdf $selected_version fixture lock"
    fi
  done

  inconsistent_project="$TEST_ROOT/provenance-inconsistent"
  mkdir -p "$inconsistent_project"
  cp "$PROVENANCE_FIXTURES/pyproject.toml" "$inconsistent_project/pyproject.toml"
  sed 's/pypdf-6\.13\.2-py3-none-any\.whl/pypdf-6.14.2-py3-none-any.whl/' \
    "$PROVENANCE_FIXTURES/uv.lock.pypdf-6.13.2" >"$inconsistent_project/uv.lock"
  provenance_status=0
  provenance_output="$(
    cd "$inconsistent_project" &&
      env UV_OFFLINE=1 UV_NO_CACHE=1 "$provenance_uv" export \
        --frozen --no-group security --no-emit-project \
        --format requirements-txt -o "$TEST_ROOT/provenance-inconsistent.txt" 2>&1
  )" || provenance_status=$?
  assert_nonzero "artifact-inconsistent selected record is rejected" "$provenance_status"
  assert_contains "artifact inconsistency is the rejection reason" "$provenance_output" "inconsistent version"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
