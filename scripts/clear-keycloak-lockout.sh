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

# Read .env the way docker compose does: strip an inline `# comment` from an UNQUOTED value, but
# treat a quoted body as literal (Compose removes the quotes and keeps a `#` inside them).
env_val() {
  local v
  v="$(grep -m1 "^$1=" .env | cut -d= -f2-)"
  v="${v%$'\r'}"
  v="$(printf '%s' "$v" | sed -E 's/[[:space:]]+$//')"
  case "$v" in
    \"*\"*) v="${v#\"}"; v="${v%%\"*}" ;;                   # quoted: body literal (`#` included); a comment after the closing quote falls off
    \'*\'*) v="${v#\'}"; v="${v%%\'*}" ;;
    *) v="$(printf '%s' "$v" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')" ;;
  esac
  printf '%s' "$v" | sed -E 's/^[[:space:]]*//'
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

# ⚠ `-q username=X` is a CONTAINS match: querying `ann` also returns `joann`. Without `exact=true`
# this could clear — or report on — a different person's account. Parse JSON rather than CSV so the
# result is self-describing, and re-verify the username before touching account state.
# A failed lookup must not read as "user not found" — that misdiagnosis sends the operator
# chasing a typo while the real problem is an expired admin session, a 403/5xx, or availability.
if ! json="$(kc get users -r easysynq -q username="$USERNAME" -q exact=true --fields id,username 2>&1)"; then
  echo "clear-keycloak-lockout: user lookup failed (auth/API error — NOT 'user not found'):" >&2
  printf '  %s\n' "$json" >&2
  exit 1
fi
SUB="$(printf '%s' "$json"  | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'       | head -1)"
NAME="$(printf '%s' "$json" | sed -n 's/.*"username"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[ -n "$SUB" ] || { echo "clear-keycloak-lockout: user '$USERNAME' not found in the easysynq realm" >&2; exit 1; }
[ "$NAME" = "$USERNAME" ] || {
  echo "clear-keycloak-lockout: refusing to act — asked for '$USERNAME', Keycloak returned '$NAME'" >&2
  exit 1
}

echo "user: $USERNAME"
echo "sub : $SUB"

echo "before:"
kc get "attack-detection/brute-force/users/$SUB" -r easysynq 2>/dev/null | sed 's/^/  /' || echo "  (no record)"

# ⚠ Do NOT collapse this into `delete && echo cleared || echo nothing to clear`. Keycloak returns
# success whether or not a lockout record exists, so ANY non-zero result here is a real failure —
# an expired admin session, a 403, a 5xx, the service being down. Reporting those as "nothing to
# clear" and exiting 0 leaves the user locked while the operator believes it was remediated.
if ! out="$(kc delete "attack-detection/brute-force/users/$SUB" -r easysynq 2>&1)"; then
  echo "clear-keycloak-lockout: Keycloak refused the lockout deletion:" >&2
  printf '  %s\n' "$out" >&2
  exit 1
fi
echo "delete: accepted"

# Verify the absence rather than assume it — the only trustworthy "no-op" is an observed one.
# ⚠ This GET returns 200 with a (possibly zeroed) status map even when no record exists, so a
# non-zero exit is never "no record" — it is an API failure (expired admin session, 403, 5xx).
# Reporting that as "not locked" would exit 0 with the remediation unverified.
echo "after:"
if after="$(kc get "attack-detection/brute-force/users/$SUB" -r easysynq 2>&1)"; then
  printf '%s\n' "$after" | sed 's/^/  /'
  if printf '%s' "$after" | grep -qE '"numFailures"[[:space:]]*:[[:space:]]*[1-9]'; then
    echo "clear-keycloak-lockout: failures still recorded after delete — investigate" >&2
    exit 1
  fi
  echo "not locked."
else
  echo "clear-keycloak-lockout: could not verify — the post-delete status read failed:" >&2
  printf '  %s\n' "$after" >&2
  exit 1
fi
