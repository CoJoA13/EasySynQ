#!/usr/bin/env bash
# EasySynQ production host bootstrap (Ubuntu).
#
# Provisions the HOST; scripts/install.sh provisions the APP. Run with sudo BEFORE install.sh.
# This script deliberately generates NO secrets and never edits .env — install.sh stays the single
# origin of .env so there is exactly one artifact to escrow.
#
# Every step is idempotent and skip-if-done: a failed run resumes by re-running.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

HOST_NAME=""
PROFILE="s"
QMS_SHARE=""
QMS_USER=""
SKIP_FIREWALL=0
SKIP_UPGRADES=0
FORCE=0
DRY_RUN=0

# Fixed, mirroring the appliance: the mount never rewrites .env, so the operator sets
# IMPORT_SOURCE_PATH to this value by hand (printed in the summary).
IMPORT_ROOT="/srv/easysynq/import"
CRED_FILE="/etc/easysynq-qms.cred"

usage() {
  cat >&2 <<'EOF'
usage: sudo ./scripts/bootstrap-ubuntu.sh --host <fqdn> [options]

  --host <fqdn>          Static FQDN; must match the AD DNS A record (required)
  --profile s|m          Sizing floor for preflight (default: s)
  --qms-share <unc>      e.g. //FILESRV/QMS — mount the import source read-only
  --qms-user <account>   Service account for that share (requires --qms-share)
  --skip-firewall        Leave ufw untouched (a managed/external firewall)
  --skip-upgrades        Do not enable unattended-upgrades
  --force                Proceed despite a failed sizing preflight
  --dry-run              Print every command; execute nothing
  -h, --help             This message

Run this BEFORE scripts/install.sh. It does not call install.sh.
EOF
}

die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
skip_note() { printf '    (already done: %s)\n' "$*"; }

# run <cmd...> — execute, or print under --dry-run.
run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: %s\n' "$*"
  else
    "$@"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)    [ $# -ge 2 ] || { usage; exit 2; }; HOST_NAME="$2"; shift 2 ;;
    --profile) [ $# -ge 2 ] || { usage; exit 2; }; PROFILE="$2"; shift 2 ;;
    --qms-share) [ $# -ge 2 ] || { usage; exit 2; }; QMS_SHARE="$2"; shift 2 ;;
    --qms-user)  [ $# -ge 2 ] || { usage; exit 2; }; QMS_USER="$2"; shift 2 ;;
    --skip-firewall) SKIP_FIREWALL=1; shift ;;
    --skip-upgrades) SKIP_UPGRADES=1; shift ;;
    --force)   FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[ -n "$HOST_NAME" ] || { usage; exit 2; }
case "$PROFILE" in s|m) ;; *) usage; exit 2 ;; esac
# A credential-less share is a valid anonymous mount, but a user without a share is a typo.
[ -z "$QMS_USER" ] || [ -n "$QMS_SHARE" ] || { usage; exit 2; }

if ! bash "$ROOT/scripts/validate-dns-name.sh" "$HOST_NAME"; then
  die "--host must be a valid DNS name without a scheme, path, or port"
fi

# ---------------------------------------------------------------- step 0: preflight
step "Preflight"

case "$PROFILE" in
  s) MIN_CPU=2; MIN_MEM=8;  MIN_DISK=50 ;;
  m) MIN_CPU=4; MIN_MEM=16; MIN_DISK=200 ;;
esac

# In --dry-run the environment gates REPORT instead of exiting, so the harness runs on any distro.
preflight_fail() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '    would fail: %s\n' "$*"
  elif [ "$FORCE" = "1" ]; then
    printf '    IGNORED (--force): %s\n' "$*"
  else
    die "$* (override with --force)"
  fi
}

[ "$DRY_RUN" = "1" ] || [ "$(id -u)" = "0" ] || die "must run as root (use sudo)"

OS_ID=""; OS_VER=""; CODENAME=""
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  OS_ID="$(. /etc/os-release && printf '%s' "${ID:-}")"
  OS_VER="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
  CODENAME="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
fi
[ "$OS_ID" = "ubuntu" ] || preflight_fail "this script targets Ubuntu (found ID='${OS_ID:-unknown}')"
if [ -n "$OS_VER" ] && [ "$OS_VER" != "26.04" ]; then
  printf '    warning: tested on Ubuntu 26.04; found %s — continuing\n' "$OS_VER"
fi
[ "$(uname -m)" = "x86_64" ] || preflight_fail "x86_64 required (found $(uname -m))"

CPU_COUNT="$(nproc 2>/dev/null || echo 0)"
MEM_GB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0) / 1024 / 1024 ))
DISK_GB="$(df -BG --output=size / 2>/dev/null | tail -1 | tr -dc '0-9')"
DISK_GB="${DISK_GB:-0}"

printf '    profile=%s  cpu=%s (min %s)  mem=%sG (min %sG)  disk=%sG (min %sG)\n' \
  "$PROFILE" "$CPU_COUNT" "$MIN_CPU" "$MEM_GB" "$MIN_MEM" "$DISK_GB" "$MIN_DISK"

[ "$CPU_COUNT" -ge "$MIN_CPU" ]  || preflight_fail "profile ${PROFILE} needs >= ${MIN_CPU} vCPU"
[ "$MEM_GB"    -ge "$MIN_MEM" ]  || preflight_fail "profile ${PROFILE} needs >= ${MIN_MEM} GB RAM"
[ "$DISK_GB"   -ge "$MIN_DISK" ] || preflight_fail "profile ${PROFILE} needs >= ${MIN_DISK} GB disk on /"

# ---------------------------------------------------------------- step 1: base packages
step "Base packages"
run apt-get update -qq
run apt-get install -y -qq \
  ca-certificates curl git openssl gnupg ufw cifs-utils unattended-upgrades

# ---------------------------------------------------------------- step 2: Docker CE
step "Docker CE repository"
if [ -x /usr/bin/dockerd ] && [ -r /etc/apt/keyrings/docker.asc ]; then
  skip_note "docker engine + keyring present"
else
  # Probe the suite before trusting it. Silently falling back to another Ubuntu series would
  # install a mismatched build that fails much later and far less legibly.
  PROBE_CODENAME="${CODENAME:-unknown}"
  printf '    probing https://download.docker.com/linux/ubuntu/dists/%s/Release\n' "$PROBE_CODENAME"
  if [ "$DRY_RUN" != "1" ]; then
    curl -fsI "https://download.docker.com/linux/ubuntu/dists/${PROBE_CODENAME}/Release" >/dev/null \
      || die "Docker publishes no '${PROBE_CODENAME}' suite yet.
       Check https://download.docker.com/linux/ubuntu/dists/ and either wait for the suite or
       install Docker by another supported means. Do NOT substitute a different codename."
  fi

  run install -m 0755 -d /etc/apt/keyrings
  run curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  run chmod a+r /etc/apt/keyrings/docker.asc

  DOCKER_LIST="deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${PROBE_CODENAME} stable"
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: write /etc/apt/sources.list.d/docker.list <- %s\n' "$DOCKER_LIST"
  else
    printf '%s\n' "$DOCKER_LIST" > /etc/apt/sources.list.d/docker.list
  fi

  run apt-get update -qq
  run apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# ---------------------------------------------------------------- step 3: Compose floor
step "Docker Compose version floor"
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: bash %s/scripts/require-compose-version.sh\n' "$ROOT"
else
  bash "$ROOT/scripts/require-compose-version.sh" \
    || die "Compose floor not met — see the message above"
fi

# ---------------------------------------------------------------- step 4: service + group
step "Docker service and group"
run systemctl enable --now docker
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER:-}" != "root" ]; then
  run usermod -aG docker "$SUDO_USER"
  printf '    %s added to the docker group — log out and back in for it to take effect\n' "$SUDO_USER"
else
  printf '    no SUDO_USER to add to the docker group (running as root directly)\n'
fi

# Tasks 3-4 append their steps below this line.

step "Done"
printf '    (steps 1-10 land in tasks 2-4)\n'
