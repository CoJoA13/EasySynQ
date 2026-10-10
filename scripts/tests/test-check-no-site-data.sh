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
  'values[::2]' 'values[::+2]' 'values[::-2]' 'values [ :: 2 ]'
  'values[::abcd]' 'values[::2 + 1]' 'values[::f()]'
  'values[::(2 + 1)]' 'values[::f(2 + 1)]' 'values[::f((2 + 1))]'
  'values[::pkg.f(2, 1)]' 'values[::2 * 3 / 1 % 2]'
  'f()[::2]' 'values[0][::2]' 'object.values[::2]'
  'values[::2] + other[::f()]' 'values[0:5:2]'
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

new_text_case "slice-uri-neighbor" "values[::f()]; endpoint=http://[$leading_hex]:443"
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
  assert_not_contains "$label has no shortened IPv6 match" "$RUN_OUTPUT" "$ipv6_rule"
done

new_text_case "structural-ipv6-controls" 'bind=:: loopback=::1 documentation=2001:db8::1 canonical=0:0:0:0:0:0:0:1 time=12:30 ratio=2:1 mac=00:15:5D:00:00:01'
run_gate "$repo" "$PATH"
expect_clean "structural-ipv6-controls"
full_ipv6="$(printf '%s:%s:%s:%s:%s:%s:%s:%s' 'fdab' '1234' '5678' '9012' '3456' '7890' 'abcd' 'ef01')"
new_text_case "existing-full-ipv6" "$full_ipv6"
run_gate "$repo" "$PATH"
expect_rejection "existing-full-ipv6" "$ipv6_rule"

# One terminal prose period is punctuation; internal/repeated dots remain part of the candidate.
case_number=0
for suffix in '.' '. ' $'.\t'; do
  for prefix in '' 'values[::2 + 1]; ' 'values[::f()]; '; do
    case_number=$((case_number + 1))
    label="leading-sentence-period-$case_number"
    new_text_case "$label" "${prefix}${leading_hex}${suffix}"
    run_gate "$repo" "$PATH"
    expect_rejection "$label" "$ipv6_rule"
    assert_contains "$label keeps the complete token" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
    assert_not_contains "$label omits the prose period" "$RUN_OUTPUT" "probe.txt:1:${leading_hex}."
  done
done
new_text_case "sentence-two-neighbors" "values[::f((2 + 1))]; $leading_hex. $mapped_hex."
run_gate "$repo" "$PATH"
expect_rejection "sentence-two-neighbors" "$ipv6_rule"
assert_contains "sentence first neighbor survives" "$RUN_OUTPUT" "probe.txt:1:$leading_hex"
assert_contains "sentence second neighbor survives" "$RUN_OUTPUT" "probe.txt:1:$mapped_hex"

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
new_text_case "mapped-slice-and-neighbor" "values[::f()]; $mapped_doc. $mapped_unsafe."
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
new_text_case "leading-shell-only" "values[::f()]; $leading_hex."
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
new_text_case "lock-version-neighbor" "version = \"$public_version\" # endpoint=$unsafe_ipv4" uv.lock
run_gate "$repo" "$PATH"
expect_rejection "lock-version-neighbor" "$ipv4_rule" "uv.lock:1"
assert_contains "version sanction preserves neighboring address" "$RUN_OUTPUT" "uv.lock:1:$unsafe_ipv4"
assert_not_contains "version sanction removes only metadata occurrence" "$RUN_OUTPUT" "uv.lock:1:$public_version"
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
