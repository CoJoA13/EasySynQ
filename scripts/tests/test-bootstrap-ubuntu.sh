#!/usr/bin/env bash
# Assertion harness for scripts/bootstrap-ubuntu.sh, driven entirely through --dry-run so it runs
# on any host (this repo's dev box is Fedora; the script targets Ubuntu).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/bootstrap-ubuntu.sh"
PASS=0
FAIL=0

ok()   { printf '  ok   %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

# assert_contains <label> <haystack> <needle>
assert_contains() {
  case "$2" in *"$3"*) ok "$1" ;; *) bad "$1 (missing: $3)" ;; esac
}

# assert_not_contains <label> <haystack> <needle>
assert_not_contains() {
  case "$2" in *"$3"*) bad "$1 (unexpectedly present: $3)" ;; *) ok "$1" ;; esac
}

# assert_exit <label> <expected-code> <args...>
assert_exit() {
  local label="$1" want="$2"; shift 2
  local got=0
  "$SCRIPT" "$@" >/dev/null 2>&1 || got=$?
  [ "$got" = "$want" ] && ok "$label" || bad "$label (want exit $want, got $got)"
}

# line_index <haystack> <needle> -> first matching line number, or empty
line_index() {
  printf '%s\n' "$1" | grep -n -- "$2" | head -1 | cut -d: -f1
}

echo "== bootstrap-ubuntu.sh =="

# Pin the Docker-detection inputs so results never depend on whether THIS machine has Docker.
# Without this the skip-if-done branch fires on any host that already has dockerd + the keyring,
# omitting the probe/install output and failing these assertions on a perfectly healthy script.
export EASYSYNQ_DOCKERD_BIN=/nonexistent/dockerd
export EASYSYNQ_DOCKER_KEYRING=/nonexistent/docker.asc

OUT="$("$SCRIPT" --dry-run --host easysynq.corp.example 2>&1)"

assert_contains "docker suite is probed"        "$OUT" "dists/"
assert_contains "installs docker-ce"            "$OUT" "docker-ce"
assert_contains "prints the fixed import root"  "$OUT" "/srv/easysynq/import"

# The other branch: with both present, the script must SKIP the repo work rather than re-add it.
SKIPDOCKER="$(EASYSYNQ_DOCKERD_BIN="$(command -v sh)" EASYSYNQ_DOCKER_KEYRING="$SCRIPT" \
  "$SCRIPT" --dry-run --host easysynq.corp.example 2>&1)"
assert_contains     "skips docker install when already present" "$SKIPDOCKER" "already done: docker"
assert_not_contains "skip branch does not re-add the repo"      "$SKIPDOCKER" "sources.list.d/docker.list"

# THE lockout guard: SSH must be allowed before the firewall is enabled.
SSH_LINE="$(line_index "$OUT" 'ufw allow OpenSSH')"
ENABLE_LINE="$(line_index "$OUT" 'ufw --force enable')"
if [ -n "$SSH_LINE" ] && [ -n "$ENABLE_LINE" ] && [ "$SSH_LINE" -lt "$ENABLE_LINE" ]; then
  ok "ufw allows OpenSSH before enabling"
else
  bad "ufw allows OpenSSH before enabling (ssh=${SSH_LINE:-none} enable=${ENABLE_LINE:-none})"
fi

assert_contains "opens 9443"  "$OUT" "9443"
assert_contains "opens 443"   "$OUT" "443"

SKIPPED="$("$SCRIPT" --dry-run --host easysynq.corp.example --skip-firewall 2>&1)"
assert_not_contains "--skip-firewall suppresses ufw enable" "$SKIPPED" "ufw --force enable"

NOSHARE="$("$SCRIPT" --dry-run --host easysynq.corp.example 2>&1)"
assert_not_contains "no mount without --qms-share" "$NOSHARE" "/etc/fstab"

SHARE="$("$SCRIPT" --dry-run --host easysynq.corp.example \
  --qms-share //FILESRV/QMS --qms-user svc-easysynq-ro 2>&1)"
assert_contains "mounts when --qms-share given" "$SHARE" "/etc/fstab"
assert_contains "mount is read-only"            "$SHARE" "ro,"

# Codex #4 (P1): _netdev orders the mount vs the network, NOT docker vs the mount. Without this
# drop-in, a reboot can start the worker against the still-empty bind source for the whole boot.
assert_contains "orders docker after the import mount" "$SHARE" "RequiresMountsFor=/srv/easysynq/import"

# Codex #2: anonymous access is a mount OPTION; a bare "guest" line is not a credentials-file field.
GUEST="$("$SCRIPT" --dry-run --host easysynq.corp.example --qms-share //FILESRV/QMS 2>&1)"
assert_contains     "guest mount uses the guest option" "$GUEST" "guest"
assert_not_contains "guest mount writes no creds file"  "$GUEST" "credentials="

# Codex #5: fstab fields are whitespace-delimited; Windows share names may contain spaces.
SPACED="$("$SCRIPT" --dry-run --host easysynq.corp.example \
  --qms-share '//FILESRV/Quality Documents' --qms-user svc 2>&1)"
assert_contains     "encodes spaces in the UNC as \\040" "$SPACED" 'Quality\040Documents'
assert_not_contains "no raw space survives into fstab"   "$SPACED" "//FILESRV/Quality Documents /srv"

assert_exit "--host is required"           2
assert_exit "rejects an invalid profile"   2 --host easysynq.corp.example --profile xl
assert_exit "--qms-user needs --qms-share" 2 --host easysynq.corp.example --qms-user svc
assert_exit "--help exits 0"               0 --help

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
