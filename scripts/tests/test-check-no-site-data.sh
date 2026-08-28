#!/usr/bin/env bash
# Behavioral regressions for scripts/check-no-site-data.sh. Each case runs the real gate in a
# minimal Git repository so remote lookup, tracked-file enumeration, and exit behavior stay real.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/check-no-site-data.sh"
PASS=0
FAIL=0
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/easysynq-r61-test.XXXXXX")"

cleanup() {
  local expected_parent="${TMPDIR:-/tmp}"
  case "$TEST_ROOT" in
    "$expected_parent"/easysynq-r61-test.*)
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
  if [ "$got" = "$want" ]; then
    ok "$label"
  else
    bad "$label (want exit $want, got $got)"
  fi
}

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in *"$needle"*) ok "$label" ;; *) bad "$label (missing: $needle)" ;; esac
}

assert_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in *"$needle"*) bad "$label (unexpected: $needle)" ;; *) ok "$label" ;; esac
}

new_repo() {
  local path="$1"
  mkdir -p "$path/scripts"
  cp "$SCRIPT" "$path/scripts/check-no-site-data.sh"
  printf 'portable fixture\n' >"$path/notes.txt"
  git -C "$path" init -q
  git -C "$path" add scripts/check-no-site-data.sh notes.txt
}

run_gate() {
  local repo="$1" execution_path="$2"
  RUN_CODE=0
  RUN_OUTPUT="$(cd "$repo" && env PATH="$execution_path" ./scripts/check-no-site-data.sh 2>&1)" \
    || RUN_CODE=$?
}

printf '== check-no-site-data.sh ==\n'

# The repository-owner scan is optional. A local worktree without an origin must still execute every
# other R61 rule and report clean for benign tracked content.
repo="$TEST_ROOT/no-origin"
new_repo "$repo"
run_gate "$repo" "$PATH"
assert_exit "missing origin is non-fatal" 0 "$RUN_CODE"
assert_contains "missing origin still reaches the clean verdict" "$RUN_OUTPUT" "check-no-site-data: clean"

# Cross-platform tools commonly serialize Windows profiles with forward slashes. Construct the
# sensitive shape at runtime so this regression test is not itself rejected by the repository gate.
repo="$TEST_ROOT/forward-slash-profile"
new_repo "$repo"
git -C "$repo" remote add origin https://example.com/alternate.git
printf 'workspace=C:/%s/%s/project\n' "Users" "ExampleUser" >"$repo/profile.txt"
printf 'workspace=/%s/%s/%s/project\n' "c" "Users" "ExampleUser" >"$repo/profile-msys.txt"
git -C "$repo" add profile.txt profile-msys.txt
run_gate "$repo" "$PATH"
assert_exit "forward-slash Windows profile is rejected" 1 "$RUN_CODE"
assert_contains "forward-slash rejection identifies the profile rule" "$RUN_OUTPUT" "Personal Windows profile path"
assert_contains "MSYS profile form is included in the rejection" "$RUN_OUTPUT" "profile-msys.txt"

# The R61 gate runs before language-toolchain setup in CI and from supported Git-Bash checkouts.
# Constrain PATH to the shell tools the scanner already uses: owner-token scanning must not require
# either a global Python executable or a hydrated project environment.
repo="$TEST_ROOT/no-python3"
new_repo "$repo"
git -C "$repo" remote add origin https://github.com/ExampleOwner/EasySynQ.git
fake_bin="$TEST_ROOT/fake-bin"
mkdir -p "$fake_bin"
for tool in bash dirname git grep sed head cat; do
  ln -s "$(type -P "$tool")" "$fake_bin/$tool"
done
run_gate "$repo" "$fake_bin"
assert_exit "repository-owner scan needs no Python command" 0 "$RUN_CODE"
assert_contains "interpreter-free scan reaches the clean verdict" "$RUN_OUTPUT" "check-no-site-data: clean"

# The public repository Owner field is legitimate metadata and remains exempt.
printf 'Owner:** %s\n' "ExampleOwner" >"$repo/metadata.txt"
git -C "$repo" add metadata.txt
run_gate "$repo" "$fake_bin"
assert_exit "standalone repository-owner metadata stays allowed" 0 "$RUN_CODE"

# Absence of an interpreter must not turn the owner scan into a no-op. A real owner-token use is
# still rejected, including Owner-like suffixes that are not standalone metadata fields. The
# diagnostic must continue to redact the personal identifier.
printf 'shellOwner: %s\nnotOwner:**%s\n' "ExampleOwner" "ExampleOwner" >"$repo/identity.txt"
git -C "$repo" add identity.txt
run_gate "$repo" "$fake_bin"
assert_exit "interpreter-free owner scan still rejects an identity use" 1 "$RUN_CODE"
assert_contains "owner-token diagnostic stays redacted" "$RUN_OUTPUT" "<redacted-personal-identifier>"
assert_contains "Owner-like prefix on line 1 is not exempt" "$RUN_OUTPUT" "identity.txt:1"
assert_contains "Owner-like prefix on line 2 is not exempt" "$RUN_OUTPUT" "identity.txt:2"
assert_not_contains "owner-token diagnostic does not echo the identity" "$RUN_OUTPUT" "ExampleOwner"

# The container-topology widening must stay a single literal. `172.16.0.0` is sanctioned as the
# base of Docker's bridge pool (the shipped TRUSTED_PROXY_CIDRS default); every other address in
# that space must still red, or the exemption becomes a hiding place for a real one. Both shapes
# are assembled at runtime so this regression test is not itself rejected by the gate.
repo="$TEST_ROOT/docker-pool-base"
new_repo "$repo"
printf 'TRUSTED_PROXY_CIDRS=%s.%s.0.0/12\n' "172" "16" >"$repo/settings.txt"
git -C "$repo" add settings.txt
run_gate "$repo" "$PATH"
assert_exit "the Docker bridge-pool base is sanctioned" 0 "$RUN_CODE"

printf 'edge=%s.%s.0.5\n' "172" "16" >"$repo/edge.txt"
git -C "$repo" add edge.txt
run_gate "$repo" "$PATH"
assert_exit "a neighbouring address in the same space still reds" 1 "$RUN_CODE"
assert_contains "the neighbouring address is reported" "$RUN_OUTPUT" "edge.txt:1"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
