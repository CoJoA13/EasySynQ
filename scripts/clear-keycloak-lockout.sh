#!/usr/bin/env bash
# Clear a Keycloak brute-force lockout for one user.
#
#   ./scripts/clear-keycloak-lockout.sh <username>
#
# After repeated failed logins Keycloak temporarily disables the account and then rejects even the
# CORRECT password, showing only a generic "invalid username or password". The realm event log is
# the sole place it is legible, as error="user_temporarily_disabled". Without knowing that, the
# obvious next move is to reset a password that was never wrong — which does not help, because the
# lockout survives the reset.
set -euo pipefail
cd "$(dirname "$0")/.."

USERNAME="${1:-}"
[ -n "$USERNAME" ] || { echo "usage: ./scripts/clear-keycloak-lockout.sh <username>" >&2; exit 2; }
[ -f .env ] || { echo "clear-keycloak-lockout: no .env — run scripts/install.sh first" >&2; exit 1; }

# Match docker compose's .env parsing: strip an inline `# comment` and surrounding whitespace.
env_val() {
  grep -m1 "^$1=" .env | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//'
}

PROFILE="$(env_val EASYSYNQ_PROFILE)"; PROFILE="${PROFILE:-s}"
KC_ADMIN="$(env_val KEYCLOAK_ADMIN_USER)"; KC_ADMIN="${KC_ADMIN:-admin}"
KC_PW="$(env_val KEYCLOAK_ADMIN_PASSWORD)"
[ -n "$KC_PW" ] || { echo "clear-keycloak-lockout: KEYCLOAK_ADMIN_PASSWORD is empty in .env" >&2; exit 1; }

# `exec` against the running container (never `run`, which would recreate dependencies whose
# resolved config differs from this file set). MSYS_NO_PATHCONV=1 keeps the container path intact
# under Git Bash on native Windows; harmless elsewhere.
kc() {
  MSYS_NO_PATHCONV=1 docker compose --env-file .env \
    -f infra/compose/compose.yml -f "infra/compose/compose.${PROFILE}.yml" \
    exec -T keycloak /opt/keycloak/bin/kcadm.sh "$@" </dev/null
}

kc config credentials --server http://localhost:8080 --realm master \
  --user "$KC_ADMIN" --password "$KC_PW" >/dev/null
unset KC_PW

SUB="$(kc get users -r easysynq -q username="$USERNAME" --fields id --format csv --noquotes 2>/dev/null | tr -d '\r' | head -1)"
[ -n "$SUB" ] || { echo "clear-keycloak-lockout: user '$USERNAME' not found in the easysynq realm" >&2; exit 1; }

echo "user: $USERNAME"
echo "sub : $SUB"

echo "before:"
kc get "attack-detection/brute-force/users/$SUB" -r easysynq 2>/dev/null | sed 's/^/  /' || echo "  (no record)"

kc delete "attack-detection/brute-force/users/$SUB" -r easysynq 2>/dev/null && echo "cleared" || echo "nothing to clear"

echo "after:"
kc get "attack-detection/brute-force/users/$SUB" -r easysynq 2>/dev/null | sed 's/^/  /' || echo "  (no record — not locked)"
