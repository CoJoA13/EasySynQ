#!/usr/bin/env bash
# Dev fixture: create the SoD persona logins (priya/ken/mara) in Keycloak + seed their
# author/approver/releaser grants, so the full review->approve->release loop (S-web-5) is demoable
# (invoked by `just seed-personas`). Keycloak is ephemeral (wiped on `just down`), so re-run after a
# reset. Idempotent; password is the documented dev credential. Lives in a script (not a justfile
# shebang recipe) so it runs identically on Linux/macOS and native Windows + Git Bash.
set -euo pipefail
cd "$(dirname "$0")/.."

# Read the admin password the way docker compose parses .env: drop an inline `# comment` and any
# surrounding whitespace (a fresh `install.sh` .env leaves `KEYCLOAK_ADMIN_PASSWORD=CHANGE_ME  # …`,
# and a naive `cut -d=` would otherwise feed the comment into kcadm as part of the password).
pw="$(grep -m1 '^KEYCLOAK_ADMIN_PASSWORD=' .env | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//')"
# MSYS_NO_PATHCONV=1: on native Windows + Git Bash, MSYS rewrites the container path
# `/opt/keycloak/bin/kcadm.sh` into a host path (`C:/Program Files/Git/opt/…`) before docker sees it,
# so the exec fails with `exit 127`. Disabling path conversion for this call keeps it a container
# path; the flag is a harmless no-op on Linux/macOS.
kc() { MSYS_NO_PATHCONV=1 docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@" </dev/null; }
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
