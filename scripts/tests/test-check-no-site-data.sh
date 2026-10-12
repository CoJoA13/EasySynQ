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
  shift 2
  RUN_CODE=0
  RUN_OUTPUT="$(cd "$repo" && env PATH="$execution_path" ./scripts/check-no-site-data.sh "$@" 2>&1)" \
    || RUN_CODE=$?
}

# Optional progress probes exercise the public gate one fixture at a time. The caller supplies
# the safe deadline; the scanner itself needs no timeout command or additional runtime tool.
# Default invocation continues to run the existing behavioral cohort below without alterations.
run_progress_case() {
  local name="$1" progress_repo="$TEST_ROOT/progress-$1"
  local padding='' payload='' repeated='' unicode='' fragment='' iteration
  local first second numeric mapped malformed_mapped long_step slice_prefix want=1
  slice_prefix="$(printf '%s%s' ':' ':')"
  first="$(printf '%s%s' '::' 'abcd')"
  second="$(printf '%s%s' '::' 'def0')"
  numeric="$(printf '%s%s' '::' '2')"
  mapped="$(printf '%s%s:%s.%s.%s.%s' '::' 'ffff' '192' '0' '2' '1')"
  malformed_mapped="${mapped}.1"
  printf -v padding '%65536s' ''

  case "$name" in
    small-control)
      printf -v padding '%1024s' ''
      payload="$(printf '%s%s' "$padding" "values[${slice_prefix}f()]; endpoint='$first'.")"
      ;;
    long-no-bracket)
      payload="${padding}endpoint='$first'"
      ;;
    long-sparse)
      payload="endpoint='$first. '; values[${slice_prefix}f()]${padding}[$numeric]; endpoint='$second. '"
      ;;
    unicode-sparse)
      unicode='λ'
      for ((iteration=0; iteration<15; iteration++)); do unicode+="$unicode"; done
      payload="endpoint='$first. '; values[${slice_prefix}f()]; text='${unicode}'; values[${slice_prefix}2 + 1]; endpoint='$second. '"
      ;;
    all-slices)
      repeated="values[0][${slice_prefix}2][${slice_prefix}f()] [${slice_prefix}(2 + 1)] + f()[${slice_prefix}2]; "
      # Four recognized spans per group, doubled to 512 groups: exactly 2,048 slice spans.
      for ((iteration=0; iteration<9; iteration++)); do repeated+="$repeated"; done
      payload="$repeated"
      want=0
      ;;
    long-step)
      long_step="${padding// /x}"
      payload="values[$first + $long_step + f((2 + 1))]; endpoint='$second'"
      ;;
    malformed-dense)
      fragment="values[$numeric + (((1)))]; "
      repeated="$fragment"
      for ((iteration=0; iteration<9; iteration++)); do repeated+="$repeated"; done
      repeated="${repeated#"$fragment"}"
      repeated="${repeated%"$fragment"}"
      payload="$(printf '%s\n%s' "values[$first + (((1)))]; ${repeated}values[$second + (1]" \
        "outer[bad + inner[${slice_prefix}f()]];")"
      assert_exit 'progress malformed-dense retains 510 identical middle fragments' \
        "$((${#fragment} * 510))" "${#repeated}"
      assert_exit 'progress malformed-dense retains 512 malformed fragments' \
        512 "$(printf '%s' "$payload" | grep -Fo -- 'values[' | wc -l | tr -d '[:blank:]')"
      assert_exit 'progress malformed-dense retains its fresh source line' \
        2 "$(printf '%s\n' "$payload" | wc -l | tr -d '[:blank:]')"
      ;;
    literal-dense)
      fragment='note = "escaped \"quote\" and \\slash [key] # = ;"; '
      fragment+="values[$numeric]; "
      repeated="$fragment"
      for ((iteration=0; iteration<11; iteration++)); do repeated+="$repeated"; done
      payload="note = \"server[$first]\"; ${repeated}note = \"server[$second]"
      assert_exit 'progress literal-dense retains 2048 complete literal/slice groups' \
        "$((${#fragment} * 2048))" "${#repeated}"
      assert_exit 'progress literal-dense retains 2048 complete escaped literals' \
        2048 "$(printf '%s' "$repeated" | grep -Fo -- 'note = "escaped' | wc -l | tr -d '[:blank:]')"
      assert_exit 'progress literal-dense retains 2048 numeric slices' \
        2048 "$(printf '%s' "$repeated" | grep -Fo -- "[$numeric]" | wc -l | tr -d '[:blank:]')"
      ;;
    malformed-long-step)
      long_step="${padding// /x}"
      payload="values[$first + $long_step + (((1)))]; $malformed_mapped; unmatched[ endpoint='$second'"
      ;;
    terminal-periods)
      long_step="${padding// /.}"
      payload="values[$numeric]; note=\"$first${long_step}\"; endpoint=$second"
      assert_exit 'progress terminal-periods retains 65536 periods' 65536 "${#long_step}"
      case "$long_step" in
        *[!.]*) bad 'progress terminal-periods contains only ASCII periods' ;;
        *) ok 'progress terminal-periods contains only ASCII periods' ;;
      esac
      assert_exit 'progress terminal-periods retains its exact file byte length' \
        65580 "$((${#payload} + 1))"
      ;;
    *) printf 'Unknown progress case: %s\n' "$name" >&2; return 2 ;;
  esac

  new_repo "$progress_repo"
  printf '%s\n' "$payload" >"$progress_repo/probe.txt"
  git -C "$progress_repo" add -- probe.txt
  run_gate "$progress_repo" "$PATH"
  assert_exit "progress $name completes with its expected decision" "$want" "$RUN_CODE"
  if [ "$want" -eq 0 ]; then
    assert_contains "progress $name reaches the clean verdict" "$RUN_OUTPUT" 'check-no-site-data: clean'
    assert_not_contains "progress $name does not report slices" "$RUN_OUTPUT" 'IPv6 address outside'
    # A successful all-slice pass cannot hide an eligible address appended to the same line.
    printf '%s endpoint=%s\n' "$payload" "$first" >"$progress_repo/probe.txt"
    git -C "$progress_repo" add -- probe.txt
    run_gate "$progress_repo" "$PATH"
    assert_exit "progress $name still rejects its same-line neighbor" 1 "$RUN_CODE"
    assert_contains "progress $name keeps its neighbor token" "$RUN_OUTPUT" "probe.txt:1:$first"
  else
    assert_contains "progress $name reports the IPv6 rule" "$RUN_OUTPUT" 'IPv6 address outside'
    assert_contains "progress $name reports its source line" "$RUN_OUTPUT" 'probe.txt:1:'
    assert_not_contains "progress $name has no clean verdict" "$RUN_OUTPUT" 'check-no-site-data: clean'
    case "$name" in
      terminal-periods)
        assert_contains "progress $name preserves its first address" "$RUN_OUTPUT" "probe.txt:1:$first"$'\n'
        ;;
      long-step)
        assert_contains "progress long-step preserves the neighbor" "$RUN_OUTPUT" "probe.txt:1:$second"
        assert_not_contains "progress long-step masks only its approved step" "$RUN_OUTPUT" "probe.txt:1:$first"
        ;;
      *) assert_contains "progress $name preserves its first address" "$RUN_OUTPUT" "probe.txt:1:$first" ;;
    esac
    case "$name" in
      terminal-periods)
        assert_contains "progress $name preserves its last address" "$RUN_OUTPUT" "probe.txt:1:$second"$'\n'
        ;;
      long-sparse|unicode-sparse|malformed-dense|malformed-long-step|literal-dense)
        assert_contains "progress $name preserves its last address" "$RUN_OUTPUT" "probe.txt:1:$second"
        ;;
    esac
  fi
  case "$name" in
    small-control|long-sparse|unicode-sparse|all-slices|literal-dense|terminal-periods)
      assert_not_contains "progress $name has no numeric-slice diagnostic" "$RUN_OUTPUT" "probe.txt:1:$numeric"
      ;;
  esac
  case "$name" in
    small-control|long-sparse|unicode-sparse|terminal-periods)
      assert_not_contains "progress $name omits sentence periods" "$RUN_OUTPUT" "probe.txt:1:${first}."
      ;;
  esac
  if [ "$name" = malformed-long-step ]; then
    local partial_mapped
    partial_mapped="$(printf '%s%s:%s' '::' 'ffff' '192')"
    assert_not_contains 'progress malformed mapped token has no shortened match' "$RUN_OUTPUT" "probe.txt:1:$partial_mapped"
  fi
  if [ "$name" = malformed-dense ]; then
    local inner_candidate
    inner_candidate="$(printf '%s%s' '::' 'f')"
    assert_not_contains 'progress fresh source line permits its inner slice' "$RUN_OUTPUT" "$inner_candidate"
  fi
}

if [ "$#" -gt 0 ]; then
  if [ "$#" -ne 2 ] || [ "$1" != '--progress-case' ]; then
    printf 'Usage: %s [--progress-case NAME]\n' "$0" >&2
    exit 2
  fi
  run_progress_case "$2" || exit $?
  printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
  [ "$FAIL" -eq 0 ]
  exit $?
fi

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

# The project's own container packages live at the repository path in the lowercase form GHCR
# requires. Like the repository URL, that coordinate is public repository metadata; the same owner
# namespace anywhere else, including a project-named package outside the path, is still rejected.
repo="$TEST_ROOT/ghcr-packages"
new_repo "$repo"
git -C "$repo" remote add origin https://github.com/ExampleOwner/EasySynQ.git
printf 'minio    ghcr.io/%s/easysynq/minio:RELEASE.X\n' "exampleowner" >"$repo/images.lock"
printf '    image: ghcr.io/%s/easysynq/mc:RELEASE.Y\n' "exampleowner" >"$repo/compose.yml"
git -C "$repo" add images.lock compose.yml
run_gate "$repo" "$PATH"
assert_exit "the project's GHCR package path stays allowed" 0 "$RUN_CODE"
assert_contains "the GHCR package path reaches the clean verdict" "$RUN_OUTPUT" "check-no-site-data: clean"

printf 'image: ghcr.io/%s/minio:RELEASE.X\n' "exampleowner" >"$repo/outside.txt"
printf 'image: ghcr.io/%s/easysynq-minio:RELEASE.X\n' "exampleowner" >"$repo/suffix.txt"
git -C "$repo" add outside.txt suffix.txt
run_gate "$repo" "$PATH"
assert_exit "an owner namespace outside the project path still reds" 1 "$RUN_CODE"
assert_contains "a package directly under the owner is reported" "$RUN_OUTPUT" "outside.txt:1"
assert_contains "a project-named package outside the path is reported" "$RUN_OUTPUT" "suffix.txt:1"
assert_not_contains "the exempt package lines stay clean" "$RUN_OUTPUT" "images.lock:1"
assert_not_contains "the GHCR diagnostic does not echo the identity" "$RUN_OUTPUT" "exampleowner"

# The container-topology widening must stay a single literal. `172.16.0.0` is sanctioned as the
# pinned `internal` subnet (the shipped TRUSTED_PROXY_CIDRS default); every other address in that
# space must still red, or the exemption becomes a hiding place for a real one. Both shapes are
# assembled at runtime so this regression test is not itself rejected by the gate.
repo="$TEST_ROOT/docker-pool-base"
new_repo "$repo"
printf 'TRUSTED_PROXY_CIDRS=%s.%s.0.0/24\n' "172" "16" >"$repo/settings.txt"
git -C "$repo" add settings.txt
run_gate "$repo" "$PATH"
assert_exit "the Docker bridge-pool base is sanctioned" 0 "$RUN_CODE"

printf 'edge=%s.%s.0.5\n' "172" "16" >"$repo/edge.txt"
git -C "$repo" add edge.txt
run_gate "$repo" "$PATH"
assert_exit "a neighbouring address in the same space still reds" 1 "$RUN_CODE"
assert_contains "the neighbouring address is reported" "$RUN_OUTPUT" "edge.txt:1"

# Each named case keeps Git enumeration and the real scanner intact. Rejected tokens are assembled
# from components so this tracked harness remains safe to scan after the rules are strengthened.
new_text_case() {
  local label="$1" content="$2" filename="${3:-probe.txt}"
  repo="$TEST_ROOT/$label"
  new_repo "$repo"
  printf '%s\n' "$content" >"$repo/$filename"
  git -C "$repo" add -- "$filename"
}

expect_rejection() {
  local label="$1" rule="$2" location="${3:-probe.txt:1}"
  assert_exit "$label rejects" 1 "$RUN_CODE"
  assert_contains "$label reports its rule" "$RUN_OUTPUT" "$rule"
  assert_contains "$label reports its location" "$RUN_OUTPUT" "$location"
  assert_not_contains "$label has no clean verdict" "$RUN_OUTPUT" "check-no-site-data: clean"
}

expect_clean() {
  local label="$1"
  assert_exit "$label stays clean" 0 "$RUN_CODE"
  assert_contains "$label reaches the clean verdict" "$RUN_OUTPUT" "check-no-site-data: clean"
}

ipv6_rule="IPv6 address outside"
ipv4_rule="IPv4 address outside"
fingerprint_rule="Certificate/key fingerprint"
leading_hex="$(printf '%s%s' '::' 'abcd')"
leading_numeric="$(printf '%s%s' '::' '2')"
slice_prefix="$(printf '%s%s' ':' ':')"
mapped_hex="$(printf '%s%s:%s:%s' '::' 'ffff' 'c0a8' '0101')"
unsafe_ipv4="$(printf '%s.%s.0.5' '172' '16')"
mapped_doc="$(printf '%s%s:%s.%s.%s.%s' '::' 'ffff' '192' '0' '2' '1')"
mapped_doc_upper="$(printf '%s%s:%s.%s.%s.%s' '::' 'FFFF' '192' '0' '2' '1')"
mapped_doc_invalid_octet="$(printf '%s%s:%s.%s.%s.%s' '::' 'ffff' '192' '0' '2' '999')"
mapped_unsafe="$(printf '%s%s:%s' '::' 'ffff' "$unsafe_ipv4")"

# Missing leading-compression matching must fail independently for each shape and context.
case_number=0
for token in "$leading_hex" "$leading_numeric" "$mapped_hex"; do
  for context in bare quoted uri; do
    case_number=$((case_number + 1))
    case "$context" in
      bare) content="endpoint=$token" ;;
      quoted) content="endpoint=\"$token\"" ;;
      uri) content="endpoint=http://[$token]:443" ;;
    esac
    label="leading-ipv6-$case_number-$context"
    new_text_case "$label" "$content"
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$ipv6_rule"
  done
done

# The bounded mask covers arithmetic/calls, receivers, and multiple spans without needing Python.
slice_cases=(
  "values[${slice_prefix}2]" "values[${slice_prefix}+2]" "values[${slice_prefix}-2]" "values [ ${slice_prefix} 2 ]"
  "values[${slice_prefix}abcd]" "values[${slice_prefix}2 + 1]" "values[${slice_prefix}f()]"
  "values[${slice_prefix}(2 + 1)]" "values[${slice_prefix}f(2 + 1)]" "values[${slice_prefix}f((2 + 1))]"
  "values[${slice_prefix}pkg.f(2, 1)]" "values[${slice_prefix}2 * 3 / 1 % 2]"
  "f()[${slice_prefix}2]" "values[0][${slice_prefix}2]" "object.values[${slice_prefix}2]"
  "values[${slice_prefix}2] + other[${slice_prefix}f()]" 'values[0:5:2]'
)
case_number=0
for slice in "${slice_cases[@]}"; do
  case_number=$((case_number + 1))
  label="slice-control-$case_number"
  new_text_case "$label" "$slice"
  run_gate "$repo" "$PATH"
  expect_clean "$label"
  label="slice-neighbor-$case_number"
  new_text_case "$label" "$slice; endpoint='$leading_hex'"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv6_rule"
  assert_contains "$label preserves the address token" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
  assert_not_contains "$label does not report the numeric slice" "$RUN_OUTPUT" "probe.txt:1:$leading_numeric"
done

# A prose word is not a source receiver, even with compact/call/index spelling.
prose_second="$(printf '%s%s' '::' 'def0')"
prose_number=0
for separator in '' ' ' $'\t'; do
  for padded in no yes; do
    prose_number=$((prose_number + 1))
    if [ "$padded" = yes ]; then
      bracket="[ $leading_hex ]"
    else
      bracket="[$leading_hex]"
    fi
    label="prose-bracket-$prose_number"
    new_text_case "$label" "DNS server${separator}${bracket}"
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$ipv6_rule"
    assert_contains "$label retains the address token" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
  done
done
for receiver_kind in call index; do
  case "$receiver_kind" in
    call) prose_receiver='DNS server()' ;;
    index) prose_receiver='DNS server[0]' ;;
  esac
  for separator in '' ' '; do
    prose_number=$((prose_number + 1))
    label="prose-$receiver_kind-$prose_number"
    new_text_case "$label" "${prose_receiver}${separator}[$leading_hex]"
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$ipv6_rule"
    assert_contains "$label retains the original token" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
  done
done
for content in "DNS server[$leading_hex] DNS backup[$prose_second]" \
  "DNS server[$leading_hex][$prose_second]"; do
  prose_number=$((prose_number + 1))
  label="prose-two-addresses-$prose_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv6_rule"
  assert_contains "$label preserves the first address" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
  assert_contains "$label preserves the second address" "$RUN_OUTPUT" "probe.txt:1:$prose_second"
done
for content in "values[$leading_numeric]; DNS server[$leading_hex]" \
  "values[0][$leading_numeric] server[$leading_hex]"; do
  prose_number=$((prose_number + 1))
  label="prose-after-source-$prose_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv6_rule"
  assert_contains "$label retains the prose address" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
  assert_not_contains "$label still masks the source slice" "$RUN_OUTPUT" "probe.txt:1:$leading_numeric"
done

# Explicit source framing applies uniformly to identifiers, calls and proven index chains.
framed_slice_cases=(
  "    values [$leading_hex]"
  "$(printf '\tvalues\t[%s]' "$leading_numeric")"
  "result = values [$leading_hex]"
  "consume(0, values [$leading_numeric])"
  "consume(values [$leading_hex])"
  "result = 1 + values [$leading_hex]"
  "result = 1; values [$leading_hex]"
  "result = f() [$leading_hex]"
  "result = (values + other) [$leading_numeric]"
  "result = object.values [$leading_hex]"
  "result = values[\"key\"] [$leading_hex]"
  "values[0][$leading_numeric] [$leading_hex][$leading_numeric]"
  "outer[bad + inner[${slice_prefix}f()]]"
  "$(printf '`values[%s]`' "$leading_hex")"
)
case_number=0
for slice in "${framed_slice_cases[@]}"; do
  case_number=$((case_number + 1))
  label="framed-slice-control-$case_number"
  new_text_case "$label" "$slice"
  run_gate "$repo" "$PATH"
  if [ "$case_number" -eq 14 ]; then
    expect_rejection "$label" "$ipv6_rule"
    assert_contains "$label retains its literal address" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
  else
    expect_clean "$label"
  fi
done

# Literal contents and invalid prose cannot acquire source permission through punctuation.
literal_context_cases=(
  "message = \"server[$leading_hex]\""
  "message = 'server[$leading_hex]'"
  "message = \"value = server[$leading_hex]\""
  "\"server[$leading_hex]\""
  'message = "escaped \"quote\"; path \\host; server['"$leading_hex"']"'
  "message = \"server[$leading_hex]"
  "message = 'server[$leading_hex]"
  "message = \"\"\"server[$leading_hex]\"\"\""
  "message = '''server[$leading_hex]'''"
  'message = `value = server['"$leading_hex"']`'
  "- server[$leading_hex]"
  "+ server[$leading_hex]"
  "* server[$leading_hex]"
  "1. server[$leading_hex]"
  "# value = server[$leading_hex]"
  "result = 1; # value = server[$leading_hex]"
  "message = \"server()[$leading_hex]\""
  "message = \"server[0][$leading_hex]\""
  "- server()[$leading_hex]"
  "- server[0][$leading_hex]"
  "message = \"(server + other)[$leading_hex]\""
  "DNS prose; server[$leading_hex]"
  "DNS prose = server[$leading_hex]"
  "DNS prose + server[$leading_hex]"
  "DNS prose (server[$leading_hex])"
  "DNS prose, server[$leading_hex]"
  "DNS prose [server[$leading_hex]]"
  "DNS prose; result = values[$leading_hex]"
  "values[0 ???]; server[$leading_hex]"
  "values[(0]; server[$leading_hex]"
  "values[0:1:2:3]; server[$leading_hex]"
  'message = "escaped \"; result = server['"$leading_hex"']'
  "message = \"server[$leading_hex]"'\'
  "note = \"safe\"server[$leading_hex]"
  "values[0]server[$leading_hex]"
  "f(\"safe\"server[$leading_hex])"
  "f(values[0]server[$leading_hex])"
  "\"safe\"()[$leading_hex]"
  "f(\"safe\"()[$leading_hex])"
  "values[0]()[$leading_hex]"
  "f(values[0]()[$leading_hex])"
)
case_number=0
for content in "${literal_context_cases[@]}"; do
  case_number=$((case_number + 1))
  label="literal-context-$case_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv6_rule"
  assert_contains "$label retains the complete address" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
done

# One surviving address cannot conceal masking of the other, including unknown-index recovery.
literal_dual_cases=(
  "message = \"server[$leading_hex]; value = server[$prose_second]\""
  "message = \"server[$leading_hex]\"; result = values[$leading_numeric]; other = \"server[$prose_second]\""
  "values[$leading_hex + (((1)))]; server[$prose_second]"
  "\"server[$leading_hex]\" + \"value = server[$prose_second]\""
  'message = `server['"$leading_hex"']`; server['"$prose_second"']'
)
case_number=0
for content in "${literal_dual_cases[@]}"; do
  case_number=$((case_number + 1))
  label="literal-dual-$case_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv6_rule"
  assert_contains "$label retains the first complete token" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
  assert_contains "$label retains the second complete token" "$RUN_OUTPUT" "probe.txt:1:$prose_second"
  if [ "$case_number" -eq 2 ]; then
    assert_not_contains "$label still masks its real source slice" "$RUN_OUTPUT" "probe.txt:1:$leading_numeric"
  fi
done

# Opaque address content remains visible while actual source slices keep their provenance.
literal_source_neighbors=(
  "result = values[$leading_numeric]; message = \"server[$leading_hex]\""
  "message = \"server[$leading_hex]\"; result = values[$leading_numeric]"
  "result = values[\"server[$leading_hex]\"][$leading_numeric]"
  "result = values[$leading_numeric]; # server[$leading_hex]"
  "result = values[$leading_numeric]; note = 'server[$leading_hex]'"
  "note = 'server[$leading_hex]'; values[$leading_numeric]"
)
case_number=0
for content in "${literal_source_neighbors[@]}"; do
  case_number=$((case_number + 1))
  label="literal-source-neighbor-$case_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv6_rule"
  assert_contains "$label preserves its literal address" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
  assert_not_contains "$label masks its genuine slice" "$RUN_OUTPUT" "probe.txt:1:$leading_numeric"
done
new_text_case "nested-inner-source-neighbor" \
  "outer[bad + inner[${slice_prefix}f()]]; endpoint='$leading_hex'"
run_gate "$repo" "$PATH"
expect_rejection "nested-inner-source-neighbor" "$ipv6_rule"
assert_contains 'nested inner preserves the same-line neighbor' "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
assert_not_contains 'nested inner remains a genuine source slice' "$RUN_OUTPUT" \
  "probe.txt:1:$(printf '%s%s' ':' ':f')"

# Measured executable frames and benign opaque atoms remain supported within fixed depth.
literal_frame_positives=(
  "ordering = tuple(reversed(values[$leading_numeric] + values[1:][$leading_numeric]))"
  "reversed_case = dataclasses.replace(case, observations=values[$leading_numeric])"
  "for ordering in (values, values[$leading_numeric]):"
  "for observations in (case.observations[$leading_numeric], case.observations[1:] + case.observations[:1]):"
  "note = 'key'; result = values[$leading_numeric]"
  'note = "escaped \"quote\" and \\slash [key] # = ;"; values['"$leading_numeric"']'
  "result = values['key'][$leading_numeric]"
  "result = values[1:][$leading_numeric]"
  "result = values[0:5:2][$leading_numeric]"
  "result = consume(values[$leading_numeric], 1)"
  "values[(0 + 1)][$leading_numeric]"
  "values[0 * (1 + 2)][$leading_numeric]"
)
case_number=0
for content in "${literal_frame_positives[@]}"; do
  case_number=$((case_number + 1))
  label="literal-frame-positive-$case_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  expect_clean "$label"
done

# Fixed physical-line byte coordinates characterize refill boundaries without changing grammar.
run_window_boundary_cases() {
  local LC_ALL=C
  local case_name prefix padding payload want_length want_second want_mask want_numeric
  for case_name in window-key-utf8 window-odd-escape window-even-escape \
    window-triple-double window-triple-single window-source-frames window-slice-close \
    window-escape-eof window-invalid-prefix; do
    want_second=0
    want_mask=0
    want_numeric=0
    case "$case_name" in
      window-key-utf8)
        prefix='values["'
        printf -v padding '%*s' "$((4095 - ${#prefix}))" ''
        payload="${padding}${prefix}λ"
        printf -v padding '%*s' "$((8178 - ${#payload}))" ''
        payload+="${padding}server[$leading_hex]\"][$leading_numeric]; endpoint=\"$prose_second\""
        want_length=8218
        want_second=1
        want_mask=1
        assert_exit "$case_name quote and UTF-8 bytes cross the first boundary" \
          '"λ' "${payload:4094:3}"
        assert_exit "$case_name literal and real closes precede the genuine slice" \
          "]\"][$leading_numeric]" "${payload:8191:8}"
        ;;
      window-odd-escape)
        prefix='note = "x'
        printf -v padding '%*s' "$((4095 - ${#prefix}))" ''
        payload="${padding}${prefix}"'\'
        payload+="\"; value = server[$leading_hex]\"; values[$leading_numeric]; endpoint=$prose_second"
        want_length=4152
        want_second=1
        want_mask=1
        assert_exit "$case_name escape crosses the boundary before a literal semicolon" \
          '\";' "${payload:4095:3}"
        ;;
      window-even-escape)
        prefix="note = 'x"
        printf -v padding '%*s' "$((4095 - ${#prefix}))" ''
        payload="${padding}${prefix}"'\\'
        payload+="'; values[$leading_numeric]; endpoint=\"server[$leading_hex]\""
        want_length=4138
        want_mask=1
        assert_exit "$case_name paired escape crosses the boundary before the real close" \
          "\\\\'" "${payload:4095:3}"
        ;;
      window-triple-double)
        prefix='note = '
        printf -v padding '%*s' "$((4095 - ${#prefix}))" ''
        payload="${padding}${prefix}\"\"\"value = server[$leading_hex]\"\"\"; values[$leading_numeric]"
        want_length=4136
        want_numeric=1
        assert_exit "$case_name opener spans the boundary with a one-plus-two split" \
          '"""' "${payload:4095:3}"
        ;;
      window-triple-single)
        prefix='note = '
        printf -v padding '%*s' "$((4094 - ${#prefix}))" ''
        payload="${padding}${prefix}'''value = server[$leading_hex]'''; values[$leading_numeric]"
        want_length=4135
        want_numeric=1
        assert_exit "$case_name opener spans the boundary with a two-plus-one split" \
          "'''" "${payload:4094:3}"
        ;;
      window-source-frames)
        payload='result = 1'
        printf -v padding '%*s' "$((4095 - ${#payload}))" ''
        payload+="${padding};consume("
        printf -v padding '%*s' "$((8189 - ${#payload}))" ''
        payload+="${padding}values[$leading_numeric]"
        printf -v padding '%*s' "$((12287 - ${#payload}))" ''
        payload+="${padding}, values[0"
        printf -v padding '%*s' "$((16383 - ${#payload}))" ''
        payload+="${padding}][$leading_numeric]); endpoint=$leading_hex"
        want_length=16407
        want_mask=1
        assert_exit "$case_name completed statement ends at the first boundary" ';' "${payload:4095:1}"
        assert_exit "$case_name source root crosses the second boundary" 'values' "${payload:8189:6}"
        assert_exit "$case_name argument comma ends at the third boundary" ',' "${payload:12287:1}"
        assert_exit "$case_name proven index close and chained slice cross the fourth boundary" \
          "][$leading_numeric]" "${payload:16383:6}"
        ;;
      window-slice-close)
        prefix='values'
        printf -v padding '%*s' "$((4095 - ${#prefix}))" ''
        payload="${padding}${prefix}[$leading_numeric"
        printf -v padding '%4093s' ''
        payload+="${padding}]; endpoint=$leading_hex"
        want_length=8210
        want_mask=1
        assert_exit "$case_name opener and step cross the first boundary" \
          "[$leading_numeric" "${payload:4095:4}"
        assert_exit "$case_name accepted close begins the third window" ']' "${payload:8192:1}"
        ;;
      window-escape-eof)
        printf -v padding '%4073s' ''
        payload="note = \"${padding}server[$leading_hex]"'\'
        want_length=4096
        assert_exit "$case_name unmatched escape is the last physical-line byte" '\' "${payload:4095:1}"
        ;;
      window-invalid-prefix)
        payload='DNS prose '
        printf -v padding '%*s' "$((4096 - ${#payload}))" ''
        payload+="${padding}server[$leading_hex]"
        want_length=4110
        assert_exit "$case_name retains the original invalid prefix" 'DNS prose ' "${payload:0:10}"
        assert_exit "$case_name apparent receiver starts at the next window" 'server[' "${payload:4096:7}"
        ;;
    esac
    assert_exit "$case_name has its reviewed physical-line byte length" "$want_length" "${#payload}"
    new_text_case "$case_name" "$payload"
    run_gate "$repo" "$PATH"
    expect_rejection "$case_name" "$ipv6_rule"
    assert_contains "$case_name retains its complete address" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
    if [ "$want_second" -eq 1 ]; then
      assert_contains "$case_name retains its complete neighbor" "$RUN_OUTPUT" "probe.txt:1:$prose_second"
    fi
    if [ "$want_mask" -eq 1 ]; then
      assert_not_contains "$case_name masks its genuine source slices" "$RUN_OUTPUT" "probe.txt:1:$leading_numeric"
    fi
    if [ "$want_numeric" -eq 1 ]; then
      assert_contains "$case_name retains its unsupported apparent slice" "$RUN_OUTPUT" "probe.txt:1:$leading_numeric"
    fi
  done
}
run_window_boundary_cases

new_text_case "slice-uri-neighbor" "values[${slice_prefix}f()]; endpoint=http://[$leading_hex]:443"
run_gate "$repo" "$PATH"
expect_rejection "slice-uri-neighbor" "$ipv6_rule"

new_text_case "leading-two-addresses" "$leading_hex $mapped_hex"
run_gate "$repo" "$PATH"
expect_rejection "leading-two-addresses" "$ipv6_rule"
assert_contains "both addresses: first token survives" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
assert_contains "both addresses: second token survives" "$RUN_OUTPUT" "probe.txt:1:$mapped_hex"

# A bracket without a receiver, or a step outside the recognized grammar, cannot hide an address.
invalid_slice_cases=(
  "= [$leading_hex]" "values[$leading_hex:f]"
  "values[$leading_hex + \"x\"]" "values[$leading_hex + (1]"
  "values[$leading_hex + (((1)))]"
)
case_number=0
for content in "${invalid_slice_cases[@]}"; do
  case_number=$((case_number + 1))
  label="slice-outside-grammar-$case_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv6_rule"
done

# Complete-token boundaries must avoid accidental matches inside identifiers and malformed groups.
boundary_cases=("prefix$leading_hex" "${leading_hex}x" "${leading_hex}e" "${leading_hex}..")
case_number=0
for content in "${boundary_cases[@]}"; do
  case_number=$((case_number + 1))
  label="leading-boundary-$case_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  if [ "$case_number" -eq 4 ]; then
    expect_rejection "$label" "$ipv6_rule"
    assert_contains "$label keeps the complete dot-free token" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"$'\n'
    assert_not_contains "$label omits terminal prose periods" "$RUN_OUTPUT" "probe.txt:1:${leading_hex}."
  else
    assert_not_contains "$label has no shortened IPv6 match" "$RUN_OUTPUT" "$ipv6_rule"
  fi
done

new_text_case "structural-ipv6-controls" 'bind=:: loopback=::1 documentation=2001:db8::1 canonical=0:0:0:0:0:0:0:1 time=12:30 ratio=2:1 mac=00:15:5D:00:00:01'
run_gate "$repo" "$PATH"
expect_clean "structural-ipv6-controls"
full_ipv6="$(printf '%s:%s:%s:%s:%s:%s:%s:%s' 'fdab' '1234' '5678' '9012' '3456' '7890' 'abcd' 'ef01')"
new_text_case "existing-full-ipv6" "$full_ipv6"
run_gate "$repo" "$PATH"
expect_rejection "existing-full-ipv6" "$ipv6_rule"

# Terminal prose periods normalize only after complete-core and lexical-boundary checks.
case_number=0
for suffix in '.' '. ' $'.\t'; do
  for prefix in '' "values[${slice_prefix}2 + 1]; " "values[${slice_prefix}f()]; "; do
    case_number=$((case_number + 1))
    label="leading-sentence-period-$case_number"
    new_text_case "$label" "${prefix}${leading_hex}${suffix}"
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$ipv6_rule"
    assert_contains "$label keeps the complete token" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
    assert_not_contains "$label omits the prose period" "$RUN_OUTPUT" "probe.txt:1:${leading_hex}."
  done
done
new_text_case "sentence-two-neighbors" "values[${slice_prefix}f((2 + 1))]; $leading_hex. $mapped_hex."
run_gate "$repo" "$PATH"
expect_rejection "sentence-two-neighbors" "$ipv6_rule"
assert_contains "sentence first neighbor survives" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
assert_contains "sentence second neighbor survives" "$RUN_OUTPUT" "probe.txt:1:$mapped_hex"

# Focused punctuation cases are self-contained for unchanged-source red characterization.
run_punctuation_cases() {
  local case_index label content punctuation_loopback punctuation_nonloopback
  local -a punctuation_labels=(
    punctuation-quoted-period punctuation-ellipsis punctuation-quoted-ellipsis
    punctuation-parenthesis punctuation-single-quote punctuation-uri-bracket
    punctuation-backtick punctuation-ellipsis-space punctuation-ellipsis-tab
  )
  local -a punctuation_cases=(
    "note=\"The endpoint is $leading_hex.\""
    "The endpoint is ${leading_hex}..."
    "note=\"The endpoint is ${leading_hex}...\""
    "The endpoint is (${leading_hex}.)"
    "note='The endpoint is ${leading_hex}.'"
    "endpoint=http://[${leading_hex}.]:443"
    '`The endpoint is '"${leading_hex}"'...`'
    "${leading_hex}... "
    "${leading_hex}..."$'\t'
  )
  for ((case_index=0; case_index<${#punctuation_cases[@]}; case_index++)); do
    label="${punctuation_labels[case_index]}"
    new_text_case "$label" "${punctuation_cases[case_index]}"
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$ipv6_rule"
    assert_contains "$label keeps the complete dot-free token" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"$'\n'
    assert_not_contains "$label omits terminal prose periods" "$RUN_OUTPUT" "probe.txt:1:${leading_hex}."
  done

  punctuation_loopback="$(printf '%s%s' "$slice_prefix" '1')"
  new_text_case "punctuation-exact-loopback" "note=\"${punctuation_loopback}...\""
  run_gate "$repo" "$PATH"
  expect_clean "punctuation-exact-loopback"
  assert_not_contains "punctuation exact loopback has no IPv6 diagnostic" "$RUN_OUTPUT" "$ipv6_rule"
  punctuation_nonloopback="$(printf '%s%s' "$slice_prefix" '1abc')"
  new_text_case "punctuation-one-prefix" "note=\"${punctuation_nonloopback}...\""
  run_gate "$repo" "$PATH"
  expect_rejection "punctuation-one-prefix" "$ipv6_rule"
  assert_contains "punctuation nonloopback retains its entire token" "$RUN_OUTPUT" \
    "probe.txt:1:$punctuation_nonloopback"$'\n'
  assert_not_contains "punctuation nonloopback omits terminal periods" "$RUN_OUTPUT" \
    "probe.txt:1:${punctuation_nonloopback}."

  new_text_case "punctuation-two-neighbors" \
    "values[$leading_numeric]; note=\"${leading_hex}...\"; other=(${prose_second}.)"
  run_gate "$repo" "$PATH"
  expect_rejection "punctuation-two-neighbors" "$ipv6_rule"
  assert_contains "punctuation first complete neighbor survives" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"$'\n'
  assert_contains "punctuation second complete neighbor survives" "$RUN_OUTPUT" "probe.txt:1:$prose_second"$'\n'
  assert_not_contains "punctuation first neighbor omits periods" "$RUN_OUTPUT" "probe.txt:1:${leading_hex}."
  assert_not_contains "punctuation second neighbor omits periods" "$RUN_OUTPUT" "probe.txt:1:${prose_second}."
  assert_not_contains "punctuation neighbors keep the genuine slice masked" "$RUN_OUTPUT" "probe.txt:1:$leading_numeric"

  case_index=0
  for content in "note=\"${mapped_doc}...\"" "endpoint=(${mapped_doc_upper}...)"; do
    case_index=$((case_index + 1))
    label="punctuation-mapped-documentation-$case_index"
    new_text_case "$label" "$content"
    run_gate "$repo" "$PATH"
    expect_clean "$label"
    assert_not_contains "$label has no partial IPv6 match" "$RUN_OUTPUT" "$ipv6_rule"
  done
  case_index=0
  for content in "note=\"${mapped_unsafe}...\"" "endpoint=(${mapped_unsafe}...)"; do
    case_index=$((case_index + 1))
    label="punctuation-mapped-unsafe-$case_index"
    new_text_case "$label" "$content"
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$ipv4_rule"
    assert_contains "$label preserves the exact unsafe IPv4 token" "$RUN_OUTPUT" "probe.txt:1:$unsafe_ipv4"$'\n'
    assert_not_contains "$label has no partial IPv6 match" "$RUN_OUTPUT" "$ipv6_rule"
  done

  local -a punctuation_malformed=(
    "$(printf '%s%s:%s.%s' "$slice_prefix" 'ffff' '192' '0')"
    "$(printf '%s%s:%s..%s.%s.%s' "$slice_prefix" 'ffff' '192' '0' '2' '1')"
    "${mapped_doc}.1" "${leading_hex}.1" "${leading_hex}e"
  )
  case_index=0
  for content in "${punctuation_malformed[@]}"; do
    case_index=$((case_index + 1))
    label="punctuation-malformed-$case_index"
    new_text_case "$label" "note=\"${content}...\""
    run_gate "$repo" "$PATH"
    assert_not_contains "$label never extracts a shorter IPv6 prefix" "$RUN_OUTPUT" "$ipv6_rule"
  done
  case_index=0
  for content in "prefix${leading_hex}..." "${leading_hex}...x" "${leading_hex}..._"; do
    case_index=$((case_index + 1))
    label="punctuation-continuation-$case_index"
    new_text_case "$label" "note=\"$content\""
    run_gate "$repo" "$PATH"
    assert_not_contains "$label denies a shortened IPv6 token" "$RUN_OUTPUT" "$ipv6_rule"
  done
}
run_punctuation_cases

# General mixed addresses retain the complete IPv6 token independently of the IPv4 tail policy.
run_mixed_ipv6_cases() {
  local doc_quad mixed_zero mixed_one mixed_five mixed_unsafe token content context label
  local case_index=0 malformed legacy_second legacy_interior structural
  doc_quad="$(printf '%s.%s.%s.%s' '192' '0' '2' '1')"
  mixed_zero="${slice_prefix}${doc_quad}"
  mixed_one="${slice_prefix}abcd:${doc_quad}"
  mixed_five="${slice_prefix}0:1:a:Bc:ABCD:${doc_quad}"
  mixed_unsafe="${slice_prefix}abcd:${unsafe_ipv4}"

  for token in "$mixed_zero" "$mixed_one" "$mixed_five"; do
    for context in bare quoted uri period ellipsis; do
      case_index=$((case_index + 1))
      label="mixed-ipv6-shape-$case_index-$context"
      case "$context" in
        bare) content="$token" ;;
        quoted) content="endpoint=\"$token\"" ;;
        uri) content="endpoint=\"http://[$token]:443\"" ;;
        period) content="The endpoint is $token. " ;;
        ellipsis) content="note=\"The endpoint is ${token}...\"" ;;
      esac
      new_text_case "$label" "$content"
      run_gate "$repo" "$PATH"
      expect_rejection "$label" "$ipv6_rule"
      assert_contains "$label retains the complete token" "$RUN_OUTPUT" "probe.txt:1:$token"$'\n'
      assert_not_contains "$label omits terminal periods" "$RUN_OUTPUT" "probe.txt:1:${token}."
    done
  done

  local -a mixed_edges=(
    "${slice_prefix}a:$(printf '%s.%s.%s.%s' '0' '9' '10' '99')"
    "${slice_prefix}a:$(printf '%s.%s.%s.%s' '100' '199' '200' '249')"
    "${slice_prefix}a:$(printf '%s.%s.%s.%s' '250' '255' '0' '1')"
    "${slice_prefix}$(printf '%s.%s.%s.%s' '0' '0' '0' '0')"
    "${slice_prefix}$(printf '%s.%s.%s.%s' '0' '0' '0' '1')"
    "${slice_prefix}0:ffff:${doc_quad}"
  )
  for token in "${mixed_edges[@]}"; do
    case_index=$((case_index + 1))
    label="mixed-ipv6-octet-or-alias-$case_index"
    new_text_case "$label" "endpoint=\"${token}...\""
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$ipv6_rule"
    assert_contains "$label retains the complete token" "$RUN_OUTPUT" "probe.txt:1:$token"$'\n'
    assert_not_contains "$label omits terminal periods" "$RUN_OUTPUT" "probe.txt:1:${token}."
  done

  new_text_case "mixed-ipv6-unsafe-tail" "endpoint=\"http://[$mixed_unsafe]:443\""
  run_gate "$repo" "$PATH"
  expect_rejection "mixed-ipv6-unsafe-tail" "$ipv6_rule"
  assert_contains "mixed unsafe tail retains the complete IPv6 token" "$RUN_OUTPUT" \
    "probe.txt:1:$mixed_unsafe"$'\n'
  assert_contains "mixed unsafe tail retains the independent IPv4 token" "$RUN_OUTPUT" \
    "probe.txt:1:$unsafe_ipv4"$'\n'

  new_text_case "mixed-ipv6-two-neighbors" \
    "values[$leading_numeric]; note=\"${mixed_one}...\"; endpoint='$leading_hex'"
  run_gate "$repo" "$PATH"
  expect_rejection "mixed-ipv6-two-neighbors" "$ipv6_rule"
  assert_contains "mixed neighbor remains complete" "$RUN_OUTPUT" "probe.txt:1:$mixed_one"$'\n'
  assert_contains "pure hex neighbor remains complete" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"$'\n'
  assert_not_contains "mixed neighbors preserve the genuine slice mask" "$RUN_OUTPUT" \
    "probe.txt:1:$leading_numeric"$'\n'

  for token in "$mapped_doc" "$mapped_doc_upper" "$mapped_doc_invalid_octet"; do
    case_index=$((case_index + 1))
    label="mixed-ipv6-exact-mapped-control-$case_index"
    new_text_case "$label" "endpoint=\"http://[$token]:443\""
    run_gate "$repo" "$PATH"
    expect_clean "$label"
    assert_not_contains "$label delegates the whole mapped token" "$RUN_OUTPUT" "$ipv6_rule"
  done
  new_text_case "mixed-ipv6-exact-mapped-unsafe" "endpoint=\"${mapped_unsafe}...\""
  run_gate "$repo" "$PATH"
  expect_rejection "mixed-ipv6-exact-mapped-unsafe" "$ipv4_rule"
  assert_contains "exact mapped unsafe retains the IPv4 token" "$RUN_OUTPUT" \
    "probe.txt:1:$unsafe_ipv4"$'\n'
  assert_not_contains "exact mapped unsafe still delegates IPv6" "$RUN_OUTPUT" "$ipv6_rule"

  new_text_case "mixed-ipv6-pure-hex-control" "endpoint='$leading_hex'"
  run_gate "$repo" "$PATH"
  expect_rejection "mixed-ipv6-pure-hex-control" "$ipv6_rule"
  assert_contains "mixed pure hex control retains its complete token" "$RUN_OUTPUT" \
    "probe.txt:1:$leading_hex"$'\n'
  for structural in "$slice_prefix" "${slice_prefix}1"; do
    case_index=$((case_index + 1))
    label="mixed-ipv6-exact-structural-control-$case_index"
    new_text_case "$label" "endpoint='$structural'"
    run_gate "$repo" "$PATH"
    expect_clean "$label"
    assert_not_contains "$label retains the exact structural sanction" "$RUN_OUTPUT" "$ipv6_rule"
  done

  local -a mixed_malformed=(
    "${slice_prefix}0:1:a:Bc:ABCD:2:${doc_quad}"
    "${slice_prefix}abcde:${doc_quad}"
    "${slice_prefix}abcd:$(printf '%s.%s.%s' '192' '0' '2')"
    "${mixed_one}.1"
    "${slice_prefix}abcd:$(printf '%s..%s.%s.%s' '192' '0' '2' '1')"
    "${slice_prefix}abcd:$(printf '%s.%s.%s.%s' '256' '0' '2' '1')"
    "${slice_prefix}abcd:$(printf '%s.%s.%s.%s' '192' '0' '2' '999')"
    "${slice_prefix}abcd:$(printf '%s.%s.%s.%s' '192' '0' '2' '01')"
    "${slice_prefix}abcd:$(printf '%s.%s.%s.%s' '192' '0' '' '1')"
  )
  for malformed in "${mixed_malformed[@]}"; do
    for context in bare ellipsis; do
      case_index=$((case_index + 1))
      label="mixed-ipv6-malformed-$case_index-$context"
      case "$context" in
        bare) content="$malformed" ;;
        ellipsis) content="note=\"${malformed}...\"" ;;
      esac
      new_text_case "$label" "$content"
      run_gate "$repo" "$PATH"
      assert_not_contains "$label never extracts a shorter IPv6 token" "$RUN_OUTPUT" "$ipv6_rule"
    done
  done

  legacy_second="${slice_prefix}abcd${slice_prefix}${doc_quad}"
  legacy_interior="abcd${slice_prefix}192"
  new_text_case "mixed-ipv6-second-marker-legacy" "$legacy_second"
  run_gate "$repo" "$PATH"
  expect_rejection "mixed-ipv6-second-marker-legacy" "$ipv6_rule"
  assert_contains "second marker preserves the characterized legacy interior token" "$RUN_OUTPUT" \
    "probe.txt:1:$legacy_interior"$'\n'
  assert_not_contains "second marker never reports the full leading candidate" "$RUN_OUTPUT" \
    "probe.txt:1:$legacy_second"$'\n'
  assert_not_contains "second marker never reports a shortened leading token" "$RUN_OUTPUT" \
    "probe.txt:1:$leading_hex"$'\n'
  new_text_case "mixed-ipv6-second-marker-no-legacy" "${slice_prefix}${slice_prefix}${doc_quad}"
  run_gate "$repo" "$PATH"
  expect_clean "mixed-ipv6-second-marker-no-legacy"
  assert_not_contains "adjacent markers never extract an IPv6 token" "$RUN_OUTPUT" "$ipv6_rule"

  for content in "prefix${mixed_one}" "_${mixed_one}" "${mixed_one}x" \
    "${mixed_one}_" "${mixed_one}:" "${mixed_one}.x"; do
    case_index=$((case_index + 1))
    label="mixed-ipv6-continuation-$case_index"
    new_text_case "$label" "note=\"$content\""
    run_gate "$repo" "$PATH"
    assert_not_contains "$label preserves whole-token boundaries" "$RUN_OUTPUT" "$ipv6_rule"
  done

  new_text_case "mixed-ipv6-shell-only" "values[$leading_numeric]; endpoint='$mixed_one'"
  run_gate "$repo" "$fake_bin"
  expect_rejection "mixed-ipv6-shell-only" "$ipv6_rule"
  assert_contains "mixed shell-only control retains the complete token" "$RUN_OUTPUT" \
    "probe.txt:1:$mixed_one"$'\n'
}
run_mixed_ipv6_cases

# Dotted mapped endpoints are whole tokens; the IPv4 policy decides sanctioned and unsafe endpoints.
case_number=0
for token in "$mapped_doc" "$mapped_doc_upper" "$mapped_doc_invalid_octet"; do
  for context in bare quoted uri sentence; do
    case_number=$((case_number + 1))
    case "$context" in
      bare) content="$token" ;;
      quoted) content="endpoint=\"$token\"" ;;
      uri) content="http://[$token]:443" ;;
      sentence) content="$token. " ;;
    esac
    label="mapped-documentation-$case_number"
    new_text_case "$label" "$content"
    run_gate "$repo" "$PATH"
    expect_clean "$label"
    assert_not_contains "$label has no partial hex match" "$RUN_OUTPUT" "$ipv6_rule"
  done
done
for suffix in '' '.' '. '; do
  case_number=$((case_number + 1))
  label="mapped-unsafe-$case_number"
  new_text_case "$label" "${mapped_unsafe}${suffix}"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv4_rule"
  assert_not_contains "$label has no partial hex match" "$RUN_OUTPUT" "$ipv6_rule"
done
new_text_case "mapped-slice-and-neighbor" "values[${slice_prefix}f()]; $mapped_doc. $mapped_unsafe."
run_gate "$repo" "$PATH"
expect_rejection "mapped-slice-and-neighbor" "$ipv4_rule"
assert_contains "mapped unsafe endpoint survives" "$RUN_OUTPUT" "probe.txt:1:$unsafe_ipv4"
assert_not_contains "mapped mixed line has no partial hex match" "$RUN_OUTPUT" "$ipv6_rule"
malformed_dotted_cases=(
  "$(printf '%s%s:%s.%s' '::' 'ffff' '192' '0')"
  "$(printf '%s%s:%s..%s.%s.%s' '::' 'ffff' '192' '0' '2' '1')"
  "${mapped_doc}.1" "${mapped_doc}.." "${mapped_doc}.1."
  "${leading_hex}e." "${leading_hex}.1."
)
case_number=0
for content in "${malformed_dotted_cases[@]}"; do
  case_number=$((case_number + 1))
  label="mapped-malformed-$case_number"
  new_text_case "$label" "$content"
  run_gate "$repo" "$PATH"
  assert_not_contains "$label never extracts a shorter hex prefix" "$RUN_OUTPUT" "$ipv6_rule"
done

# Exercise the new scanner branches with exactly the shell-only PATH used by the existing control.
new_text_case "leading-shell-only" "values[${slice_prefix}f()]; $leading_hex."
run_gate "$repo" "$fake_bin"
expect_rejection "leading-shell-only" "$ipv6_rule"

# Locks are selected by content, not extension. Explicit selection must agree with the default.
for filename in uv.lock images.lock other.lock package-lock.json; do
  label="text-lock-${filename//./-}"
  new_text_case "$label" "endpoint=$unsafe_ipv4" "$filename"
  run_gate "$repo" "$PATH"
  expect_rejection "$label default" "$ipv4_rule" "$filename:1"
  run_gate "$repo" "$PATH" "$filename"
  expect_rejection "$label explicit" "$ipv4_rule" "$filename:1"
done
local_host="$(printf '%s.%s' 'fixture-host' 'local')"
new_text_case "text-lock-host" "registry=https://$local_host/simple" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "text-lock-host" "Non-example .local hostname" "uv.lock:1"

repo="$TEST_ROOT/binary-lock"
new_repo "$repo"
printf '\0endpoint=%s\n' "$unsafe_ipv4" >"$repo/binary.lock"
git -C "$repo" add -- binary.lock
run_gate "$repo" "$PATH"
expect_clean "binary-lock default"
run_gate "$repo" "$PATH" binary.lock
expect_clean "binary-lock explicit"

public_version="$(printf '%s.%s.%s.%s' '1' '1' '1' '2')"
package_head=$'[[package]]\nname = "click-plugins"'
package_version="version = \"$public_version\""
package_source='source = { registry = "https://pypi.org/simple" }'
qualified_package="$(printf '%s\n%s\n%s' "$package_head" "$package_version" "$package_source")"
digest_hex="$(printf '%064d' 0)"
public_artifact="https://files.pythonhosted.org/packages/aa/bb/$digest_hex/click_plugins-${public_version}.tar.gz"
public_wheel="https://files.pythonhosted.org/packages/aa/bb/$digest_hex/click_plugins-${public_version}-py2.py3-none-any.whl"
benign_lock="$(printf '[[package]]\nname = "click-plugins"\nversion = "%s"\nsource = { registry = "https://pypi.org/simple" }\nsdist = { url = "%s", hash = "sha256:%s" }\nwheels = [{ url = "%s", hash = "sha256:%s" }]\n' "$public_version" "$public_artifact" "$digest_hex" "$public_wheel" "$digest_hex")"
new_text_case "lock-public-metadata" "$benign_lock" uv.lock
run_gate "$repo" "$PATH"
expect_clean "lock-public-metadata default"
run_gate "$repo" "$PATH" uv.lock
expect_clean "lock-public-metadata explicit"
run_gate "$repo" "$fake_bin" uv.lock
expect_clean "lock-public-metadata shell-only"

for context in endpoint host; do
  case "$context" in
    endpoint) content="endpoint=$public_version" ;;
    host) content="registry=https://$public_version/simple" ;;
  esac
  new_text_case "version-not-global-$context" "$content" uv.lock
  run_gate "$repo" "$PATH"
  expect_rejection "version-not-global-$context" "$ipv4_rule" "uv.lock:1"
done
new_text_case "lock-version-neighbor" "$(printf '%s\n%s # endpoint=%s\n%s' "$package_head" "$package_version" "$unsafe_ipv4" "$package_source")" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "lock-version-neighbor" "$ipv4_rule" "uv.lock:3"
assert_contains "version sanction preserves neighboring address" "$RUN_OUTPUT" "uv.lock:3:$unsafe_ipv4"
assert_not_contains "version sanction removes only metadata occurrence" "$RUN_OUTPUT" "uv.lock:3:$public_version"
new_text_case "lock-artifact-neighbor" "sdist = { url = \"$public_artifact\", endpoint = \"$unsafe_ipv4\" }" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "lock-artifact-neighbor" "$ipv4_rule" "uv.lock:1"
assert_contains "artifact sanction preserves neighboring address" "$RUN_OUTPUT" "uv.lock:1:$unsafe_ipv4"
assert_not_contains "artifact sanction removes only basename occurrence" "$RUN_OUTPUT" "uv.lock:1:$public_version"
new_text_case "lock-artifact-wrong-host" "sdist = { url = \"https://example.com/click_plugins-${public_version}.tar.gz\" }" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "lock-artifact-wrong-host" "$ipv4_rule" "uv.lock:1"
new_text_case "lock-artifact-wrong-package" "sdist = { url = \"https://files.pythonhosted.org/packages/aa/bb/$digest_hex/other-${public_version}.tar.gz\" }" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "lock-artifact-wrong-package" "$ipv4_rule" "uv.lock:1"
new_text_case "lock-artifact-wrong-scheme" "sdist = { url = \"http://files.pythonhosted.org/packages/aa/bb/$digest_hex/click_plugins-${public_version}.tar.gz\" }" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "lock-artifact-wrong-scheme" "$ipv4_rule" "uv.lock:1"
new_text_case "lock-metadata-and-unsafe-line" "${benign_lock}
endpoint=$unsafe_ipv4" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "lock-metadata-and-unsafe-line" "$ipv4_rule" "uv.lock:7"
new_text_case "lowercase-image-digest" "image=registry.example.com/image:tag@sha256:$digest_hex" images.lock
run_gate "$repo" "$PATH" images.lock
expect_clean "lowercase-image-digest"

# Assignment sanctions require actual uv package identity and public registry ownership.
# These helpers change only fixture setup: expected diagnostic locations are declared by each case.
new_version_case() {
  local label="$1" content="$2" filename="${3:-uv.lock}"
  repo="$TEST_ROOT/$label"
  new_repo "$repo"
  mkdir -p "$repo/$(dirname "$filename")"
  printf '%s\n' "$content" >"$repo/$filename"
  git -C "$repo" add -- "$filename"
}

reject_public_version() {
  local label="$1" content="$2" filename="$3" line="$4"
  new_version_case "$label" "$content" "$filename"
  run_gate "$repo" "$PATH"
  expect_rejection "$label" "$ipv4_rule" "$filename:$line"
  assert_contains "$label retains the assignment token" "$RUN_OUTPUT" "$filename:$line:$public_version"
}

for filename in deployment.ini uv.lock apps/api/uv.lock; do
  label="package-bare-${filename//\//-}"
  reject_public_version "$label" "$package_version" "$filename" 1
  run_gate "$repo" "$PATH" "$filename"
  expect_rejection "$label explicit" "$ipv4_rule" "$filename:1"
  assert_contains "$label explicit retains the token" "$RUN_OUTPUT" "$filename:1:$public_version"
done

# A copied public stanza is insufficient in any filename outside the exact uv.lock component.
for filename in deployment.ini other.lock uv.lock.bak myuv.lock .uv.lock \
  scripts/tests/fixtures/uv-lock-provenance/uv.lock.pypdf-6.13.2 \
  scripts/tests/fixtures/uv-lock-provenance/uv.lock.pypdf-6.14.2; do
  reject_public_version "package-wrong-path-${filename//\//-}" "$qualified_package" "$filename" 3
done

for filename in uv.lock apps/api/uv.lock; do
  label="package-qualified-${filename//\//-}"
  new_version_case "$label" "$benign_lock" "$filename"
  run_gate "$repo" "$PATH"
  expect_clean "$label default"
  run_gate "$repo" "$PATH" "$filename"
  expect_clean "$label explicit"
  run_gate "$repo" "$fake_bin" "$filename"
  expect_clean "$label shell-only"
done

for wrong_name in unrelated-package Click-plugins click_plugins; do
  content="$(printf '[[package]]\nname = "%s"\n%s\n%s' "$wrong_name" "$package_version" "$package_source")"
  reject_public_version "package-wrong-name-$wrong_name" "$content" uv.lock 3
  if [ "$wrong_name" = unrelated-package ]; then
    run_gate "$repo" "$PATH" uv.lock
    expect_rejection "package-wrong-name explicit" "$ipv4_rule" "uv.lock:3"
    assert_contains "package-wrong-name explicit retains the token" "$RUN_OUTPUT" "uv.lock:3:$public_version"
  fi
done

reject_public_version "package-missing-header" "$(printf 'name = "click-plugins"\n%s\n%s' "$package_version" "$package_source")" uv.lock 2
reject_public_version "package-missing-name" "$(printf '[[package]]\n%s\n%s' "$package_version" "$package_source")" uv.lock 2
reject_public_version "package-name-comment" "$(printf '[[package]]\n# name = "click-plugins"\n%s\n%s' "$package_version" "$package_source")" uv.lock 3
reject_public_version "package-name-dependency-object" "$(printf '[[package]]\n{ name = "click-plugins" }\n%s\n%s' "$package_version" "$package_source")" uv.lock 3
reject_public_version "package-wrong-table" "$(printf '[tool.metadata]\nname = "click-plugins"\n%s\n%s' "$package_version" "$package_source")" uv.lock 3
reject_public_version "package-wrong-header-shape" "$(printf '[package]\nname = "click-plugins"\n%s\n%s' "$package_version" "$package_source")" uv.lock 3
reject_public_version "package-source-before-version" "$(printf '%s\n%s\n%s' "$package_head" "$package_source" "$package_version")" uv.lock 4
reject_public_version "package-source-before-name" "$(printf '[[package]]\n%s\nname = "click-plugins"\n%s' "$package_source" "$package_version")" uv.lock 4
reject_public_version "package-missing-source" "$(printf '%s\n%s' "$package_head" "$package_version")" uv.lock 3
reject_public_version "package-source-comment" "$(printf '%s\n%s\n# %s' "$package_head" "$package_version" "$package_source")" uv.lock 3

source_number=0
for source in \
  'source = { registry = "https://example.com/simple" }' \
  'source = { registry = "https://pypi.org/simple/" }' \
  'source = { editable = "." }' \
  'source = { path = "." }' \
  'source = { git = "https://example.com/package.git" }' \
  'source = { registry = "https://pypi.org/simple", path = "." }'; do
  source_number=$((source_number + 1))
  reject_public_version "package-unowned-source-$source_number" "$(printf '%s\n%s\n%s' "$package_head" "$package_version" "$source")" uv.lock 3
done

# Approval is deferred until the package closes; conflicting or repeated ownership is ambiguous.
for key in name version source; do
  case "$key" in
    name) duplicate='name = "unrelated-package"' ;;
    version) duplicate="$package_version" ;;
    source) duplicate='source = { editable = "." }' ;;
  esac
  reject_public_version "package-duplicate-$key" "$(printf '%s\n%s' "$qualified_package" "$duplicate")" uv.lock 3
  if [ "$key" = version ]; then
    assert_contains "duplicate version remains rejected on its own line" "$RUN_OUTPUT" "uv.lock:5:$public_version"
  fi
done
reject_public_version "package-duplicate-identical-name" "$(printf '%s\nname = "click-plugins"' "$qualified_package")" uv.lock 3
reject_public_version "package-duplicate-identical-source" "$(printf '%s\n%s' "$qualified_package" "$package_source")" uv.lock 3

# A bracket in package data is not a table boundary and cannot approve ownership prematurely.
matrix=$'matrix = [\n    ["a", "b"],\n]'
for key in name version source; do
  case "$key" in
    name) duplicate='name = "unrelated-package"' ;;
    version) duplicate="$package_version" ;;
    source) duplicate='source = { editable = "." }' ;;
  esac
  reject_public_version "package-array-before-duplicate-$key" "$(printf '%s\n%s\n%s' "$qualified_package" "$matrix" "$duplicate")" uv.lock 3
done
for row in true 1; do
  reject_public_version "package-array-closed-$row-before-duplicate-name" "$(printf '%s\nmatrix = [\n    [%s]\n]\nname = "unrelated-package"' "$qualified_package" "$row")" uv.lock 3
done
for key in matrix '"matrix"' matrix.rows; do
  case "$key" in
    matrix) label=bare ;;
    '"matrix"') label=quoted ;;
    matrix.rows) label=dotted ;;
  esac
  reject_public_version "package-array-partial-$label" "$(printf '%s\n%s = [[true],\n    [1]\n]\nname = "unrelated-package"' "$qualified_package" "$key")" uv.lock 3
done
reject_public_version "package-array-comment-fake-close" "$(printf '%s\nmatrix = [true # ]\n    [1]\n]\nname = "unrelated-package"' "$qualified_package")" uv.lock 3
reject_public_version "package-array-unclosed-eof" "$(printf '%s\nmatrix = [\n    { value = true },' "$qualified_package")" uv.lock 3
for key in name version source; do
  reject_public_version "package-duplicate-array-$key" "$(printf '%s\n%s = [\n]' "$qualified_package" "$key")" uv.lock 3
done
reject_public_version "package-malformed-open-bracket" "$(printf '%s\n[' "$qualified_package")" uv.lock 3
reject_public_version "package-malformed-bracket-before-duplicate" "$(printf '%s\n[\nname = "unrelated-package"' "$qualified_package")" uv.lock 3

for boundary in package tool subtable; do
  case "$boundary" in
    package)
      tail="$(printf '[[package]]\nname = "unrelated-package"\n%s\n%s' "$package_version" "$package_source")"
      version_line=7
      ;;
    tool) tail="$(printf '[tool.metadata]\n%s' "$package_version")"; version_line=6 ;;
    subtable) tail="$(printf '[package.optional-dependencies]\n%s' "$package_version")"; version_line=6 ;;
  esac
  reject_public_version "package-reset-$boundary" "$(printf '%s\n%s' "$qualified_package" "$tail")" uv.lock "$version_line"
  assert_not_contains "$boundary reset retains preceding legitimate metadata" "$RUN_OUTPUT" "uv.lock:3:$public_version"
done
reject_public_version "package-reset-missing-identity" "$(printf '%s\n[[package]]\n%s\n%s' "$qualified_package" "$package_version" "$package_source")" uv.lock 6
assert_not_contains "incomplete next package cannot spoil the preceding public entry" "$RUN_OUTPUT" "uv.lock:3:$public_version"

new_version_case "package-reset-file" "$qualified_package" a/uv.lock
mkdir -p "$repo/z"
printf '%s\n' "$package_version" >"$repo/z/uv.lock"
git -C "$repo" add -- z/uv.lock
run_gate "$repo" "$PATH"
expect_rejection "package-reset-file default" "$ipv4_rule" "z/uv.lock:1"
assert_contains "file reset retains the unqualified assignment" "$RUN_OUTPUT" "z/uv.lock:1:$public_version"
assert_not_contains "file reset preserves the qualified file" "$RUN_OUTPUT" "a/uv.lock:3:$public_version"
run_gate "$repo" "$PATH" z/uv.lock
expect_rejection "package-reset-file explicit" "$ipv4_rule" "z/uv.lock:1"
assert_contains "explicit file selection retains the unqualified assignment" "$RUN_OUTPUT" "z/uv.lock:1:$public_version"

quote_number=0
for quote in '"""' "'''"; do
  quote_number=$((quote_number + 1))
  reject_public_version "package-multiline-$quote_number" "$(printf 'example = %s\n%s\n%s' "$quote" "$qualified_package" "$quote")" uv.lock 4
done

# Repeating the same public token in a comment must produce one diagnostic, not zero or two.
new_version_case "package-comment-same-token" "$(printf '%s\n%s # endpoint=%s\n%s' "$package_head" "$package_version" "$public_version" "$package_source")" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "package-comment-same-token" "$ipv4_rule" "uv.lock:3"
public_hits=0
while IFS= read -r diagnostic; do
  if [ "$diagnostic" = "  uv.lock:3:$public_version" ]; then public_hits=$((public_hits + 1)); fi
done <<< "$RUN_OUTPUT"
assert_exit "same-token comment reports only the neighbor" 1 "$public_hits"

for context in endpoint host; do
  case "$context" in
    endpoint) tail="endpoint = \"$public_version\"" ;;
    host) tail="registry = \"https://$public_version/simple\"" ;;
  esac
  reject_public_version "package-body-$context" "$(printf '%s\n%s' "$qualified_package" "$tail")" uv.lock 5
  assert_not_contains "body $context retains legitimate version masking" "$RUN_OUTPUT" "uv.lock:3:$public_version"
done
new_version_case "package-wrong-version" "$(printf '%s\nversion = "%s"\n%s' "$package_head" "$unsafe_ipv4" "$package_source")" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "package-wrong-version" "$ipv4_rule" "uv.lock:3"
assert_contains "package name cannot approve a different token" "$RUN_OUTPUT" "uv.lock:3:$unsafe_ipv4"

whitespace_package="$(printf ' \t[[package]] # public package\n\n# identity\n\tname\t=\t"click-plugins" # public\n\tversion\t=\t"%s" # release\n\tsource\t=\t{ \tregistry\t=\t"https://pypi.org/simple" \t} # registry' "$public_version")"
new_version_case "package-positive-whitespace" "$whitespace_package" uv.lock
run_gate "$repo" "$PATH"
expect_clean "package-positive-whitespace"
run_gate "$repo" "$fake_bin" uv.lock
expect_clean "package-positive-whitespace shell-only"

other_package=$'[[package]]\nname = "another-package"\nversion = "2.0"\nsource = { registry = "https://pypi.org/simple" }'
new_version_case "package-positive-surrounded" "$(printf '%s\n%s\n%s' "$other_package" "$qualified_package" "$other_package")" uv.lock
run_gate "$repo" "$PATH"
expect_clean "package-positive-surrounded"
new_version_case "package-positive-no-final-newline" "$qualified_package" apps/api/uv.lock
printf '%s' "$qualified_package" >"$repo/apps/api/uv.lock"
git -C "$repo" add -- apps/api/uv.lock
run_gate "$repo" "$PATH"
expect_clean "package-positive-no-final-newline default"
run_gate "$repo" "$PATH" apps/api/uv.lock
expect_clean "package-positive-no-final-newline explicit"
multiline_public_lock="$(printf '%s\ndependencies = [\n    { name = "click" },\n]\nsdist = { url = "%s", hash = "sha256:%s" }\nwheels = [\n    { url = "%s", hash = "sha256:%s" },\n]' "$qualified_package" "$public_artifact" "$digest_hex" "$public_wheel" "$digest_hex")"
new_version_case "package-positive-multiline-artifacts" "$multiline_public_lock" apps/api/uv.lock
run_gate "$repo" "$PATH"
expect_clean "package-positive-multiline-artifacts default"
run_gate "$repo" "$PATH" apps/api/uv.lock
expect_clean "package-positive-multiline-artifacts explicit"
run_gate "$repo" "$fake_bin" apps/api/uv.lock
expect_clean "package-positive-multiline-artifacts shell-only"

# Case widening must retain the 16-byte minimum independently of MAC and IPv6 diagnostics.
legacy_lower="$(printf 'ab:%.0s' {1..15})ab"
legacy_upper="$(printf 'AB:%.0s' {1..15})AB"
legacy_mixed="$(printf 'aB:%.0s' {1..15})Ab"
case_number=0
for fingerprint in "$legacy_lower" "$legacy_upper" "$legacy_mixed"; do
  for prefix in '' 'MD5:'; do
    case_number=$((case_number + 1))
    label="legacy-fingerprint-$case_number"
    new_text_case "$label" "${prefix}${fingerprint}"
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$fingerprint_rule"
  done
done
legacy_short="$(printf 'AB:%.0s' {1..14})AB"
new_text_case "legacy-fingerprint-15-bytes" "$legacy_short"
run_gate "$repo" "$PATH"
assert_not_contains "15-byte sequence is not a key fingerprint" "$RUN_OUTPUT" "$fingerprint_rule"
new_text_case "legacy-sanctioned-mac" '00:15:5D:00:00:01'
run_gate "$repo" "$PATH"
expect_clean "legacy-sanctioned-mac"
unsafe_mac="$(printf '%s:%s:%s:%s:%s:%s' 'AB' 'CD' 'EF' '12' '34' '56')"
new_text_case "legacy-six-byte-mac" "$unsafe_mac"
run_gate "$repo" "$PATH"
expect_rejection "legacy-six-byte-mac" "MAC address"
assert_not_contains "six-byte MAC is not a key fingerprint" "$RUN_OUTPUT" "$fingerprint_rule"

# The leading-dash filename is valid on Windows. Its old interpretation excludes actual probe.txt.
option_filename='--exclude=probe.txt'
new_text_case "option-benign" 'portable fixture'
printf 'portable option fixture\n' >"$repo/$option_filename"
git -C "$repo" add -- "$option_filename"
run_gate "$repo" "$PATH"
expect_clean "option-benign default"
run_gate "$repo" "$PATH" "$option_filename"
expect_clean "option-benign explicit option filename"
run_gate "$repo" "$PATH" probe.txt
expect_clean "option-benign explicit ordinary filename"

option_rules=(linux windows workstation email dossier owner ipv4 ipv6 hostname mac legacy openssh)
for kind in "${option_rules[@]}"; do
  case "$kind" in
    linux) content="$(printf '/%s/%s' 'home' 'FixtureUser')"; rule='Personal Linux home path' ;;
    windows) content="$(printf 'C:/%s/%s/project' 'Users' 'FixtureUser')"; rule='Personal Windows profile path' ;;
    workstation) content="$(printf '%s %s' 'current' 'workstation')"; rule='Installation/workstation-specific prose' ;;
    email) content="$(printf '%s@%s.%s' 'operator' 'fixture' 'local')"; rule='Non-example email address' ;;
    dossier) content="$(printf '%s %s' 'this is a site-specific' 'deployment design')"; rule='Site-deployment dossier prose' ;;
    owner) content='shellOwner: ExampleOwner'; rule='Repository-owner token used as a personal identifier' ;;
    ipv4) content="$unsafe_ipv4"; rule="$ipv4_rule" ;;
    ipv6) content="$full_ipv6"; rule="$ipv6_rule" ;;
    hostname) content="$local_host"; rule='Non-example .local hostname' ;;
    mac) content="$unsafe_mac"; rule='MAC address' ;;
    legacy) content="$legacy_upper"; rule="$fingerprint_rule" ;;
    openssh) content="$(printf 'SHA%s:%043d' '256' 0)"; rule='OpenSSH SHA256 key fingerprint' ;;
  esac
  label="option-rule-$kind"
  new_text_case "$label" "$content"
  if [ "$kind" = owner ]; then
    git -C "$repo" remote add origin https://github.com/ExampleOwner/EasySynQ.git
  fi
  # First establish that the exact payload activates its existing rule before adding the option.
  run_gate "$repo" "$PATH"
  expect_rejection "$label baseline payload" "$rule"
  printf 'portable option fixture\n' >"$repo/$option_filename"
  git -C "$repo" add -- "$option_filename"
  run_gate "$repo" "$PATH"
  expect_rejection "$label option cannot suppress payload" "$rule"
  if [ "$kind" = owner ]; then
    assert_contains "option owner diagnostic remains redacted" "$RUN_OUTPUT" '<redacted-personal-identifier>'
    assert_not_contains "option owner diagnostic does not echo identity" "$RUN_OUTPUT" 'ExampleOwner'
  fi
done
new_text_case "option-payload-inside-filename" 'portable fixture'
printf 'endpoint=%s\n' "$unsafe_ipv4" >"$repo/$option_filename"
git -C "$repo" add -- "$option_filename"
run_gate "$repo" "$PATH"
expect_rejection "option payload default" "$ipv4_rule" "$option_filename:1"
run_gate "$repo" "$PATH" "$option_filename"
expect_rejection "option payload explicit" "$ipv4_rule" "$option_filename:1"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
