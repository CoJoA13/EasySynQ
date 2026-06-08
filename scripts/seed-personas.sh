#!/usr/bin/env bash
# Dev fixture: create the SoD persona logins (priya/ken/mara) in Keycloak + seed their
# author/approver/releaser grants, so the full review->approve->release loop (S-web-5) is demoable
# (invoked by `just seed-personas`). Keycloak is ephemeral (wiped on `just down`), so re-run after a
# reset. Idempotent; password is the documented dev credential. Lives in a script (not a justfile
# shebang recipe) so it runs identically on Linux/macOS and native Windows + Git Bash.
set -euo pipefail
cd "$(dirname "$0")/.."

pw="$(grep -m1 '^KEYCLOAK_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
kc() { docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@" </dev/null; }
kc config credentials --server http://localhost:8080 --realm master --user admin --password "$pw" >/dev/null

sub_for() {
  local u="$1" out id
  out="$(kc create users -r easysynq -s username="$u" -s enabled=true 2>&1 || true)"
  id="$(printf '%s' "$out" | grep -oE "'[0-9a-f-]{36}'" | tr -d "'" | head -1)"
  if [ -z "$id" ]; then
    id="$(kc get users -r easysynq -q username="$u" --fields id 2>/dev/null | grep -oE '[0-9a-f-]{36}' | head -1)"
  fi
  kc set-password -r easysynq --username "$u" --new-password "Demo-Password-1" >/dev/null 2>&1 || true
  printf '%s' "$id"
}

author="$(sub_for priya)"; approver="$(sub_for ken)"; releaser="$(sub_for mara)"
if [ -z "$author" ] || [ -z "$approver" ] || [ -z "$releaser" ]; then
  echo "failed to resolve a Keycloak subject (author=$author approver=$approver releaser=$releaser)" >&2; exit 1
fi
./scripts/easysynq seed-personas --author "$author" --approver "$approver" --releaser "$releaser"
echo "personas ready: priya(author) / ken(approver) / mara(releaser) - all password Demo-Password-1"
