#!/usr/bin/env bash
# (Re)create the Keycloak `demo` dev user for local login (invoked by `just demo-user`).
# Keycloak has no volume, so its data (incl. this user) is wiped on `just down` / any keycloak
# recreate; the realm re-imports from realm-export.json. Idempotent; password is the documented dev
# credential. Lives in a script (not a justfile shebang recipe) so it runs identically on Linux/macOS
# and native Windows + Git Bash.
set -euo pipefail
cd "$(dirname "$0")/.."

pw="$(grep -m1 '^KEYCLOAK_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
kc() { docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@" </dev/null; }
kc config credentials --server http://localhost:8080 --realm master --user admin --password "$pw" >/dev/null
kc create users -r easysynq -s username=demo -s enabled=true 2>/dev/null || true
kc set-password -r easysynq --username demo --new-password "Demo-Password-1"
echo "demo / Demo-Password-1 ready - sign in at http://localhost"
