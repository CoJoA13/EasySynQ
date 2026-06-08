#!/usr/bin/env bash
# (Re)create the Keycloak `demo` dev user for local login (invoked by `just demo-user`).
# Keycloak has no volume, so its data (incl. this user) is wiped on `just down` / any keycloak
# recreate; the realm re-imports from realm-export.json. Idempotent; password is the documented dev
# credential. Lives in a script (not a justfile shebang recipe) so it runs identically on Linux/macOS
# and native Windows + Git Bash.
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
kc create users -r easysynq -s username=demo -s enabled=true 2>/dev/null || true
kc set-password -r easysynq --username demo --new-password "Demo-Password-1"
echo "demo / Demo-Password-1 ready - sign in at http://localhost"
