#!/usr/bin/env bash
# Behavioral regression for scripts/run-contract-tool.sh. Every executable used by the test lives
# under a guarded temporary fixture, so PATH lookup, argument forwarding, telemetry, and CWD are
# observed through the real wrapper boundary.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
SCRIPT="$ROOT/scripts/run-contract-tool.sh"
PASS=0
FAIL=0
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/easysynq-contract-tool.XXXXXX")"

cleanup() {
  local expected_parent="${TMPDIR:-/tmp}"
  case "$TEST_ROOT" in
    "$expected_parent"/easysynq-contract-tool.*)
      if [ -d "$TEST_ROOT" ] && [ ! -L "$TEST_ROOT" ]; then
        rm -rf -- "$TEST_ROOT"
      fi
      ;;
  esac
}
trap cleanup EXIT

ok()  { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

assert_exit() {
  local label="$1" want="$2" got="$3"
  if [ "$got" = "$want" ]; then ok "$label"; else bad "$label (want exit $want, got $got)"; fi
}

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in *"$needle"*) ok "$label" ;; *) bad "$label (missing: $needle)" ;; esac
}

run_tool() {
  RUN_CODE=0
  RUN_OUTPUT="$(cd "$1" && shift && env PATH="$TEST_ROOT/path-bin:$PATH" "$@" 2>&1)" || RUN_CODE=$?
}

printf '== run-contract-tool.sh ==\n'

fixture="$TEST_ROOT/fixture"
mkdir -p "$fixture/scripts" "$fixture/packages/contracts/node_modules/.bin" "$TEST_ROOT/path-bin"
if [ -f "$SCRIPT" ]; then cp "$SCRIPT" "$fixture/scripts/run-contract-tool.sh"; fi
chmod +x "$fixture/scripts/run-contract-tool.sh" 2>/dev/null || true

run_tool "$fixture" "$fixture/scripts/run-contract-tool.sh"
assert_exit "no argument is rejected" 64 "$RUN_CODE"

run_tool "$fixture" "$fixture/scripts/run-contract-tool.sh" unknown
assert_exit "unknown tool is rejected" 64 "$RUN_CODE"

run_tool "$fixture" "$fixture/scripts/run-contract-tool.sh" redocly
assert_exit "missing local binary returns 127" 127 "$RUN_CODE"
case "$RUN_OUTPUT" in *"Run: npm ci --prefix packages/contracts --ignore-scripts"*) ok "missing binary prints exact setup command" ;; *) bad "missing binary prints exact setup command" ;; esac

path_marker="$TEST_ROOT/path-called"
cat >"$TEST_ROOT/path-bin/redocly" <<EOF
#!/usr/bin/env bash
touch "$path_marker"
exit 99
EOF
chmod +x "$TEST_ROOT/path-bin/redocly"
run_tool "$fixture" "$fixture/scripts/run-contract-tool.sh" redocly
assert_exit "same-named PATH executable is ignored" 127 "$RUN_CODE"
if [ -e "$path_marker" ]; then bad "same-named PATH executable is never called"; else ok "same-named PATH executable is never called"; fi
rm -f -- "$TEST_ROOT/path-bin/redocly"

args_log="$TEST_ROOT/redocly-args"
env_log="$TEST_ROOT/redocly-env"
pwd_log="$TEST_ROOT/redocly-pwd"
cat >"$fixture/packages/contracts/node_modules/.bin/redocly" <<EOF
#!/usr/bin/env bash
printf '%s\\n' "\$@" >"$args_log"
printf 'REDOCLY_TELEMETRY=%s\\nREDOCLY_SUPPRESS_UPDATE_NOTICE=%s\\n' "\${REDOCLY_TELEMETRY-}" "\${REDOCLY_SUPPRESS_UPDATE_NOTICE-}" >"$env_log"
pwd >"$pwd_log"
exit 23
EOF
chmod +x "$fixture/packages/contracts/node_modules/.bin/redocly"
unrelated="$TEST_ROOT/unrelated-repo"
mkdir -p "$unrelated"
git -C "$unrelated" init -q
run_tool "$unrelated" bash "$fixture/scripts/run-contract-tool.sh" redocly "argument with spaces" second
assert_exit "redocly status is preserved" 23 "$RUN_CODE"
case "$(sed -n '1p' "$args_log" 2>/dev/null || true)" in *"argument with spaces"*) ok "argument containing spaces remains one argument" ;; *) bad "argument containing spaces remains one argument" ;; esac
case "$(cat "$env_log" 2>/dev/null || true)" in *"REDOCLY_TELEMETRY=off"*) ok "redocly telemetry is disabled" ;; *) bad "redocly telemetry is disabled" ;; esac
case "$(cat "$env_log" 2>/dev/null || true)" in *"REDOCLY_SUPPRESS_UPDATE_NOTICE=true"*) ok "redocly update notice is suppressed" ;; *) bad "redocly update notice is suppressed" ;; esac
case "$(cat "$pwd_log" 2>/dev/null || true)" in *"$fixture"*) ok "caller repository does not control CWD" ;; *) bad "caller repository does not control CWD" ;; esac

openapi_log="$TEST_ROOT/openapi-args"
cat >"$fixture/packages/contracts/node_modules/.bin/openapi-typescript" <<EOF
#!/usr/bin/env bash
printf '%s\\n' "\$@" >"$openapi_log"
exit 17
EOF
chmod +x "$fixture/packages/contracts/node_modules/.bin/openapi-typescript"
run_tool "$fixture" "$fixture/scripts/run-contract-tool.sh" openapi-typescript schema.yaml
assert_exit "openapi-typescript status is preserved" 17 "$RUN_CODE"
case "$(cat "$openapi_log" 2>/dev/null || true)" in *"schema.yaml"*) ok "openapi-typescript selects exact local binary" ;; *) bad "openapi-typescript selects exact local binary" ;; esac

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
