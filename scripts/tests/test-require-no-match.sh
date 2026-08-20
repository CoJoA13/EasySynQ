#!/usr/bin/env bash
# Behavioral proof for the fail-safe source-absence gate.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HELPER="$ROOT/scripts/require-no-match.sh"
PASS=0
FAIL=0
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/easysynq-no-match-test.XXXXXX")"

cleanup() {
  local expected_parent="${TMPDIR:-/tmp}"
  case "$TEST_ROOT" in
    "$expected_parent"/easysynq-no-match-test.*)
      if [ -d "$TEST_ROOT" ] && [ ! -L "$TEST_ROOT" ]; then
        rm -rf -- "$TEST_ROOT"
      fi
      ;;
  esac
}
trap cleanup EXIT

ok()  { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

run_helper() {
  RUN_CODE=0
  RUN_OUTPUT="$(bash "$HELPER" "$@" 2>&1)" || RUN_CODE=$?
}

assert_exit() {
  local label="$1" want="$2"
  if [ "$RUN_CODE" -eq "$want" ]; then
    ok "$label"
  else
    bad "$label (want exit $want, got $RUN_CODE: $RUN_OUTPUT)"
  fi
}

printf '== require-no-match.sh ==\n'
printf 'safe fixture line\nforbidden fixture line\n' >"$TEST_ROOT/input.txt"

run_helper 'anything'
assert_exit "zero paths is a usage error" 2

run_helper 'absent fixture text' "$TEST_ROOT/input.txt"
assert_exit "absent pattern succeeds" 0

run_helper 'forbidden fixture' "$TEST_ROOT/input.txt"
assert_exit "present pattern fails" 1
case "$RUN_OUTPUT" in
  *"2:forbidden fixture line"*) ok "present match prints the safe fixture line" ;;
  *) bad "present match output omitted the safe fixture line: $RUN_OUTPUT" ;;
esac

run_helper '[' "$TEST_ROOT/input.txt"
assert_exit "invalid regex preserves ripgrep status" 2

run_helper 'anything' "$TEST_ROOT/missing.txt"
if [ "$RUN_CODE" -ne 0 ]; then
  ok "missing path fails closed"
else
  bad "missing path was misclassified as no match"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
