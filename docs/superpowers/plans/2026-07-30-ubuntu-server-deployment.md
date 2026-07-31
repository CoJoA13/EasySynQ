# Ubuntu 26.04 Host Bootstrap + Windows-LAN Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision a bare Ubuntu 26.04 host to the state `scripts/install.sh` already assumes, and document the end-to-end deployment including the Windows-server side.

**Architecture:** A new `scripts/bootstrap-ubuntu.sh` provisions the **host** (packages, Docker CE, firewall, power, time, hostname, optional CIFS import mount). The existing `scripts/install.sh` continues to provision the **app** unchanged — it stays root-free because the appliance provisioner and the air-gapped installer both reuse it. A new operator runbook sequences both plus the Windows-side AD/DNS/GPO work.

**Tech Stack:** Bash (`set -euo pipefail`), `apt`, Docker CE `resolute` repo, `ufw`, `systemd`, `cifs-utils`. Markdown runbooks. No Python/Node/API/web/migration changes.

**Spec:** `docs/superpowers/specs/2026-07-30-ubuntu-server-deployment-design.md`

## Global Constraints

- **Docker Compose floor is `2.24.4`** — the production overlay's `!reset` merge tag requires it. Verify by delegating to the existing `scripts/require-compose-version.sh`; do not re-implement version parsing.
- **Use Docker's official repo** (`download.docker.com/linux/ubuntu`, suite from `VERSION_CODENAME` = `resolute` on 26.04). Ubuntu's `docker.io` / `docker-compose-v2` lag the floor. The dev runbook's `apt install docker.io` is **not** acceptable for production.
- **`bootstrap-ubuntu.sh` never calls `install.sh`** and never generates or writes secrets. `install.sh` remains the single origin of `.env`.
- **`bootstrap-ubuntu.sh` never edits `.env`.** The import mount uses the fixed root `/srv/easysynq/import`; the script *prints* the `IMPORT_SOURCE_PATH` value for the operator to set.
- **Every step is idempotent and skip-if-done** — a failed run resumes by re-running.
- **`ufw allow OpenSSH` must execute before `ufw --force enable`.** Reversed, this severs a remote install session.
- **The Docker suite is probed, never guessed** — `curl -fsI .../dists/<codename>/Release` must succeed before adding the repo.
- **Do not modify** `infra/appliance/build-appliance.sh` or the 24.04 references in `docs/runbooks/appliance-install.md` — an explicit spec non-goal (boot-proven pin for a path this deployment does not use).
- **`--dry-run` must be runnable on a non-Ubuntu dev box.** In dry-run, preflight *reports* rather than exits, so the test harness runs anywhere (this repo's dev box is Fedora 44).
- Shell style follows `scripts/install.sh`: `set -euo pipefail`, a `usage()` heredoc, `echo`-prefixed step output.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/bootstrap-ubuntu.sh` (create) | Host provisioner. Arg parsing, preflight, 10 idempotent steps, summary. |
| `scripts/tests/test-bootstrap-ubuntu.sh` (create) | Bash assertion harness over `--dry-run` output. Guards ordering + gating invariants. |
| `docs/runbooks/install-ubuntu-server.md` (create) | End-to-end runbook: values worksheet, Windows prep, bootstrap, install, CA/GPO, wizard, import, verify, troubleshoot. |
| `docs/runbooks/install-online.md` (modify) | Prereqs: pointer to the bootstrap script. |
| `docs/manuals/installation-guide.md` (modify) | §2 path table + §3 host prereqs: same pointer. |
| `docs/runbooks/00-index.md` (modify) | Index row for the new runbook. |

## Deploy-day fallback

The deploy is 2026-07-31 morning. Tasks 1–4 build the script; **Task 5 (the runbook) is the critical-path deliverable** — with it, the deploy can be done by hand even if the script is unfinished. If the clock forces a cut, execute Task 5 first, then re-verify its §2 against the final script before marking the plan done.

---

### Task 1: Script skeleton — args, preflight, dry-run harness, test harness

**Files:**
- Create: `scripts/bootstrap-ubuntu.sh`
- Create: `scripts/tests/test-bootstrap-ubuntu.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: the shell functions `run()`, `step()`, `skip_note()`, `die()`; the globals `HOST_NAME PROFILE QMS_SHARE QMS_USER SKIP_FIREWALL SKIP_UPGRADES FORCE DRY_RUN IMPORT_ROOT CRED_FILE CODENAME`. Tasks 2–4 append steps that call `run()` and `step()`. `IMPORT_ROOT` is the literal `/srv/easysynq/import`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test-bootstrap-ubuntu.sh`:

```bash
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

# line_index <haystack> <needle> -> first matching line number, or 0
line_index() {
  printf '%s\n' "$1" | grep -n -- "$2" | head -1 | cut -d: -f1
}

echo "== bootstrap-ubuntu.sh =="

OUT="$("$SCRIPT" --dry-run --host easysynq.corp.example 2>&1)"

assert_contains "docker suite is probed"        "$OUT" "dists/"
assert_contains "installs docker-ce"            "$OUT" "docker-ce"
assert_contains "prints the fixed import root"  "$OUT" "/srv/easysynq/import"

# THE lockout guard: SSH must be allowed before the firewall is enabled.
SSH_LINE="$(line_index "$OUT" 'ufw allow OpenSSH')"
ENABLE_LINE="$(line_index "$OUT" 'ufw --force enable')"
if [ -n "$SSH_LINE" ] && [ -n "$ENABLE_LINE" ] && [ "$SSH_LINE" -lt "$ENABLE_LINE" ]; then
  ok "ufw allows OpenSSH before enabling"
else
  bad "ufw allows OpenSSH before enabling (ssh=$SSH_LINE enable=$ENABLE_LINE)"
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

assert_exit "--host is required"          2
assert_exit "rejects an invalid profile"  2 --host easysynq.corp.example --profile xl
assert_exit "--qms-user needs --qms-share" 2 --host easysynq.corp.example --qms-user svc
assert_exit "--help exits 0"              0 --help

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
chmod +x scripts/tests/test-bootstrap-ubuntu.sh
./scripts/tests/test-bootstrap-ubuntu.sh
```

Expected: FAIL — every assertion errors because `scripts/bootstrap-ubuntu.sh` does not exist.

- [ ] **Step 3: Write the skeleton**

Create `scripts/bootstrap-ubuntu.sh`:

```bash
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

# Tasks 2-4 append their steps below this line.

step "Done"
printf '    (steps 1-10 land in tasks 2-4)\n'
```

- [ ] **Step 4: Run the test to verify the skeleton assertions pass**

```bash
chmod +x scripts/bootstrap-ubuntu.sh
./scripts/tests/test-bootstrap-ubuntu.sh
```

Expected: the four `assert_exit` cases and "prints the fixed import root" **PASS**; the docker/ufw/mount assertions still **FAIL** (those steps arrive in Tasks 2–4). That split is the point — it proves arg parsing and the dry-run harness work before any privileged logic exists.

- [ ] **Step 5: Commit**

```bash
git add scripts/bootstrap-ubuntu.sh scripts/tests/test-bootstrap-ubuntu.sh
git commit -m "feat(ops): bootstrap-ubuntu skeleton — args, preflight, dry-run harness"
```

---

### Task 2: Docker CE repo, packages, Compose floor, docker service

**Files:**
- Modify: `scripts/bootstrap-ubuntu.sh` (replace the `# Tasks 2-4 append their steps below this line.` marker)

**Interfaces:**
- Consumes: `run()`, `step()`, `skip_note()`, `die()`, `CODENAME`, `DRY_RUN` from Task 1.
- Produces: a provisioned Docker Engine + Compose plugin. Leaves the marker comment in place for Task 3.

- [ ] **Step 1: Replace the marker with steps 1–4**

Replace the line `# Tasks 2-4 append their steps below this line.` with:

```bash
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
```

- [ ] **Step 2: Run the test**

```bash
./scripts/tests/test-bootstrap-ubuntu.sh
```

Expected: "docker suite is probed" and "installs docker-ce" now **PASS**. ufw and mount assertions still fail.

- [ ] **Step 3: Eyeball the dry-run ordering**

```bash
./scripts/bootstrap-ubuntu.sh --dry-run --host easysynq.corp.example | head -40
```

Expected: preflight report, then base packages, then the suite probe **before** the `docker.list` write, then the Compose check.

- [ ] **Step 4: Commit**

```bash
git add scripts/bootstrap-ubuntu.sh
git commit -m "feat(ops): bootstrap-ubuntu — Docker CE repo, packages, Compose floor"
```

---

### Task 3: Power, time, hostname, firewall, unattended upgrades

**Files:**
- Modify: `scripts/bootstrap-ubuntu.sh` (replace the `# Tasks 3-4 append their steps below this line.` marker)

**Interfaces:**
- Consumes: `run()`, `step()`, `HOST_NAME`, `SKIP_FIREWALL`, `SKIP_UPGRADES` from Task 1.
- Produces: a hardened host. Leaves a marker for Task 4.

- [ ] **Step 1: Replace the marker with steps 5–8**

Replace `# Tasks 3-4 append their steps below this line.` with:

```bash
# ---------------------------------------------------------------- step 5: never sleep
# Repurposed workstation hardware suspends on an idle timer or a closed lid; either takes the QMS
# down outside hours with no obvious cause.
step "Disable sleep, suspend and hibernate"
run systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: set logind HandleLidSwitch*/HandleSuspendKey=ignore\n'
else
  install -d /etc/systemd/logind.conf.d
  cat > /etc/systemd/logind.conf.d/10-easysynq.conf <<'LOGIND'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchDocked=ignore
HandleLidSwitchExternalPower=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
LOGIND
  systemctl restart systemd-logind
fi

# ---------------------------------------------------------------- step 6: time + hostname
# Clock skew breaks OIDC token validation and misorders audit events.
step "Time sync and hostname"
run timedatectl set-ntp true
run hostnamectl set-hostname "$HOST_NAME"

HOST_SHORT="${HOST_NAME%%.*}"
if [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: ensure /etc/hosts has "127.0.1.1 %s %s"\n' "$HOST_NAME" "$HOST_SHORT"
elif grep -qE "^127\.0\.1\.1[[:space:]]+${HOST_NAME}([[:space:]]|$)" /etc/hosts; then
  skip_note "/etc/hosts entry"
else
  printf '127.0.1.1\t%s\t%s\n' "$HOST_NAME" "$HOST_SHORT" >> /etc/hosts
fi

# ---------------------------------------------------------------- step 7: firewall
step "Firewall"
if [ "$SKIP_FIREWALL" = "1" ]; then
  printf '    skipped (--skip-firewall)\n'
else
  # ORDER IS LOAD-BEARING: allow SSH before enabling, or a remote session is severed mid-install.
  run ufw allow OpenSSH
  run ufw allow 80/tcp     # ACME challenge / HTTPS redirect
  run ufw allow 443/tcp    # SPA, API, Keycloak
  run ufw allow 9443/tcp   # presigned object-store origin — omit and every file transfer fails
  run ufw --force enable
fi

# ---------------------------------------------------------------- step 8: security updates
step "Unattended security upgrades"
if [ "$SKIP_UPGRADES" = "1" ]; then
  printf '    skipped (--skip-upgrades)\n'
elif [ "$DRY_RUN" = "1" ]; then
  printf 'DRY-RUN: enable unattended-upgrades (security only, Automatic-Reboot false)\n'
else
  cat > /etc/apt/apt.conf.d/20easysynq-upgrades <<'UPG'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Allowed-Origins { "${distro_id}:${distro_codename}-security"; };
// Patching must never restart the QMS unannounced — reboot on your own schedule.
Unattended-Upgrade::Automatic-Reboot "false";
UPG
  systemctl enable --now unattended-upgrades
fi

# Task 4 appends its steps below this line.
```

- [ ] **Step 2: Run the test**

```bash
./scripts/tests/test-bootstrap-ubuntu.sh
```

Expected: the ufw ordering guard, "opens 9443", "opens 443", and "--skip-firewall suppresses ufw enable" now **PASS**. Only the two mount assertions still fail.

- [ ] **Step 3: Verify the ordering guard actually guards**

Temporarily move the `run ufw allow OpenSSH` line to *after* `run ufw --force enable`, re-run the harness, and confirm the "ufw allows OpenSSH before enabling" assertion **FAILS**. Then restore the correct order and confirm it passes again. This proves the test is mutation-distinguishing rather than trivially true.

- [ ] **Step 4: Commit**

```bash
git add scripts/bootstrap-ubuntu.sh
git commit -m "feat(ops): bootstrap-ubuntu — power, time, hostname, firewall, upgrades"
```

---

### Task 4: QMS share mount and summary

**Files:**
- Modify: `scripts/bootstrap-ubuntu.sh` (replace the `# Task 4 appends its steps below this line.` marker)

**Interfaces:**
- Consumes: `run()`, `step()`, `die()`, `QMS_SHARE`, `QMS_USER`, `IMPORT_ROOT`, `CRED_FILE`, `HOST_NAME`, `PROFILE`.
- Produces: the final script. No later task modifies it.

- [ ] **Step 1: Replace the marker with steps 9–10**

Replace `# Task 4 appends its steps below this line.` with:

```bash
# ---------------------------------------------------------------- step 9: QMS import mount
step "QMS import source"
if [ -z "$QMS_SHARE" ]; then
  printf '    no --qms-share given; skipping (mount it before setting IMPORT_SOURCE_PATH)\n'
else
  run install -d -m 0755 "$IMPORT_ROOT"

  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: write %s (0600, root) with the share credentials\n' "$CRED_FILE"
  elif [ -f "$CRED_FILE" ]; then
    skip_note "$CRED_FILE"
  else
    if [ -n "$QMS_USER" ]; then
      printf 'Password for %s (input hidden): ' "$QMS_USER" >&2
      read -rs QMS_PASS; printf '\n' >&2
      umask 077
      printf 'username=%s\npassword=%s\n' "$QMS_USER" "$QMS_PASS" > "$CRED_FILE"
      unset QMS_PASS
    else
      umask 077
      printf 'guest\n' > "$CRED_FILE"
    fi
    chmod 0600 "$CRED_FILE"
    chown root:root "$CRED_FILE"
  fi

  # ro is the whole point: the import engine reads the existing QMS tree and never writes it.
  # _netdev defers the mount until networking is up so a reboot does not strand it.
  FSTAB_LINE="${QMS_SHARE} ${IMPORT_ROOT} cifs ro,_netdev,vers=3.0,noserverino,credentials=${CRED_FILE},uid=0,gid=0 0 0"
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN: append to /etc/fstab <- %s\n' "$FSTAB_LINE"
    printf 'DRY-RUN: mount %s\n' "$IMPORT_ROOT"
  elif grep -qF " ${IMPORT_ROOT} cifs " /etc/fstab; then
    skip_note "/etc/fstab entry for ${IMPORT_ROOT}"
  else
    printf '%s\n' "$FSTAB_LINE" >> /etc/fstab
  fi

  if [ "$DRY_RUN" != "1" ]; then
    mountpoint -q "$IMPORT_ROOT" || mount "$IMPORT_ROOT" \
      || die "could not mount ${QMS_SHARE} at ${IMPORT_ROOT} — check the share path, the service account password, and that TCP 445 reaches the file server"
    ls "$IMPORT_ROOT" >/dev/null 2>&1 \
      || die "${IMPORT_ROOT} mounted but is not readable — check the share and NTFS permissions for ${QMS_USER:-guest}"
    printf '    mounted %s read-only at %s\n' "$QMS_SHARE" "$IMPORT_ROOT"
  fi
fi

# ---------------------------------------------------------------- step 10: summary
step "Host ready"
cat <<SUMMARY

  Host provisioned for profile '${PROFILE}' as ${HOST_NAME}.

  Next — provision the APP (this script deliberately does not):

    ./scripts/install.sh ${PROFILE} --host ${HOST_NAME} --tls internal

  Use --tls internal for a private/LAN name; --tls acme only if ${HOST_NAME} is publicly
  resolvable and reachable by a public CA.

SUMMARY

if [ -n "$QMS_SHARE" ]; then
  cat <<IMPORTNOTE
  After install.sh completes, point the import engine at the mount and recreate the two
  containers that bind it:

    sed -i 's|^IMPORT_SOURCE_PATH=.*|IMPORT_SOURCE_PATH=${IMPORT_ROOT}|' .env
    docker compose --env-file .env \\
      -f infra/compose/compose.yml -f infra/compose/compose.${PROFILE}.yml \\
      -f infra/compose/compose.production.yml up -d api worker

  The mount already exists, so the bind resolves on recreate. (A bind mount never sees a
  filesystem mounted over its source after the container has started.)

IMPORTNOTE
fi

if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER:-}" != "root" ]; then
  printf '  Log out and back in first so the docker group applies to %s.\n\n' "$SUDO_USER"
fi
```

Then delete the now-redundant placeholder tail from Task 1:

```bash
step "Done"
printf '    (steps 1-10 land in tasks 2-4)\n'
```

- [ ] **Step 2: Run the test — all assertions must pass**

```bash
./scripts/tests/test-bootstrap-ubuntu.sh
```

Expected: `16 passed, 0 failed`.

- [ ] **Step 3: Full dry-run read-through**

```bash
./scripts/bootstrap-ubuntu.sh --dry-run --host easysynq.corp.example \
  --qms-share //FILESRV/QMS --qms-user svc-easysynq-ro
```

Expected: all ten steps in order, ending with the `install.sh` command and the `IMPORT_SOURCE_PATH` note. Confirm no secret is printed and no `.env` write appears anywhere.

- [ ] **Step 4: Commit**

```bash
git add scripts/bootstrap-ubuntu.sh
git commit -m "feat(ops): bootstrap-ubuntu — read-only QMS mount and operator summary"
```

---

### Task 5: The deployment runbook

**Files:**
- Create: `docs/runbooks/install-ubuntu-server.md`

**Interfaces:**
- Consumes: the finished `scripts/bootstrap-ubuntu.sh` CLI from Tasks 1–4.
- Produces: the operator-facing procedure. Task 6 links to it.

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/install-ubuntu-server.md` with these sections, in this order. Every command must be copy-paste runnable and reference the §0 worksheet names rather than inline placeholders.

**Title + audience note.** Ubuntu host serving a Windows LAN; points to `install-online.md` for browser-edge detail rather than duplicating it.

**§0 Values worksheet.** The fill-once table from the spec, with the discovery command for each row:

| Name | Value | Discover it with |
|---|---|---|
| AD DNS zone | | `Get-DnsServerZone \| Where-Object { -not $_.IsReverseLookupZone }` |
| App FQDN | | your choice, e.g. `easysynq.<zone>` |
| Ubuntu host IP | | `ip -4 addr show scope global` (on the Ubuntu box) |
| QMS share UNC | | `Get-SmbShare \| Where-Object Name -notlike '*$'` |
| Share local path | | same output, `Path` column |
| Service account | | your choice, e.g. `svc-easysynq-ro` |
| NetBIOS domain | | `(Get-ADDomain).NetBIOSName` |

**§1 Windows server prep.** Four subsections, each prose + one PowerShell block:

1. *DNS A record* — `Add-DnsServerResourceRecordA -Name <short> -ZoneName <zone> -IPv4Address <ip> -CreatePtr`, then verify **from a workstation, not the DC**: `Resolve-DnsName <fqdn>`.
2. *Service account* — `New-ADUser -Name <svc> -SamAccountName <svc> -AccountPassword (Read-Host -AsSecureString "Password") -PasswordNeverExpires $true -CannotChangePassword $true -Enabled $true`. Note it needs no interactive logon rights.
3. *Share + NTFS* — `Grant-SmbShareAccess -Name <share> -AccountName "<NETBIOS>\<svc>" -AccessRight Read -Force`, then the NTFS ACE:
   ```powershell
   $acl = Get-Acl <sharepath>
   $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
     "<NETBIOS>\<svc>","ReadAndExecute","ContainerInherit,ObjectInherit","None","Allow")
   $acl.AddAccessRule($rule); Set-Acl <sharepath> $acl
   ```
   State explicitly that **both layers are required** — share-level Read still defers to NTFS, and the most common failure is granting one and not the other.
4. *Confirm reachability* — TCP 445 from the Ubuntu host's IP to the file server.

**§2 Bootstrap the Ubuntu host.** The `sudo ./scripts/bootstrap-ubuntu.sh` invocation with the worksheet values, including `--qms-share`/`--qms-user`. Note the log-out/in for the docker group.

**§3 Install EasySynQ.** `git clone` at a release tag, then `./scripts/install.sh s --host <fqdn> --tls internal`. Carry the spec's escrow warning verbatim: **`BACKUP_ENCRYPTION_KEY` lives only in the `0600` `.env` — copy it to a password manager before the first backup, or every encrypted backup is unrecoverable.**

**§4 CA export + GPO.** The `docker compose ... exec -T proxy cat /data/caddy/pki/authorities/local/root.crt` block from `install-online.md` (with the three `-f` overlay flags), the GPO GUI path, `certutil -dspublish -f easysynq-root-ca.crt RootCA` as the enterprise-store alternative, and workstation verification:
```powershell
gpupdate /force
Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like '*Caddy*'
```

**§5 Setup wizard.** `./scripts/easysynq setup mint-bootstrap`, then the five gates in order (organization → storage WORM verify G-B → backup + restore drill G-C → authentication G-D → finalize). Flag two items here: set `OPS_ALERT_CHANNELS` before go-live (an in-app-only backup-failure notice needs the database that may be what failed), and the R13 non-blocking **NOT tamper-evident** warning as a named follow-up.

**§6 Point the import at the mount.** The `sed` + `docker compose up -d api worker` block from the script's summary, with the bind-mount explanation.

**§7 Verification checklist.** The spec's command list (`docker compose version`, `systemctl is-enabled docker`, `systemctl is-masked sleep.target`, `ufw status`, `timedatectl`, `hostname -f`, `findmnt /srv/easysynq/import`) plus the release-time security check from `install-online.md` (CSP/HSTS headers; TLS 1.1 refused).

**§8 Troubleshooting.** A symptom → check table covering, at minimum: app loads but uploads fail (9443 blocked) · sign-in loops (issuer ≠ browser URL) · certificate warning persists (GPO not applied / wrong store) · import finds nothing (mount made after container start, or `IMPORT_SOURCE_PATH` unset) · `docker` permission denied (docker group needs a re-login).

- [ ] **Step 2: Verify every command against the source of truth**

For each command block, confirm it matches the repo — the overlay `-f` flags against `install-online.md`, the wizard gate names against `docs/manuals/installation-guide.md`, the script flags against `scripts/bootstrap-ubuntu.sh --help`. Fix any drift in the runbook, not in the script.

- [ ] **Step 3: Confirm no placeholder leaked into a runnable command**

```bash
grep -nE 'corp\.example|<fqdn>|<zone>|FILESRV|TODO|TBD' docs/runbooks/install-ubuntu-server.md
```

Expected: hits only inside the §0 worksheet, prose, or clearly-marked example text — never in a command a reader would paste unmodified without first filling the worksheet.

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/install-ubuntu-server.md
git commit -m "docs(runbook): Ubuntu 26.04 server install on a Windows LAN"
```

---

### Task 6: Wire the new runbook into the existing docs

**Files:**
- Modify: `docs/runbooks/00-index.md`
- Modify: `docs/runbooks/install-online.md`
- Modify: `docs/manuals/installation-guide.md`

**Interfaces:**
- Consumes: `docs/runbooks/install-ubuntu-server.md` from Task 5.
- Produces: nothing downstream.

- [ ] **Step 1: Add the index row**

In `docs/runbooks/00-index.md`, insert **above** the `install-online.md` row (it is the more specific entry point):

```markdown
| [install-ubuntu-server.md](install-ubuntu-server.md) | End-to-end first install on a bare Ubuntu 26.04 host serving a Windows LAN — host bootstrap, AD DNS + service account, GPO CA trust. |
```

- [ ] **Step 2: Add the prereq pointer in install-online.md**

In `docs/runbooks/install-online.md`, immediately after the opening paragraph that states the Compose 2.24.4 minimum, add:

```markdown
> On a host that is not yet provisioned (no Docker, firewall, or NTP), run
> [`scripts/bootstrap-ubuntu.sh`](../../scripts/bootstrap-ubuntu.sh) first — see
> [install-ubuntu-server.md](install-ubuntu-server.md). This runbook assumes those prerequisites
> are already met.
```

- [ ] **Step 3: Update the installation guide**

In `docs/manuals/installation-guide.md` §2, add a row to the path table:

```markdown
| Bare Ubuntu host on a Windows LAN | Follow [install Ubuntu server](../runbooks/install-ubuntu-server.md) — bootstraps the host, then §4. |
```

And in §3 under **Host**, after the Docker Compose bullet, add:

```markdown
- On an unprovisioned Ubuntu host, `sudo ./scripts/bootstrap-ubuntu.sh --host <fqdn>` installs and
  configures all of the above.
```

- [ ] **Step 4: Verify every new link resolves**

```bash
for f in docs/runbooks/install-ubuntu-server.md scripts/bootstrap-ubuntu.sh; do
  test -e "$f" && echo "ok $f" || echo "MISSING $f"
done
grep -n "install-ubuntu-server" docs/runbooks/00-index.md docs/runbooks/install-online.md docs/manuals/installation-guide.md
```

Expected: both `ok`, and one hit in each of the three files.

- [ ] **Step 5: Final full-plan verification**

```bash
./scripts/tests/test-bootstrap-ubuntu.sh
./scripts/bootstrap-ubuntu.sh --help
git status --short
```

Expected: all assertions pass, help renders, working tree clean apart from intended changes.

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/00-index.md docs/runbooks/install-online.md docs/manuals/installation-guide.md
git commit -m "docs: wire install-ubuntu-server into the runbook index and install paths"
```

---

## Self-Review

**Spec coverage.** Component 1 (`bootstrap-ubuntu.sh`) → Tasks 1–4, all ten steps mapped: preflight T1; packages/Docker/Compose/service T2 steps 1–4; power/time/hostname/firewall/upgrades T3 steps 5–8; mount/summary T4 steps 9–10. Component 2 (runbook) → Task 5. Component 3 (Windows) → Task 5 §1 and §4. Component 4 (doc edits) → Task 6. Non-goal (appliance 24.04 pin) → Global Constraints, "do not modify". Verification checklist → Task 5 §7. All seven spec risks appear: 9443 (T3 step 7 + T5 §7/§8), bare IP (T1 `validate-dns-name.sh` + T5 §8), ufw lockout (T3 comment + T1 test guard), mount ordering (T4 summary + T5 §6/§8), key escrow (T5 §3), ops channel (T5 §5), R13 (T5 §5).

**Placeholder scan.** No TBD/TODO. Every code step carries real, complete code. Task 5 is specified section-by-section with the exact content and command shape for each rather than "write the docs" — the one judgement call left to the implementer is prose wording, which is appropriate for a runbook.

**Type consistency.** `run()`, `step()`, `skip_note()`, `die()`, `preflight_fail()` are defined in Task 1 and used with matching arity throughout. `IMPORT_ROOT` (`/srv/easysynq/import`) and `CRED_FILE` (`/etc/easysynq-qms.cred`) are set once in Task 1 and referenced in Task 4 and Task 5 §6/§7. `CODENAME` is set in Task 1 preflight, consumed in Task 2 as `PROBE_CODENAME`. Flag names match between `usage()`, the parser, the test harness, and the runbook: `--host --profile --qms-share --qms-user --skip-firewall --skip-upgrades --force --dry-run`.

**One gap found and fixed inline:** Task 1's skeleton ends with a placeholder `step "Done"` tail that would otherwise survive into the final script — Task 4 Step 1 now explicitly deletes it.
