#!/usr/bin/env bash
# EasySynQ appliance provisioner. Two entrypoints:
#   easysynq-provision bootstrap   (cloud-init, once) install helpers + the systemd unit, then run
#   easysynq-provision run         (systemd oneshot)  the idempotent provision itself
#
# Every step is skip-if-done, so `systemctl restart easysynq-provision` safely resumes a failed or
# interrupted provision (e.g. a network blip during image pulls). Progress + failures land on the
# Hyper-V console (tty1) and in `journalctl -u easysynq-provision`.
# -E: the ERR trap must fire inside functions/subshells too (without it the FAILED banner is dead code).
set -Eeuo pipefail

SEED_MNT=/run/easysynq-seed
APP_DIR=/opt/easysynq
STATE_DIR=/var/lib/easysynq
SETUP_FILE=/home/easysynq/EASYSYNQ-SETUP.txt
HOSTNAME_DEFAULT=easysynq.local

log() { echo "easysynq-provision: $*"; }
# Banner to the Hyper-V console (tty1) AND the serial console (ttyS0 — QEMU boot tests + any
# serial-attached hypervisor viewer); each best-effort.
console() {
  echo -e "$*" >/dev/tty1 2>/dev/null || true
  echo -e "$*" >/dev/ttyS0 2>/dev/null || true
}

fail_banner() {
  console "\n[EasySynQ] PROVISIONING FAILED at: ${CURRENT_STEP:-unknown}\n  Inspect:  journalctl -u easysynq-provision -e\n  Retry:    sudo systemctl restart easysynq-provision\n"
}
trap fail_banner ERR

step() { CURRENT_STEP="$1"; log "== $1"; }

compose() {
  docker compose --env-file "$APP_DIR/.env" \
    -f "$APP_DIR/infra/compose/compose.yml" \
    -f "$APP_DIR/infra/compose/compose.s.yml" \
    -f "$APP_DIR/infra/compose/compose.airgap.yml" \
    -f "$APP_DIR/infra/compose/compose.appliance.yml" "$@"
}

# set_kv KEY VALUE — update-or-append in .env (mirrors scripts/install.sh).
set_kv() {
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$APP_DIR/.env"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$APP_DIR/.env"
  else
    printf '%s=%s\n' "$key" "$val" >>"$APP_DIR/.env"
  fi
}

mount_seed() {
  mountpoint -q "$SEED_MNT" && return 0
  mkdir -p "$SEED_MNT"
  mount -o ro /dev/disk/by-label/cidata "$SEED_MNT"
}

cmd_bootstrap() {
  step "bootstrap: install helpers + systemd unit"
  install -d -m 0755 /usr/local/bin
  for h in /opt/provision/bin/*; do
    install -m 0755 "$h" "/usr/local/bin/$(basename "$h")"
  done
  cat >/etc/systemd/system/easysynq-provision.service <<'UNIT'
[Unit]
Description=EasySynQ appliance provision (idempotent; restart to resume)
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
# No deadline: a slow first build/pull must never be SIGTERM'd mid-flight (oneshot cannot
# auto-retry). Progress is observable via journalctl; every step resumes on manual restart.
TimeoutStartSec=infinity
ExecStart=/usr/local/sbin/easysynq-provision run

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable easysynq-provision.service
  console "\n[EasySynQ] Provisioning started — this takes 10–25 minutes on first boot.\n  Watch:  journalctl -fu easysynq-provision\n"
  systemctl start easysynq-provision.service
}

cmd_run() {
  if [ -f "$STATE_DIR/provisioned" ]; then
    log "already provisioned — nothing to do"
    return 0
  fi
  mkdir -p "$STATE_DIR"

  step "seed: mount + extract repo bundle"
  mount_seed
  # Atomic extract: a crash mid-tar must not leave a half-tree that the resume guard then skips.
  if [ ! -f "$STATE_DIR/repo-extracted" ]; then
    rm -rf "$APP_DIR.tmp"
    mkdir -p "$APP_DIR.tmp"
    tar -C "$APP_DIR.tmp" -xzf "$SEED_MNT/easysynq-repo.tar.gz"
    rm -rf "$APP_DIR"
    mv "$APP_DIR.tmp" "$APP_DIR"
    cp "$SEED_MNT/version.txt" "$STATE_DIR/version.txt" 2>/dev/null || true
    touch "$STATE_DIR/repo-extracted"
  fi

  step "docker: group + import mount point"
  usermod -aG docker easysynq || true
  mkdir -p /srv/easysynq/import

  step "env: generate secrets"
  if [ ! -f "$APP_DIR/.env" ]; then
    (cd "$APP_DIR" && EASYSYNQ_ENV_ONLY=1 bash scripts/install.sh s)
  fi
  # A crash mid-generation leaves a .env full of template CHANGE_ME values that the bare
  # file-exists guard would then keep forever — fail closed and regenerate on the next retry.
  # Check ONLY the keys install.sh generates: AUDIT_SINK_* keeps its placeholder by design
  # (the off-host checkpoint sink is operator-configured later — the wizard's soft gate).
  if grep -E '^(POSTGRES_PASSWORD|DATABASE_URL|DATABASE_URL_SYNC|AUDIT_LINKER_DATABASE_URL|APP_DB_PASSWORD|LINKER_DB_PASSWORD|S3_ACCESS_KEY|S3_SECRET_KEY|APP_MASTER_KEK|BACKUP_ENCRYPTION_KEY|KEYCLOAK_ADMIN_PASSWORD)=' \
      "$APP_DIR/.env" | grep -q 'CHANGE_ME'; then
    rm -f "$APP_DIR/.env"
    echo "easysynq-provision: generated .env still held CHANGE_ME placeholders — removed; retry regenerates" >&2
    exit 1
  fi

  step "env: appliance overrides (LAN TLS via Caddy internal CA)"
  set_kv SITE_ADDRESS "https://${HOSTNAME_DEFAULT}"
  set_kv MINIO_SITE_ADDRESS "https://${HOSTNAME_DEFAULT}:9443"
  set_kv S3_PUBLIC_ENDPOINT "https://${HOSTNAME_DEFAULT}:9443"
  set_kv OIDC_ISSUER "https://${HOSTNAME_DEFAULT}/realms/easysynq"
  set_kv OIDC_JWKS_URL "http://keycloak:8080/realms/easysynq/protocol/openid-connect/certs"
  set_kv OIDC_DISCOVERY_URL "http://keycloak:8080/realms/easysynq/.well-known/openid-configuration"
  set_kv IMPORT_SOURCE_PATH "/srv/easysynq/import"
  # The sudo-less helpers (easysynq-status/--remint, easysynq-compose) read .env as the easysynq
  # user; install.sh leaves it root:root 0600. Group-read for easysynq adds no exposure — the
  # user is docker-group (root-equivalent) already.
  chown root:easysynq "$APP_DIR/.env"
  chmod 640 "$APP_DIR/.env"

  step "stack: build + start (first run pulls + builds images — the long part)"
  compose up -d --build

  step "stack: wait for /readyz"
  local ok=0
  for _ in $(seq 1 180); do
    if curl -fsk --resolve "${HOSTNAME_DEFAULT}:443:127.0.0.1" \
        "https://${HOSTNAME_DEFAULT}/readyz" >/dev/null 2>&1; then
      ok=1; break
    fi
    sleep 5
  done
  [ "$ok" -eq 1 ] || { log "readyz never went green"; exit 1; }

  # No `|| true`: a failed account creation must FAIL the provision (a READY banner with a
  # sign-in account that doesn't exist is worse than a visible retry). The helper itself is
  # idempotent (create-if-absent + set-password). kcadm receives the admin password on the
  # exec argv — accepted: it is inside the container's namespace on a single-admin VM.
  step "keycloak: create the initial sign-in account (qmsadmin, temporary password)"
  easysynq-create-user qmsadmin --temporary-password "EasySynQ-Setup-1"

  step "setup: mint the one-time bootstrap secret"
  local secret
  secret="$(compose exec -T api uv run python -m easysynq_api.cli.setup mint-bootstrap 2>/dev/null \
    | grep -E '^\s{4}\S+' | tr -d '[:space:]')"
  [ -n "$secret" ] || { log "could not mint the bootstrap secret"; exit 1; }

  step "hand-off: write the setup sheet"
  # Create with final perms BEFORE content lands — no 0644 window on a file holding secrets.
  install -m 600 -o easysynq -g easysynq /dev/null "$SETUP_FILE"
  cat >"$SETUP_FILE" <<EOF
EasySynQ appliance — first-run hand-off ($(date -u +%F))
=========================================================

1. From any workstation, open:   https://${HOSTNAME_DEFAULT}
   (mDNS name — Windows 10/11 resolve it natively. Cert warning is expected
   until the Caddy internal CA is distributed; see the runbook's GPO step.)

2. Sign in:  qmsadmin / EasySynQ-Setup-1   (you must set a new password)

3. The wizard asks for the one-time bootstrap secret:

    ${secret}

   (Single-use, 24h. Re-mint: easysynq-status --remint)

4. Then: org profile -> storage + WORM verify -> backup drill -> finalize.

Helpers on this VM:  easysynq-status | easysynq-mount-qms | easysynq-create-user | easysynq-reconfigure
QMS import share:    easysynq-mount-qms //server/share readonly-user
EOF
  touch "$STATE_DIR/provisioned"
  console "\n[EasySynQ] READY — open https://${HOSTNAME_DEFAULT}\n  Setup sheet (sign-in + bootstrap secret): login here as 'easysynq', then: cat ~/EASYSYNQ-SETUP.txt\n"
  log "provision complete"
}

case "${1:-}" in
  bootstrap) cmd_bootstrap ;;
  run) cmd_run ;;
  *) echo "usage: easysynq-provision {bootstrap|run}" >&2; exit 2 ;;
esac
